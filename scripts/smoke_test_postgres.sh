#!/usr/bin/env bash
set -euo pipefail

# Smoke test for Cerebro E2E research flow against docker-compose stack.
#
# Assumes `docker compose up` is already running. Registers a real user,
# logs in to get JWT tokens, then exercises the same 9 research-project
# endpoints as smoke_test.sh. Prints a pass/fail summary.
#
# Usage:  ./scripts/smoke_test_postgres.sh
# Exit:   0 = all pass, 1 = any fail

PORT=8000
BASE="http://localhost:${PORT}"
PASS=0
FAIL=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

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

echo -e "${YELLOW}=== Cerebro Smoke Test (PostgreSQL) ===${NC}"
echo ""

# ------------------------------------------------------------------
# 0. Check that docker-compose stack is running
# ------------------------------------------------------------------
echo "Checking docker-compose stack..."
if ! docker compose ps | grep -q research-api; then
    echo -e "  ${RED}ERROR: docker-compose stack not running${NC}"
    echo "  Run 'docker compose up -d' first"
    exit 1
fi

# Wait for /health (up to 30s)
echo "Waiting for /health..."
for i in $(seq 1 30); do
    if python3 -c "import httpx; r=httpx.get('${BASE}/health', timeout=2); exit(0 if r.status_code==200 else 1)" 2>/dev/null; then
        echo -e "  ${GREEN}Server ready${NC} (${i}s)"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo -e "  ${RED}Server not responding within 30s${NC}"
        exit 1
    fi
    sleep 1
done

echo ""
echo -e "${YELLOW}--- Auth Flow ---${NC}"

# ------------------------------------------------------------------
# 1. Register user
# ------------------------------------------------------------------
TIMESTAMP=$(date +%s)
EMAIL="smoke${TIMESTAMP}@example.com"
USERNAME="smoke${TIMESTAMP}"
PASSWORD="MyV3ryStr0ng!P@ssw0rd"

RESULT=$(python3 -c "
import httpx
import sys
payload = {
    'email': '${EMAIL}',
    'username': '${USERNAME}',
    'password': '${PASSWORD}',
    'confirm_password': '${PASSWORD}',
    'accept_terms': True
}
r = httpx.post('${BASE}/api/v1/auth/register', json=payload, timeout=30)
print(r.status_code)
if r.status_code == 201:
    print(r.json()['user']['id'])
    print(r.json()['tokens']['access_token'])
else:
    print('NONE')
    print('NONE')
    print(r.text, file=sys.stderr)
")
STATUS=$(echo "$RESULT" | head -1)
USER_ID=$(echo "$RESULT" | sed -n 2p)
TOKEN=$(echo "$RESULT" | tail -1)
check "POST /api/v1/auth/register" 201 "$STATUS"

if [ "$TOKEN" = "NONE" ]; then
    echo -e "  ${RED}Cannot continue without auth token${NC}"
    FAIL=$((FAIL + 8))
    echo ""
    echo -e "${YELLOW}=== Results ===${NC}"
    TOTAL=$((PASS + FAIL))
    echo -e "  Total:  ${TOTAL}"
    echo -e "  ${GREEN}Passed: ${PASS}${NC}"
    echo -e "  ${RED}Failed: ${FAIL}${NC}"
    echo ""
    echo -e "${RED}SMOKE TEST FAILED${NC}"
    exit 1
fi

# ------------------------------------------------------------------
# 2. Login (to verify login endpoint works)
# ------------------------------------------------------------------
RESULT=$(python3 -c "
import httpx
import sys
payload = {
    'email': '${EMAIL}',
    'password': '${PASSWORD}'
}
r = httpx.post('${BASE}/api/v1/auth/login', json=payload, timeout=30)
print(r.status_code)
if r.status_code == 200:
    print(r.json()['tokens']['access_token'])
else:
    print('NONE')
    print(r.text, file=sys.stderr)
")
STATUS=$(echo "$RESULT" | head -1)
LOGIN_TOKEN=$(echo "$RESULT" | tail -1)
check "POST /api/v1/auth/login" 200 "$STATUS"

# Use the login token for subsequent requests
if [ "$LOGIN_TOKEN" != "NONE" ]; then
    TOKEN="$LOGIN_TOKEN"
fi

AUTH_HEADER="Authorization: Bearer ${TOKEN}"

echo ""
echo -e "${YELLOW}--- Endpoint Tests ---${NC}"

# ------------------------------------------------------------------
# 3. Health check
# ------------------------------------------------------------------
STATUS=$(python3 -c "import httpx; print(httpx.get('${BASE}/health', timeout=10).status_code)")
check "GET /health" 200 "$STATUS"

# ------------------------------------------------------------------
# 4. Create research project
# ------------------------------------------------------------------
RESULT=$(python3 -c "
import httpx
import sys
payload = {
    'title': 'Smoke Test Project',
    'query': {
        'text': 'What are the current approaches to AI alignment and safety?',
        'domains': ['AI', 'Ethics', 'Computer Science']
    },
    'user_id': '${USER_ID}'
}
headers = {'${AUTH_HEADER%%: *}': '${AUTH_HEADER#*: }'}
r = httpx.post('${BASE}/api/v1/research/projects', json=payload, headers=headers, timeout=30)
print(r.status_code)
if r.status_code == 201:
    print(r.json()['id'])
else:
    print('NONE')
    print(r.text, file=sys.stderr)
")
STATUS=$(echo "$RESULT" | head -1)
PROJECT_ID=$(echo "$RESULT" | tail -1)
check "POST /api/v1/research/projects" 201 "$STATUS"

if [ "$PROJECT_ID" = "NONE" ]; then
    echo -e "  ${RED}Cannot continue without project ID${NC}"
    FAIL=$((FAIL + 5))
else
    # --------------------------------------------------------------
    # 5. Get project
    # --------------------------------------------------------------
    STATUS=$(python3 -c "import httpx; print(httpx.get('${BASE}/api/v1/research/projects/${PROJECT_ID}', headers={'Authorization': 'Bearer ${TOKEN}'}, timeout=10).status_code)")
    check "GET /api/v1/research/projects/{id}" 200 "$STATUS"

    # --------------------------------------------------------------
    # 6. Get progress
    # --------------------------------------------------------------
    STATUS=$(python3 -c "import httpx; print(httpx.get('${BASE}/api/v1/research/projects/${PROJECT_ID}/progress', headers={'Authorization': 'Bearer ${TOKEN}'}, timeout=10).status_code)")
    check "GET /api/v1/research/projects/{id}/progress" 200 "$STATUS"

    # --------------------------------------------------------------
    # 7. List projects
    # --------------------------------------------------------------
    STATUS=$(python3 -c "import httpx; print(httpx.get('${BASE}/api/v1/research/projects', headers={'Authorization': 'Bearer ${TOKEN}'}, timeout=10).status_code)")
    check "GET /api/v1/research/projects" 200 "$STATUS"

    # --------------------------------------------------------------
    # 8. Get results (expect 404 — no results yet)
    # --------------------------------------------------------------
    STATUS=$(python3 -c "import httpx; print(httpx.get('${BASE}/api/v1/research/projects/${PROJECT_ID}/results', headers={'Authorization': 'Bearer ${TOKEN}'}, timeout=10).status_code)")
    check "GET /api/v1/research/projects/{id}/results (expect 404)" 404 "$STATUS"

    # --------------------------------------------------------------
    # 9. Nonexistent project (expect 404)
    # --------------------------------------------------------------
    STATUS=$(python3 -c "import httpx; print(httpx.get('${BASE}/api/v1/research/projects/00000000-0000-0000-0000-000000000000', headers={'Authorization': 'Bearer ${TOKEN}'}, timeout=10).status_code)")
    check "GET /api/v1/research/projects/{fake} (expect 404)" 404 "$STATUS"

    # --------------------------------------------------------------
    # 10. Cancel the smoke project
    # --------------------------------------------------------------
    STATUS=$(python3 -c "import httpx; print(httpx.post('${BASE}/api/v1/research/projects/${PROJECT_ID}/cancel', headers={'Authorization': 'Bearer ${TOKEN}'}, timeout=10).status_code)")
    check "POST /api/v1/research/projects/{id}/cancel" 204 "$STATUS"
fi

# ------------------------------------------------------------------
# 11. Intelligent query (public)
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
