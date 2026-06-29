#!/usr/bin/env bash
set -euo pipefail

# Smoke test for Cerebro E2E research flow.
#
# Boots the app against a SQLite DB and a persistent RSA keypair, mints
# a JWT signed with that keypair, then exercises the research-project
# endpoint sequence and a /query/research call. Prints a pass/fail summary.
#
# Usage:  ./scripts/smoke_test.sh
# Exit:   0 = all pass, 1 = any fail

SESSION="cerebro-smoke"
PORT=8099
BASE="http://localhost:${PORT}"
PASS=0
FAIL=0
DB_FILE="./cerebro_smoke_test.db"
KEYS_DIR="/tmp/cerebro_smoke_keys"
PRIV_KEY="${KEYS_DIR}/jwt_private.pem"
PUB_KEY="${KEYS_DIR}/jwt_public.pem"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

cleanup() {
    tmux kill-session -t "$SESSION" 2>/dev/null || true
    rm -f "$DB_FILE"
    rm -rf "$KEYS_DIR"
}
trap cleanup EXIT

check() {
    local name="$1"
    local expected_status="$2"
    local actual_status="$3"

    if [ "$actual_status" -eq "$expected_status" ]; then
        echo -e "  ${GREEN}PASS${NC}  $name (${actual_status})"
        PASS=$((PASS + 1))
    else
        echo -e "  ${RED}FAIL${NC}  $name (expected ${expected_status}, got ${actual_status})"
        FAIL=$((FAIL + 1))
    fi
}

echo -e "${YELLOW}=== Cerebro Smoke Test ===${NC}"
echo ""

# ------------------------------------------------------------------
# 0. Provision RSA keys for JWT signing (shared by server + client)
# ------------------------------------------------------------------
mkdir -p "$KEYS_DIR"
.venv/bin/python3 - <<EOF
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

priv = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
with open("${PRIV_KEY}", "wb") as f:
    f.write(priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
with open("${PUB_KEY}", "wb") as f:
    f.write(priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
EOF
echo "  ${GREEN}RSA keypair provisioned${NC} at ${KEYS_DIR}"

# Mint a token. The server's tenant_context dependency reads sub +
# organization_id from the JWT and uses them as the project's owner /
# tenant. The user does not need to exist in the User table for the
# research-project endpoint set we exercise here.
SMOKE_USER_ID="00000000-0000-0000-0000-00000000beef"
SMOKE_ORG_ID="00000000-0000-0000-0000-00000000cafe"
TOKEN=$(.venv/bin/python3 - <<EOF
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from jose import jwt

with open("${PRIV_KEY}") as f:
    priv = f.read()

now = datetime.now(timezone.utc)
payload = {
    "sub": "${SMOKE_USER_ID}",
    "email": "smoke@example.com",
    "roles": ["researcher"],
    "permissions": [],
    "organization_id": "${SMOKE_ORG_ID}",
    "jti": str(uuid4()),
    "iat": int(now.timestamp()),
    "exp": int((now + timedelta(minutes=15)).timestamp()),
    "device_id": "smoke-test",
    "token_type": "access",
}
print(jwt.encode(payload, priv, algorithm="RS256"))
EOF
)
AUTH_HEADER="Authorization: Bearer ${TOKEN}"
echo "  ${GREEN}JWT minted${NC} (sub=${SMOKE_USER_ID})"

# ------------------------------------------------------------------
# 1. Boot the app
# ------------------------------------------------------------------
echo ""
echo "Booting app on port ${PORT}..."
rm -f "$DB_FILE"

tmux new-session -d -s "$SESSION" -x 220 -y 50
tmux send-keys -t "$SESSION" "cd $(pwd) && source .venv/bin/activate && \
  SECRET_KEY=cerebro-smoke-test-key-that-is-long-enough-32-chars \
  ENVIRONMENT=development \
  DATABASE_URL=sqlite+aiosqlite:///${DB_FILE} \
  JWT_PRIVATE_KEY_PATH=${PRIV_KEY} \
  JWT_PUBLIC_KEY_PATH=${PUB_KEY} \
  uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT} 2>&1" Enter

# Wait for /health (up to 30s)
echo "Waiting for /health..."
for i in $(seq 1 30); do
    if python3 -c "import httpx; r=httpx.get('${BASE}/health', timeout=2); exit(0 if r.status_code==200 else 1)" 2>/dev/null; then
        echo -e "  ${GREEN}Server ready${NC} (${i}s)"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo -e "  ${RED}Server failed to start within 30s${NC}"
        exit 1
    fi
    sleep 1
done

echo ""
echo -e "${YELLOW}--- Endpoint Tests ---${NC}"

# ------------------------------------------------------------------
# 2. Health check
# ------------------------------------------------------------------
STATUS=$(python3 -c "import httpx; print(httpx.get('${BASE}/health', timeout=10).status_code)")
check "GET /health" 200 "$STATUS"

# ------------------------------------------------------------------
# 3. Create research project
# ------------------------------------------------------------------
RESULT=$(python3 -c "
import httpx
payload = {
    'title': 'Smoke Test Project',
    'query': {
        'text': 'What are the current approaches to AI alignment and safety?',
        'domains': ['AI', 'Ethics', 'Computer Science']
    },
    'user_id': '${SMOKE_USER_ID}'
}
headers = {'${AUTH_HEADER%%: *}': '${AUTH_HEADER#*: }'}
r = httpx.post('${BASE}/api/v1/research/projects', json=payload, headers=headers, timeout=30)
print(r.status_code)
if r.status_code == 201:
    print(r.json()['id'])
else:
    print('NONE')
    import sys; print(r.text, file=sys.stderr)
")
STATUS=$(echo "$RESULT" | head -1)
PROJECT_ID=$(echo "$RESULT" | tail -1)
check "POST /api/v1/research/projects" 201 "$STATUS"

if [ "$PROJECT_ID" = "NONE" ]; then
    echo -e "  ${RED}Cannot continue without project ID${NC}"
    FAIL=$((FAIL + 5))
else
    # --------------------------------------------------------------
    # 4. Get project
    # --------------------------------------------------------------
    STATUS=$(python3 -c "import httpx; print(httpx.get('${BASE}/api/v1/research/projects/${PROJECT_ID}', headers={'Authorization': 'Bearer ${TOKEN}'}, timeout=10).status_code)")
    check "GET /api/v1/research/projects/{id}" 200 "$STATUS"

    # --------------------------------------------------------------
    # 5. Get progress
    # --------------------------------------------------------------
    STATUS=$(python3 -c "import httpx; print(httpx.get('${BASE}/api/v1/research/projects/${PROJECT_ID}/progress', headers={'Authorization': 'Bearer ${TOKEN}'}, timeout=10).status_code)")
    check "GET /api/v1/research/projects/{id}/progress" 200 "$STATUS"

    # --------------------------------------------------------------
    # 6. List projects
    # --------------------------------------------------------------
    STATUS=$(python3 -c "import httpx; print(httpx.get('${BASE}/api/v1/research/projects', headers={'Authorization': 'Bearer ${TOKEN}'}, timeout=10).status_code)")
    check "GET /api/v1/research/projects" 200 "$STATUS"

    # --------------------------------------------------------------
    # 7. Get results (expect 404 — no results yet)
    # --------------------------------------------------------------
    STATUS=$(python3 -c "import httpx; print(httpx.get('${BASE}/api/v1/research/projects/${PROJECT_ID}/results', headers={'Authorization': 'Bearer ${TOKEN}'}, timeout=10).status_code)")
    check "GET /api/v1/research/projects/{id}/results (expect 404)" 404 "$STATUS"

    # --------------------------------------------------------------
    # 8. Nonexistent project (expect 404)
    # --------------------------------------------------------------
    STATUS=$(python3 -c "import httpx; print(httpx.get('${BASE}/api/v1/research/projects/00000000-0000-0000-0000-000000000000', headers={'Authorization': 'Bearer ${TOKEN}'}, timeout=10).status_code)")
    check "GET /api/v1/research/projects/{fake} (expect 404)" 404 "$STATUS"

    # --------------------------------------------------------------
    # 8b. Cancel the smoke project
    # --------------------------------------------------------------
    STATUS=$(python3 -c "import httpx; print(httpx.post('${BASE}/api/v1/research/projects/${PROJECT_ID}/cancel', headers={'Authorization': 'Bearer ${TOKEN}'}, timeout=10).status_code)")
    check "POST /api/v1/research/projects/{id}/cancel" 204 "$STATUS"
fi

# ------------------------------------------------------------------
# 9. Intelligent query (public)
# ------------------------------------------------------------------
STATUS=$(python3 -c "
import httpx
r = httpx.post('${BASE}/api/v1/query/research',
    json={'query': 'AI safety research overview', 'domains': ['AI']}, timeout=30)
print(r.status_code)
")
check "POST /api/v1/query/research" 200 "$STATUS"

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
echo ""
echo -e "${YELLOW}=== Results ===${NC}"
TOTAL=$((PASS + FAIL))
echo -e "  Total:  ${TOTAL}"
echo -e "  ${GREEN}Passed: ${PASS}${NC}"
if [ "$FAIL" -gt 0 ]; then
    echo -e "  ${RED}Failed: ${FAIL}${NC}"
    echo ""
    echo -e "${RED}SMOKE TEST FAILED${NC}"
    exit 1
else
    echo -e "  Failed: 0"
    echo ""
    echo -e "${GREEN}SMOKE TEST PASSED${NC}"
    exit 0
fi
