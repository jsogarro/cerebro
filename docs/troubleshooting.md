# Troubleshooting Guide

This guide helps you diagnose and resolve common issues with **Cerebro**, the multi-agent
LLM research platform (current focus: financial research on US equities).

> **Naming note.** The product is **Cerebro**, but the deployment artifacts still carry the
> pre-rebrand **`research-platform`** identity: the FastAPI app is titled "Research Platform API",
> the database is `research_db`, the CLI is `research-cli`, and the API container is `research-api`.
> Those infra names are intentional and referenced verbatim below.

## Table of Contents
- [Quick Diagnostics](#quick-diagnostics)
- [Common Issues](#common-issues)
  - [Connection Issues](#connection-issues)
  - [Authentication Errors](#authentication-errors)
  - [Database Problems](#database-problems)
  - [Agent Failures](#agent-failures)
  - [Execution Issues](#execution-issues)
  - [Performance Problems](#performance-problems)
- [Service-Specific Issues](#service-specific-issues)
- [Debug Logging](#debug-logging)
- [Health Checks](#health-checks)
- [Recovery Procedures](#recovery-procedures)
- [Getting Help](#getting-help)

## Quick Diagnostics

Run these commands to quickly diagnose system health:

```bash
# Check overall system health
research-cli health

# Check API connectivity
curl -v http://localhost:8000/health

# Check service status
docker-compose ps

# View recent logs
docker-compose logs --tail=50 api

# Check database connectivity
docker-compose exec postgres pg_isready

# Check Redis connectivity
docker-compose exec redis redis-cli ping
```

The dev `docker-compose.yml` defines these services: `api`, `mcp-server`, `masr-router`,
`postgres`, `redis`, `nginx`, `web`, plus the `dev-tools` profile services `pgadmin` and
`redis-commander`. There is **no worker service** — execution runs in-process inside the `api`
container via the `DirectExecutionService` (Temporal has been removed).

## Common Issues

### Connection Issues

#### Problem: "Connection refused" when using CLI
**Symptoms:**
```
Error: Connection refused to http://localhost:8000
```

**Solutions:**
1. Verify API is running:
   ```bash
   docker-compose ps api
   # Should show "Up" status
   ```

2. Check API logs for startup errors:
   ```bash
   docker-compose logs api | grep ERROR
   ```

3. Verify port binding:
   ```bash
   netstat -an | grep 8000
   # or
   lsof -i :8000
   ```

4. Check firewall settings:
   ```bash
   # macOS
   sudo pfctl -s rules | grep 8000

   # Linux
   sudo iptables -L -n | grep 8000
   ```

5. Try alternative connection:
   ```bash
   # Use Docker network IP (container is named research-api)
   docker inspect research-api | grep IPAddress
   research-cli --api-url http://<container-ip>:8000 health
   ```

#### Problem: WebSocket connection fails
**Symptoms:**
```
WebSocket connection to 'ws://localhost:8000/ws' failed
```

**Solutions:**
1. Check the WebSocket endpoint. The live WS routes are `/ws`, `/ws/projects/{project_id}`,
   `/ws/cli/{project_id}`, and `GET /ws/health`, plus the supervisor and TalkHier WS routes
   under `/api/v1/supervisors/*` and `/api/v1/talkhier/*`:
   ```bash
   curl -i -N -H "Connection: Upgrade" \
        -H "Upgrade: websocket" \
        -H "Sec-WebSocket-Version: 13" \
        -H "Sec-WebSocket-Key: test" \
        http://localhost:8000/ws
   ```

2. Check plain WS liveness:
   ```bash
   curl http://localhost:8000/ws/health
   ```

3. Verify CORS settings in the API:
   ```bash
   grep -r "allow_origins" src/api/main.py
   ```

4. In `development` (`ENVIRONMENT=development`) anonymous WS connections are allowed. In other
   environments the connection must present a valid JWT (RS256).

### Authentication Errors

Cerebro uses **per-endpoint JWT authentication** (RS256), applied via FastAPI `Depends(...)` on
the endpoints that need it (the `/api/v1/auth/*` routes and `/api/v1/research`, which resolves a
tenant context through `get_tenant_context` -> `get_current_token`). The users GDPR endpoint and
the `/api/v1/reports` routes carry **no** auth dependency and are effectively unauthenticated. The
`AuthMiddleware` is a **no-op** — it sets
`request.state.user = None` and validates nothing. As a result the primary
**`/api/v1/query/*`, `/api/v1/agents/*`, and `/api/v1/masr/*` routes are effectively
unauthenticated**; a 401 from those routes almost always means something other than a missing token.

Tokens are minted by `POST /api/v1/auth/login` (15-minute access token, 7-day refresh token). The
RS256 keys live at `/secrets/jwt_private.pem` and `/secrets/jwt_public.pem`; passwords are bcrypt
(12 rounds), `PASSWORD_MIN_LENGTH=12`.

#### Problem: "401 Unauthorized" on an authenticated endpoint
**Symptoms:**
```
Error: Authentication failed - 401 Unauthorized
```

**Solutions:**
1. Obtain a fresh access token:
   ```bash
   curl -X POST http://localhost:8000/api/v1/auth/login \
     -H "Content-Type: application/json" \
     -d '{"email": "<user-email>", "password": "<password>"}'
   ```

2. Call the endpoint with the bearer token:
   ```bash
   curl -H "Authorization: Bearer <access-token>" \
     http://localhost:8000/api/v1/auth/me
   ```

3. If the token is rejected, verify the JWT public/private key pair is mounted and readable:
   ```bash
   docker-compose exec api ls -l /secrets/jwt_private.pem /secrets/jwt_public.pem
   ```

4. For the CLI, set the token in `~/.research-cli.env` (dotenv keys `RESEARCH_API_URL`,
   `RESEARCH_AUTH_TOKEN`) or export it:
   ```bash
   export RESEARCH_AUTH_TOKEN="<access-token>"
   ```

#### Problem: JWT token expired
**Symptoms:**
```
Error: Token has expired
```

**Solutions:**
1. Exchange the refresh token for a new access token:
   ```bash
   curl -X POST http://localhost:8000/api/v1/auth/refresh \
     -H "Content-Type: application/json" \
     -d '{"refresh_token": "<refresh-token>"}'
   ```

2. If the refresh token has also expired (7 days), log in again with `POST /api/v1/auth/login`.

### Database Problems

#### Problem: Database connection pool exhausted
**Symptoms:**
```
sqlalchemy.exc.TimeoutError: QueuePool limit of size ... overflow ... reached
```

**Solutions:**
1. Check for connection leaks:
   ```bash
   docker-compose exec postgres psql -U research -d research_db -c \
     "SELECT pid, usename, application_name, state \
      FROM pg_stat_activity WHERE datname='research_db';"
   ```

2. Kill idle connections:
   ```bash
   docker-compose exec postgres psql -U research -d research_db -c \
     "SELECT pg_terminate_backend(pid) \
      FROM pg_stat_activity \
      WHERE state = 'idle' AND state_change < now() - interval '10 minutes';"
   ```

3. The async engine (`create_async_engine` / `async_sessionmaker`) is constructed from
   `DATABASE_URL` (default `postgresql+asyncpg://research:research123@localhost:5432/research_db`).
   Reduce concurrent load or restart the API container if the pool stays saturated.

#### Problem: Migration failures
**Symptoms:**
```
alembic.util.exc.CommandError: Can't locate revision identified by 'xxx'
```

**Solutions:**
1. Check current migration status:
   ```bash
   docker-compose exec api alembic current
   ```

2. Reset to a specific revision:
   ```bash
   docker-compose exec api alembic downgrade <revision>
   docker-compose exec api alembic upgrade head
   ```

3. Force migration table rebuild:
   ```bash
   docker-compose exec postgres psql -U research -d research_db \
     -c "DROP TABLE IF EXISTS alembic_version;"
   docker-compose exec api alembic stamp head
   ```

### Agent Failures

Agents are LLM-reasoning workers (prompt-driven) built on `LLMWorkerAgentBase`. By default the
runtime is **Gemini-only** (`GEMINI_DEFAULT_MODEL=gemini-pro`). OpenRouter multi-provider routing
(DeepSeek for simple tiers, Claude Sonnet for complex tiers) is flag-gated **off** and requires
both `MULTI_PROVIDER_ROUTING_ENABLED=True` and `OPENROUTER_API_KEY` to be set.

> Confidence scores reported by agents are hardcoded heuristics (0.85 on success, 0.3 on empty
> output), not real quality signals — don't treat them as a health metric. The fast path emits a
> fixed `quality_score` of 0.8 rather than a confidence score, and it is equally hardcoded.

#### Problem: Agent produces empty or low-quality output
**Symptoms:**
```
Agent 'LiteratureReviewAgent' returned empty result
```

**Solutions:**
1. Inspect the execution status and results (see [Execution Issues](#execution-issues)):
   ```bash
   curl http://localhost:8000/api/v1/query/execution/<execution-id>/status
   curl http://localhost:8000/api/v1/query/execution/<execution-id>/results
   ```

2. Check bypass-agent status/health for a specific agent type:
   ```bash
   research-cli agents status
   # or directly:
   curl http://localhost:8000/api/v1/agents/literature-review/health
   ```

3. Check the API logs for the underlying LLM error:
   ```bash
   docker-compose logs api | grep -i "gemini\|llm\|agent"
   ```

#### Problem: Gemini API errors
**Symptoms:**
```
google.api_core.exceptions.ResourceExhausted: 429 Quota exceeded
```

**Solutions:**
1. Confirm the key is set:
   ```bash
   docker-compose exec api printenv GEMINI_API_KEY | sed 's/./*/g'
   ```

2. Cerebro applies a single global rate limiter (`MAX_REQUESTS_PER_MINUTE=100`,
   `ENABLE_RATE_LIMITING=True`) plus a Gemini-specific limiter (`src/services/gemini_limiter.py`).
   There are no per-endpoint or per-tier rate settings — reduce request volume or wait for the
   provider quota to reset.

3. Watch LLM call volume and cost via Prometheus metrics:
   ```bash
   curl -s http://localhost:8000/metrics | grep -E "llm_call_duration_seconds|llm_tokens_total|llm_cost_usd_total"
   ```

### Execution Issues

Query execution runs through the in-process **`DirectExecutionService`**
(`src/api/services/direct_execution_service.py`), which replaced Temporal. The request flow is:

```
Client -> FastAPI -> DirectExecutionService (asyncio background task)
       -> MASRouter -> MASRSupervisorBridge -> domain supervisors -> workers -> verification QA gate
```

> The immediate response from `POST /api/v1/query/research` contains **hardcoded placeholders**
> (`selected_agents=[]`, `estimated_cost=0.015`, `estimated_quality=0.85`, `confidence=0.85`,
> `routing_time_ms=50.0`). It is **not** the routing result. Real routing data and output only
> appear later via the execution status/results endpoints below.

#### Problem: Execution appears stuck at "running"
**Symptoms:**
```
Execution status: running for an unusually long time
```

**Solutions:**
1. Poll the execution status. The response carries the real `supervisor_type` and progress:
   ```bash
   curl http://localhost:8000/api/v1/query/execution/<execution-id>/status
   ```

2. Fetch results once the status reports `completed`:
   ```bash
   curl http://localhost:8000/api/v1/query/execution/<execution-id>/results
   ```

3. Check the API logs for the background task. Executions run as asyncio tasks inside the `api`
   container:
   ```bash
   docker-compose logs api | grep "<execution-id>"
   ```

4. If the app was restarted mid-flight, resume from the last checkpoint (see below).

> **Historical bug (FIXED).** A `@retry` decorator on the execution workflow previously re-ran the
> whole workflow up to 3× because a naive/aware `datetime` subtraction in the `finally` block
> raised inside the retry scope. This is fixed (timestamps now use `datetime.now(UTC)`); repeated
> re-runs of a single query are no longer expected.

#### Problem: "Execution capacity reached"
**Symptoms:**
```
RuntimeError: maximum concurrent executions reached
```

**Solutions:**
1. `DirectExecutionService` tracks live work in an in-memory `active_executions` dict guarded by a
   `max_concurrent_executions` cap; it raises when the cap is hit. Wait for in-flight executions to
   finish, or check what is currently active:
   ```bash
   curl http://localhost:8000/api/v1/agents/executions/active
   ```

2. Because `active_executions` is in-memory, restarting the `api` container clears it (and drops
   in-flight work) — resume any interrupted execution from its checkpoint afterward.

#### Problem: Resuming an interrupted execution
**Solutions:**
1. Execution progress is checkpointed to the `workflow_checkpoints` table (via
   `CheckpointRepository`) at the `masr_routing`, `supervisor_execution`, `completed`, and
   `fast_path_completed` phases.

2. Resume from the latest checkpoint:
   ```bash
   curl -X POST http://localhost:8000/api/v1/query/execution/<project-id>/resume
   ```

3. Verify the checkpoint exists before resuming:
   ```bash
   docker-compose exec postgres psql -U research -d research_db -c \
     "SELECT project_id, phase, created_at FROM workflow_checkpoints \
      WHERE project_id='<project-id>' ORDER BY created_at DESC LIMIT 5;"
   ```

### Performance Problems

Parallelism is **in-process**, not horizontal: multi-domain queries fan out under an
`asyncio.Semaphore`, and each domain supervisor coordinates its worker team internally
(hierarchical supervisors + LangGraph state graphs, one per supervisor). There is no worker fleet
to scale — tune concurrency and the LLM path instead.

#### Problem: Slow API response times
**Symptoms:**
```
API requests taking > 5 seconds
```

**Solutions:**
1. Check database query performance:
   ```bash
   docker-compose exec postgres psql -U research -d research_db -c \
     "SELECT query, mean_exec_time, calls \
      FROM pg_stat_statements \
      ORDER BY mean_exec_time DESC LIMIT 10;"
   ```

2. Most latency is LLM-bound. Inspect call duration and cost:
   ```bash
   curl -s http://localhost:8000/metrics | grep -E "llm_call_duration_seconds|llm_cost_usd_total"
   ```

3. Increase the API worker process count (production image runs `uvicorn ... --workers 4`):
   ```bash
   # In docker-compose.yml (api service)
   command: uvicorn src.api.main:app --workers 4
   ```

#### Problem: High memory usage
**Symptoms:**
```
Container using > 2GB RAM
```

**Solutions:**
1. Check memory usage:
   ```bash
   docker stats --no-stream
   ```

2. Limit container memory:
   ```yaml
   # In docker-compose.yml
   services:
     api:
       mem_limit: 2g
       memswap_limit: 2g
   ```

3. Clear Redis caches (idempotency + rate-limit state live in Redis):
   ```bash
   docker-compose exec redis redis-cli FLUSHALL
   ```

## Service-Specific Issues

### PostgreSQL Issues

Postgres runs from `postgres:16-alpine`.

#### Problem: "FATAL: too many connections"
**Solutions:**
```bash
# Increase max connections
docker-compose exec postgres psql -U postgres -c \
  "ALTER SYSTEM SET max_connections = 200;"
docker-compose restart postgres
```

#### Problem: Slow queries
**Solutions:**
```bash
# Enable query logging
docker-compose exec postgres psql -U research -d research_db -c \
  "ALTER SYSTEM SET log_min_duration_statement = 1000;"  # Log queries > 1s

# Analyze tables
docker-compose exec postgres psql -U research -d research_db -c \
  "ANALYZE;"
```

### Redis Issues

Redis runs from `redis:7-alpine`.

#### Problem: "OOM command not allowed"
**Solutions:**
```bash
# Check memory usage
docker-compose exec redis redis-cli INFO memory

# Set memory limit
docker-compose exec redis redis-cli CONFIG SET maxmemory 1gb
docker-compose exec redis redis-cli CONFIG SET maxmemory-policy allkeys-lru
```

### MASR Router Note

The dev compose includes a standalone `masr-router` container (port 9100), but the **verified query
path uses the in-process `MASRouter` Python object**, not the external service (`MASR_SERVICE_URL`
is not read anywhere in `src/`). Treat the standalone container as legacy; do not chase query-path
issues there.

## Debug Logging

### Enable Debug Mode

The single debug gate is the `DEBUG` setting (default `False`). Turning it on also enables the
`/docs` and `/redoc` Swagger pages. Log verbosity is controlled by `LOG_LEVEL`.

```bash
# In .env
DEBUG=true
LOG_LEVEL=DEBUG
```

A single `DEBUG` + `LOG_LEVEL` pair governs runtime verbosity, and logging is emitted through
structlog. Per-subsystem flags (`DEV_ENABLE_AI_BRAIN_DEBUG`, `DEV_ENABLE_MEMORY_DEBUG`,
`DEV_ENABLE_ROUTING_DEBUG`) are declared in `src/core/config.py` but are read nowhere in `src/`, so
they are currently inert and have no effect.

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f api

# Filter by level
docker-compose logs api | grep ERROR

# Save logs to file
docker-compose logs > debug.log 2>&1

# Structured (JSON) log parsing
docker-compose logs api | jq 'select(.level == "error")'
```

## Health Checks

### Comprehensive Health Check Script

```bash
#!/bin/bash
# save as check_health.sh

echo "=== System Health Check ==="

# API Health
echo -n "API: "
curl -s http://localhost:8000/health | jq -r '.status' || echo "DOWN"

# Database
echo -n "PostgreSQL: "
docker-compose exec -T postgres pg_isready > /dev/null 2>&1 && echo "UP" || echo "DOWN"

# Redis
echo -n "Redis: "
docker-compose exec -T redis redis-cli ping > /dev/null 2>&1 && echo "UP" || echo "DOWN"

# Disk Space
echo -n "Disk Space: "
df -h / | awk 'NR==2 {print $5 " used"}'

# Memory
echo -n "Memory: "
free -h | awk 'NR==2 {print $3 "/" $2 " used"}'

# Docker Resources
echo "=== Docker Resources ==="
docker system df
```

### Monitoring Endpoints

The app exposes three top-level health endpoints (`/health`, `/ready`, `/live`) plus Prometheus
metrics, alongside a WebSocket health check at `/ws/health` and per-agent endpoints
(`/api/v1/agents/{agent_type}/health` and `/api/v1/agents/health/summary`). There are no
`/api/v1/health/agents` or `/health/workflows` endpoints.

```bash
# Overall health
curl http://localhost:8000/health

# Kubernetes readiness probe
curl http://localhost:8000/ready

# Kubernetes liveness probe
curl http://localhost:8000/live

# Prometheus metrics (LLM duration, tokens, cost, cost-drift)
curl http://localhost:8000/metrics
```

For per-agent health via the bypass API:

```bash
curl http://localhost:8000/api/v1/agents/<agent-type>/health
curl http://localhost:8000/api/v1/agents/health/summary
```

## Recovery Procedures

### Full System Restart

```bash
#!/bin/bash
# Complete system restart procedure

echo "Stopping all services..."
docker-compose down

echo "Cleaning up volumes..."
docker volume prune -f

echo "Rebuilding images..."
docker-compose build --no-cache

echo "Starting services..."
docker-compose up -d

echo "Waiting for services to be ready..."
sleep 30

echo "Running migrations..."
docker-compose exec api alembic upgrade head

echo "Verifying health..."
research-cli health

echo "System restart complete!"
```

> Restarting the API clears the in-memory `active_executions` state. Resume any interrupted work
> from its `workflow_checkpoint` (see [Execution Issues](#execution-issues)).

### Database Recovery

```bash
# Backup current state
docker-compose exec postgres pg_dump -U research research_db > backup_$(date +%Y%m%d).sql

# Restore from backup
docker-compose exec -T postgres psql -U research research_db < backup_20240115.sql

# Verify integrity
docker-compose exec postgres psql -U research -d research_db -c \
  "SELECT COUNT(*) FROM research_projects;"
```

### Clear All Caches

```bash
# Redis cache (idempotency + rate-limit state)
docker-compose exec redis redis-cli FLUSHALL

# Python cache
find . -type d -name __pycache__ -exec rm -rf {} +
```

## Getting Help

### Collect Diagnostic Information

```bash
#!/bin/bash
# save as collect_diagnostics.sh

DIAG_DIR="diagnostics_$(date +%Y%m%d_%H%M%S)"
mkdir -p $DIAG_DIR

# System info
uname -a > $DIAG_DIR/system_info.txt
docker version >> $DIAG_DIR/system_info.txt

# Service status
docker-compose ps > $DIAG_DIR/service_status.txt

# Recent logs
docker-compose logs --tail=1000 > $DIAG_DIR/logs.txt 2>&1

# Configuration (sanitized)
grep -v -i "password\|secret\|api_key\|token" .env > $DIAG_DIR/config.txt

# Database status
docker-compose exec postgres psql -U research -d research_db \
  -c "SELECT version();" > $DIAG_DIR/db_info.txt 2>&1

# Health check
research-cli health > $DIAG_DIR/health.txt 2>&1

# Create archive
tar -czf $DIAG_DIR.tar.gz $DIAG_DIR
echo "Diagnostics collected in $DIAG_DIR.tar.gz"
```

### CLI Reference

The `research-cli` command tree (entrypoints `research-platform` and `research-cli`):

- Top level: `config` (`show` | `set` | `save`), `health`, `completion`
- `agents`: `query`, `route`, `estimate`, `execute`, `chain`, `status`
- `projects`: `create`, `get`, `list`, `progress`, `cancel`, `results`, `refine`

Configuration is read from `~/.research-cli.env` (dotenv keys `RESEARCH_API_URL`,
`RESEARCH_AUTH_TOKEN`). Global flags include `--api-url`, `--format` (table/json/yaml/csv),
`--verbose`, and `--no-color`.

### Before Reporting Issues

1. Check this troubleshooting guide
2. Search existing issues in the project tracker
3. Collect diagnostic information (script above)
4. Try the recovery procedures
5. Include:
   - Error messages
   - Steps to reproduce
   - System configuration (sanitized)
   - Diagnostic archive

## Common Error Codes

API errors are returned in a standard envelope whose `code` field is a string, mapped from the HTTP
status by `src/api/middleware/error_envelope.py` (individual routes may set more specific codes).
The base codes are:

| Code | HTTP status | Solution |
|------|-------------|----------|
| `BAD_REQUEST` | 400 | Check the request payload and parameters |
| `AUTHENTICATION_REQUIRED` | 401 | Verify JWT token or credentials |
| `FORBIDDEN` | 403 | Check user/tenant permissions |
| `NOT_FOUND` | 404 | Verify the resource ID exists |
| `CONFLICT` | 409 | Resolve the conflicting state and retry |
| `VALIDATION_ERROR` | 422 | Check input parameters against the schema |
| `RATE_LIMIT_EXCEEDED` | 429 | Wait for the rate-limit window to reset or reduce request volume |
| `INTERNAL_SERVER_ERROR` | 500 | Check server logs; retry or resume from checkpoint |

## Performance Optimization Tips

1. **Reduce LLM round-trips** — the fast path is a single LLM call that bypasses supervisors
2. **Use Redis caching** for idempotency and rate-limit state
3. **Respect the global rate limit** (`MAX_REQUESTS_PER_MINUTE=100`)
4. **Use async operations** — the entire request path is asyncio-based
5. **Monitor LLM cost and latency** via Prometheus `/metrics`
6. **Optimize database queries** with indexes
7. **Tune in-process concurrency** — multi-domain fan-out is bounded by an `asyncio.Semaphore`
8. **Enable compression** for API responses
9. **Implement circuit breakers** for external services (see `src/reliability/retry_strategies.py`)
