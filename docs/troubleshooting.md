# Troubleshooting

Commands use the development stack from `docker-compose.yml`; for the production stack add `-f docker-compose.production.yml`.

> **Naming.** The deployment artifacts carry the name `research-platform`: the FastAPI app is titled "Research Platform API", the Postgres database is `research_db`, the CLI entry point is `research-cli` and the Kubernetes objects are `research-api` and friends.

## Contents

- [Quick diagnostics](#quick-diagnostics)
- [Connection issues](#connection-issues)
- [Authentication errors](#authentication-errors)
- [Database problems](#database-problems)
- [Agent and model failures](#agent-and-model-failures)
- [Execution issues](#execution-issues)
- [Performance problems](#performance-problems)
- [Service-specific issues](#service-specific-issues)
- [Debug logging](#debug-logging)
- [Health checks](#health-checks)
- [Recovery procedures](#recovery-procedures)
- [Error codes](#error-codes)
- [Before reporting an issue](#before-reporting-an-issue)

## Quick diagnostics

```bash
# Overall health through the CLI
research-cli health

# API liveness (no token needed)
curl -v http://localhost:8000/health

# Service status and recent API logs
docker compose ps
docker compose logs --tail=50 api

# Dependencies
docker compose exec postgres pg_isready
docker compose exec redis redis-cli ping
```

`docker-compose.yml` defines `api`, `postgres`, `redis`, `nginx` and `web`, plus `pgadmin` and `redis-commander` under the profile `dev-tools`. There is no worker service: executions run inside the `api` container.

## Connection issues

### "Connection refused" from the CLI

1. Check that the API container is up:
   ```bash
   docker compose ps api
   docker compose logs api | grep -i error
   ```
2. Check that port 8000 is bound (`API_HOST_PORT` overrides the host port in `docker-compose.yml`):
   ```bash
   lsof -i :8000
   ```
3. Bypass port mapping by calling the container address:
   ```bash
   docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$(docker compose ps -q api)"
   research-cli --api-url http://<container-ip>:8000 health
   ```

The CLI reads `~/.research-cli.env` (keys `RESEARCH_API_URL` and `RESEARCH_AUTH_TOKEN`) and the same environment variables. See [CLI and web clients](reference/13-cli-and-web-clients.md).

### WebSocket connection fails

The WebSocket routes are `/ws`, `/ws/projects/{project_id}`, `/ws/cli/{project_id}` and `/ws/runs/{run_id}`, plus `GET /ws/health`. The supervisor and TalkHier (supervisor messaging) routers add their own WebSocket routes under `/api/v1/supervisors/` and `/api/v1/talkhier/`.

1. Check the unauthenticated probe: `curl http://localhost:8000/ws/health`.
2. Pass the access token as the `token` query parameter (`ws://localhost:8000/ws?token=<access-token>`). An `Authorization` header alone does not authenticate a WebSocket connection, and the route dependency `require_authenticated_websocket` rejects a connection without a token.
3. `DEV_ALLOW_ANONYMOUS_WEBSOCKETS` does not admit a tokenless connection. Every mounted route depends on `require_authenticated_websocket`, which passes `allow_anonymous=False`, so the step 2 check rejects the connection before the flag is read.
4. When the token is valid but the connection closes, the token can lack an `organization_id` claim, or the project can belong to another organization. See [authentication and tenancy](reference/04-authentication-and-tenancy.md).
5. Behind nginx, `docker/nginx/nginx.conf` proxies `/ws` with upgrade headers and a 86400-second read timeout and limits new connections to 5 per second per client address.

## Authentication errors

Authentication is enforced by `AuthMiddleware`. Every route except the ten entries in `PUBLIC_ROUTE_ALLOWLIST` needs `Authorization: Bearer <access-token>`; the list is in [HTTP API](reference/03-http-api.md#authentication-requirement). Access tokens last 15 minutes and refresh tokens 7 days (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, `JWT_REFRESH_TOKEN_EXPIRE_DAYS`).

### Status codes

| Status | Cause |
|---|---|
| 401 | The bearer token is missing, malformed, expired or revoked, or the route is not in the allowlist and no token was sent |
| 403 | The token has no `organization_id` claim on a tenant-scoped route (`require_organization_id` in `src/middleware/tenant_context.py`), or a caller deleted another user's data through `DELETE /api/v1/users/{user_id}/gdpr` without being that user or a superuser |
| 404 | The resource does not exist, or it belongs to another organization: tenant-scoped lookups report a foreign resource exactly like a missing one |
| 503 | The authentication service is unavailable, typically because Redis (token blacklist) cannot be reached |

Solutions:

1. Obtain a token:
   ```bash
   curl -X POST http://localhost:8000/api/v1/auth/login \
     -H "Content-Type: application/json" \
     -d '{"email": "<user-email>", "password": "<password>"}'
   ```
2. Call the endpoint with it:
   ```bash
   curl -H "Authorization: Bearer <access-token>" http://localhost:8000/api/v1/auth/me
   ```
3. When every token is rejected, check that the key pair is readable by the container. The paths are `JWT_PRIVATE_KEY_PATH` and `JWT_PUBLIC_KEY_PATH`, default `/secrets/jwt_private.pem` and `/secrets/jwt_public.pem`:
   ```bash
   docker compose exec api ls -l /secrets/
   ```
4. Exchange an expired access token with `POST /api/v1/auth/refresh` and body `{"refresh_token": "<refresh-token>"}`. A refresh token is single use, and it fails with "Refresh token not found or expired" after Redis loses its record (see [backup and recovery](backup-recovery.md)); log in again.

See [authentication and tenancy](reference/04-authentication-and-tenancy.md) for the full model.

## Database problems

### Connection pool exhausted

Symptom: `sqlalchemy.exc.TimeoutError: QueuePool limit of size ... reached`.

1. Inspect connections:
   ```bash
   docker compose exec postgres psql -U research -d research_db -c \
     "SELECT pid, state, application_name FROM pg_stat_activity WHERE datname='research_db';"
   ```
2. Each Uvicorn worker has its own pool of 20 connections plus 10 overflow, with a 30-second wait (`create_engine` in `src/models/db/session.py`; `init_db()` is called without arguments). Check that the total across workers and replicas fits Postgres `max_connections`. See [performance tuning](performance-tuning.md).

### Migration failures

The container entrypoint runs `alembic upgrade head` and refuses to start the application if it fails, so a migration error shows up as a container that exits at startup:

```bash
docker compose logs api | head -40
docker compose run --rm --entrypoint alembic api current
docker compose run --rm --entrypoint alembic api heads
docker compose run --rm --entrypoint alembic api history
```

`alembic current` shows the revision the database is at, `heads` the revisions in `alembic/versions/` and `history` the chain. More than one head means two branches of migrations need merging. After fixing the cause, `docker compose up -d api` runs `alembic upgrade head` again. Do not edit or drop the `alembic_version` table by hand.

## Agent and model failures

### Empty or poor agent output

1. Read the execution status and results (see [Execution issues](#execution-issues)).
2. Check an agent's health:
   ```bash
   research-cli agents status
   curl -H "Authorization: Bearer <access-token>" http://localhost:8000/api/v1/agents/health/summary
   curl -H "Authorization: Bearer <access-token>" http://localhost:8000/api/v1/agents/<agent-type>/health
   ```
3. Search the API logs for the underlying model error:
   ```bash
   docker compose logs api | grep -i "gemini\|llm\|agent"
   ```

### Gemini API errors

Symptom: `ResourceExhausted: 429 Quota exceeded`.

1. Confirm the key is set without printing it: `docker compose exec api sh -c 'test -n "$GEMINI_API_KEY" && echo set'`.
2. Two limiters apply: the HTTP rate limiter (`MAX_REQUESTS_PER_MINUTE`, default 100, on requests to the API) and the Gemini token bucket (10 calls per 60 seconds and 2 concurrent per service instance by default). See [performance tuning](performance-tuning.md). Reduce request volume or wait for the provider quota to reset.
3. Watch model volume and cost with the `llm_*` series (the token is required). These series cover only the provider package; calls made through `GeminiService`, the default client, are not counted ([LLM providers](reference/10-llm-providers.md)):
   ```bash
   curl -s -H "Authorization: Bearer <access-token>" http://localhost:8000/metrics | grep -E "llm_call_duration_seconds|llm_tokens_total|llm_cost_usd_total"
   ```

Provider selection is described in [LLM providers](reference/10-llm-providers.md).

## Execution issues

Query execution runs in-process (`DirectExecutionService`) behind `POST /api/v1/query/...`. Read an execution's state with:

```bash
curl -H "Authorization: Bearer <access-token>" http://localhost:8000/api/v1/query/execution/<execution-id>/status
curl -H "Authorization: Bearer <access-token>" http://localhost:8000/api/v1/query/execution/<execution-id>/results
```

An execution that belongs to another organization is reported as not found. See [research execution](reference/05-research-execution.md) and [durable runs and events](reference/08-durable-runs-and-events.md).

### Execution seems stuck

1. Poll the status endpoint and read `status`, `progress_percentage` and `current_phase`.
2. Search the logs for the id: `docker compose logs api | grep "<execution-id>"`.
3. If the API restarted mid-run, `POST /api/v1/query/execution/<project-id>/resume` rebuilds the execution record from the latest recoverable checkpoint and registers it so its status can be read. It does not start any work ([research execution](reference/05-research-execution.md)).

### "Maximum concurrent executions reached"

`DirectExecutionService` raises `RuntimeError` when 50 executions are `pending` or `running` in one API process (`max_concurrent_executions`; finished runs are retained for reading but do not count). `GET /api/v1/agents/executions/active` does not list these executions: it reports the agent-execution snapshot of `AgentExecutionService`, which has its own cap of 100. No route lists the query executions held by `DirectExecutionService`. Wait for them to finish, or add replicas.

## Performance problems

Parallelism is in-process: workers fan out under an `asyncio.Semaphore` whose size is the execution budget's `max_parallel_tasks`, and the production image runs one Uvicorn process per container. Most request latency is model latency.

1. Inspect model latency and cost with the `llm_*` metrics (see above).
2. Compare the pool and concurrency limits in [performance tuning](performance-tuning.md).
3. Check container memory with `docker stats --no-stream`. The production compose file limits `api` to 2G.
4. Do not flush Redis to free memory: it holds token blacklists and refresh records (see [backup and recovery](backup-recovery.md)). To clear only rate-limit or idempotency state, delete that prefix:
   ```bash
   docker compose exec -T redis sh -c "redis-cli --scan --pattern 'rate_limit:*' | xargs -r redis-cli DEL"
   docker compose exec -T redis sh -c "redis-cli --scan --pattern 'idempotency:*' | xargs -r redis-cli DEL"
   ```
   Deleting `rate_limit:*` resets request counters. Deleting `idempotency:*` makes a retried request with an `Idempotency-Key` run again instead of replaying its stored response. On the production stack add `-a "$REDIS_PASSWORD"` to each `redis-cli`.

## Service-specific issues

### PostgreSQL

Postgres runs from `postgres:16-alpine` with user `research`.

- "FATAL: too many connections": raise `max_connections` (the setting needs a restart) or reduce the pool total:
  ```bash
  docker compose exec postgres psql -U research -d postgres -c "ALTER SYSTEM SET max_connections = 200;"
  docker compose restart postgres
  ```
- Slow queries: log statements longer than one second with
  ```bash
  docker compose exec postgres psql -U research -d postgres -c "ALTER SYSTEM SET log_min_duration_statement = 1000;"
  docker compose exec postgres psql -U research -d postgres -c "SELECT pg_reload_conf();"
  ```

### Redis

Redis runs from `redis:7-alpine`. For "OOM command not allowed", check usage with `docker compose exec redis redis-cli INFO memory` and raise the memory available to the container. An eviction policy that removes keys can remove token-revocation and refresh keys; see [backup and recovery](backup-recovery.md) before enabling one.

## Debug logging

`DEBUG` (default `false`) only enables `/docs` and `/redoc`, which still require a token (nginx serves its own `/docs`, so these pages are reachable only on the API port). `LOG_LEVEL` (default `INFO`) sets the minimum level of the API's structlog output; set `LOG_LEVEL=DEBUG` to see debug events ([configuration](reference/02-configuration.md)). Logs go to standard output through structlog.

`DEV_ENABLE_AI_BRAIN_DEBUG`, `DEV_ENABLE_MEMORY_DEBUG` and `DEV_ENABLE_ROUTING_DEBUG` are declared in `src/core/config.py` and read nowhere else, so setting them has no effect.

```bash
docker compose logs -f api
docker compose logs api | grep ERROR
```

## Health checks

`/health` checks nothing but the process; `/ready` returns 503 with the failing check marked `unavailable` when Postgres or Redis does not answer within 2 seconds. The routes and their bodies are in [HTTP API](reference/03-http-api.md#health-and-metrics), and the per-agent health routes need a token.

```bash
curl http://localhost:8000/ready
docker compose exec -T postgres pg_isready
docker compose exec -T redis redis-cli ping
```

See [deployment and monitoring](deployment-monitoring-guide.md).

## Recovery procedures

### Restart without data loss

```bash
docker compose down
docker compose up -d
docker compose logs --tail=50 api
```

The entrypoint applies migrations on start.

### Full reset

`docker compose down -v` stops this project's containers and deletes its named volumes, including the Postgres and Redis data. Run it only when you intend to lose that data, and take a backup first (see [backup and recovery](backup-recovery.md)).

```bash
docker compose down -v
docker compose build --no-cache
docker compose up -d
```

### Database and Redis recovery

Backup and restore commands, and what each Redis key family holds, are in [backup and recovery](backup-recovery.md).

## Error codes

Errors use one envelope whose `code` is derived from the HTTP status in `src/api/middleware/error_envelope.py`; individual routes can set more specific codes.

| Code | HTTP status | Typical action |
|---|---|---|
| `BAD_REQUEST` | 400 | Check the payload and parameters |
| `AUTHENTICATION_REQUIRED` | 401 | Send a valid bearer token |
| `FORBIDDEN` | 403 | Check the tenant claim and ownership |
| `NOT_FOUND` | 404 | Check the id and the organization |
| `CONFLICT` | 409 | Resolve the conflicting state and retry |
| `VALIDATION_ERROR` | 422 | Check the input against the schema |
| `RATE_LIMIT_EXCEEDED` | 429 | Wait for `Retry-After` seconds |
| `INTERNAL_SERVER_ERROR` | 500 | Read the server logs |

A request with an `Idempotency-Key` header to a `/api/v1/query` path that is still being processed returns 409 with `IDEMPOTENCY_KEY_IN_PROGRESS`. The full API surface is in [HTTP API](reference/03-http-api.md).

## Before reporting an issue

Collect:

- The error message and the request that produced it.
- `docker compose ps` and `docker compose logs --tail=500 api`.
- The Alembic revision (`alembic current`) and `research-cli health` output.
- Your configuration with passwords, keys and tokens removed.
