# End-to-End Verification Runbook

This document describes the working procedure to run Cerebro end-to-end, based on actual execution as of 2026-06-29.

## Current State Summary

**What works:**
- Backend API starts and serves requests (health, research projects, query endpoints)
- Postgres and Redis boot successfully via Docker
- Frontend dev server boots and serves the React app
- Smoke test (`scripts/smoke_test.sh`) exercises 9 API endpoints successfully against SQLite

**What does NOT work E2E:**
- MASR router service (broken Python imports, crashes on startup)
- Full docker-compose stack (depends on broken MASR service)
- Full auth flow via `/register` or `/login` (database initialization issues with Alembic)
- Frontend-to-backend integration (not verified - see gaps doc)
- WebSocket real-time updates (not tested)

## Prerequisites

- Python 3.11+
- Node.js 18+ and npm
- Docker and Docker Compose
- Working directory: `/Users/ogarro/work/apps/cerebro`
- Virtual environment at `.venv/` (created via `./scripts/setup-python-env.sh`)

## Environment Setup

1. **Ensure `.env` file exists with required secrets:**

```bash
cd /Users/ogarro/work/apps/cerebro
cat > .env << 'EOF'
GEMINI_API_KEY=<your-key-here>
GEMINI_API_URL=https://api.gemini.com
GEMINI_API_VERSION=v1

ENVIRONMENT=development
DEBUG=true
LOG_LEVEL=DEBUG
SECRET_KEY=<generate-with-openssl-rand-hex-32>
JWT_SECRET_KEY=<generate-with-openssl-rand-hex-32>
DATABASE_URL=postgresql+asyncpg://research:research123@localhost:5432/research_db
REDIS_URL=redis://localhost:6379/0
EOF
```

**Generate secrets:**
```bash
openssl rand -hex 32  # Use output for SECRET_KEY
openssl rand -hex 32  # Use output for JWT_SECRET_KEY
```

## Boot Stack (Minimal Working Path)

### Option A: Smoke Test (Fastest, SQLite-backed, No Docker)

```bash
cd /Users/ogarro/work/apps/cerebro
./scripts/smoke_test.sh
```

**What it does:**
- Boots API on port 8099 against SQLite
- Provisions RSA keypair and mints a JWT
- Tests 9 endpoints: health, create project, get project, progress, list, results (404), cancel, query/research
- Exits with code 0 if all pass, 1 if any fail

**Limitations:**
- No real auth (uses pre-minted JWT)
- SQLite, not Postgres
- No frontend
- No WebSocket
- No MASR router

### Option B: Backend + Postgres + Redis (Manual, Postgres-backed)

This is the closest to a production-like backend setup that currently works.

**1. Start infrastructure:**

```bash
docker run -d --name cerebro-postgres \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=research_db \
  -p 5432:5432 \
  postgres:16-alpine

docker run -d --name cerebro-redis \
  -p 6379:6379 \
  redis:7-alpine

# Wait for Postgres to be ready
sleep 10
```

**2. Initialize database (CURRENTLY BROKEN - see gaps doc):**

The Alembic migration system requires manual setup that is not documented. The smoke test bypasses this by using SQLite. For Postgres, you would need to:

```bash
# This DOES NOT WORK today without additional undocumented steps
.venv/bin/alembic upgrade head
```

**3. Start API:**

```bash
cd /Users/ogarro/work/apps/cerebro
tmux new-session -d -s cerebro-api \
  ".venv/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8000 2>&1 | tee /tmp/cerebro-api.log"

# Wait for startup
sleep 10
tail -20 /tmp/cerebro-api.log
```

**4. Verify health:**

```bash
curl -s http://localhost:8000/health | jq .
# Expected: {"status": "healthy", "service": "research-platform-api"}
```

**5. Access API docs:**

Open http://localhost:8000/docs in a browser to see the OpenAPI Swagger UI.

**Known startup warnings (non-fatal):**
- `sentence-transformers not available - using fallback embeddings`
- `qdrant-client not available - using fallback storage`
- `Database initialization failed: Pool class QueuePool cannot be used with asyncio engine`
- `WeasyPrint could not import some external libraries`

### Option C: Full docker-compose (BROKEN)

```bash
cd /Users/ogarro/work/apps/cerebro
docker-compose up -d
```

**This does NOT work today** because:
1. MASR router service crashes on startup (`ModuleNotFoundError: No module named 'config'`)
2. API service depends on MASR router health check
3. Docker compose does not pass `SECRET_KEY` or `JWT_SECRET_KEY` to the API container (hardcoded fallback is too short)

See gaps document for detailed failure analysis.

## Frontend

**Start dev server:**

```bash
cd /Users/ogarro/work/apps/cerebro/cerebro/web
npm install  # First time only
npm run dev
```

The app will be available at http://localhost:5173.

**Known status:**
- Dev server boots successfully
- App renders (confirmed via curl)
- Integration with backend API NOT VERIFIED (no documented test procedure)
- No documented login/registration flow for the frontend
- No documented procedure to connect frontend to a running backend

## Golden Path: Research Project Lifecycle

This is the sequence exercised by the smoke test. It works with a pre-minted JWT (bypassing `/register` and `/login`).

**1. Create JWT:**

The smoke test provisions an RSA keypair and mints a token. For manual testing, you would need to:
- Generate RSA keys (`openssl genrsa` / `openssl rsa`)
- Configure the API to trust your public key
- Mint a JWT with required claims (`sub`, `email`, `roles`, `organization_id`, `jti`, `iat`, `exp`, `device_id`)

**2. Create research project:**

```bash
curl -X POST http://localhost:8000/api/v1/research/projects \
  -H "Authorization: Bearer <your-jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test Research",
    "query": {
      "text": "What is quantum computing?",
      "domains": ["technology", "science"],
      "depth_level": "comprehensive"
    }
  }'
```

**3. Get project status:**

```bash
curl http://localhost:8000/api/v1/research/projects/{project_id} \
  -H "Authorization: Bearer <your-jwt>"
```

**4. Get progress:**

```bash
curl http://localhost:8000/api/v1/research/projects/{project_id}/progress \
  -H "Authorization: Bearer <your-jwt>"
```

**5. List all projects:**

```bash
curl http://localhost:8000/api/v1/research/projects \
  -H "Authorization: Bearer <your-jwt>"
```

**6. Get results:**

```bash
curl http://localhost:8000/api/v1/research/projects/{project_id}/results \
  -H "Authorization: Bearer <your-jwt>"
```

**7. Cancel project:**

```bash
curl -X POST http://localhost:8000/api/v1/research/projects/{project_id}/cancel \
  -H "Authorization: Bearer <your-jwt>"
```

**8. Direct query (synchronous):**

```bash
curl -X POST http://localhost:8000/api/v1/query/research \
  -H "Authorization: Bearer <your-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"text": "What is AI?", "max_depth": 2}'
```

## Teardown

**Stop services:**

```bash
# If using smoke test
tmux kill-session -t cerebro-smoke

# If using manual backend
tmux kill-session -t cerebro-api
docker rm -f cerebro-postgres cerebro-redis

# If using docker-compose
cd /Users/ogarro/work/apps/cerebro
docker-compose down -v

# Stop frontend
pkill -f "npm run dev"
pkill -f "vite"
```

## Common Failure Modes

### "Database not initialized"

**Symptom:** API returns 500, logs show `RuntimeError: Database not initialized. Call init_db() first.`

**Root cause:** Alembic migrations not run or database session factory not initialized.

**Workaround:** Use smoke test (SQLite) or manually fix Alembic setup (see gaps doc).

### "SECRET_KEY must be at least 32 characters"

**Symptom:** API crashes on startup with Pydantic validation error.

**Root cause:** `.env` file missing or docker-compose not passing SECRET_KEY to container.

**Fix:** Ensure `.env` has valid `SECRET_KEY` and `JWT_SECRET_KEY` (see Environment Setup above).

### "ModuleNotFoundError: No module named 'config'"

**Symptom:** MASR router crashes on startup.

**Root cause:** `src/reliability/connection_pools.py` imports a non-existent `config` module.

**Status:** This is a code bug. MASR service is broken. See gaps doc.

### "dependency failed to start: container masr-router is unhealthy"

**Symptom:** `docker-compose up` fails to start API service.

**Root cause:** MASR router crashes before health check passes, API depends on it.

**Workaround:** Remove MASR dependency from docker-compose.yml or start services individually (`--no-deps`).

### "role 'research' does not exist"

**Symptom:** Alembic migration fails.

**Root cause:** Postgres container started without creating the `research` role.

**Fix:** Create role manually (see Option B step 1 above for corrected container startup command).

## Next Steps

For a production-ready E2E experience, see `/Users/ogarro/work/apps/cerebro/ai/plans/2026-06-29-e2e-gaps.md` (or wherever the gaps plan is located).
