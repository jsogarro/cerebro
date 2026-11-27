# Performance tuning

This guide lists the limits the code applies to request rate, concurrency, database connections and model calls, where each is set and how to observe the effect. It covers only values that code reads; it has no measured baselines or targets.

## Request handling

| Knob | Where set | Default | What it bounds | Source |
|---|---|---|---|---|
| `MAX_REQUESTS_PER_MINUTE` | Setting / environment variable | 100 | Requests per caller per 60-second window | `src/core/config.py` (`Settings`), `src/api/main.py` |
| `ENABLE_RATE_LIMITING` | Setting / environment variable | `true` | Turns the limiter on or off | `src/core/config.py`, `src/api/main.py` |
| Window length | `RateLimitMiddleware(window_seconds=...)` constructor argument; `src/api/main.py` does not pass it | 60 seconds | The fixed window | `src/api/middleware/rate_limiter.py` |
| Uvicorn worker processes | `--workers` in the image `CMD` | 1 | API processes per container | `Dockerfile` (`production` stage) |
| nginx request limits | `limit_req_zone` in the nginx file | 10 requests per second on `/api/`, 5 per second on `/ws`, per client address | Traffic through the reverse proxy | `docker/nginx/nginx.conf` |

`RateLimitMiddleware` counts requests in Redis with an in-memory fallback; the keys, response headers and exempt paths are in [HTTP API](reference/03-http-api.md#rate-limiting). The in-memory fallback is per process, so with several Uvicorn workers or replicas a Redis outage multiplies the effective limit by the process count.

The image runs Uvicorn with `--workers 1`: one process per container, made non-dumpable at import (`src/core/process_hardening.py`) so same-uid children cannot read its environment secrets, and scaled by replicas. The process has its own database pool, its own in-memory execution registry and its own routing cache. Docker Compose does not override the command in the production file, and the `GUNICORN_*` variables it sets are not read ([configuration](reference/02-configuration.md)).

## Execution concurrency

| Knob | Where set | Default | What it bounds | Source |
|---|---|---|---|---|
| `DirectExecutionService.max_concurrent_executions` | Attribute assigned in `__init__`; no setting or environment variable | 50 | Executions in `pending` or `running` state per API process; the 51st admission raises `RuntimeError("Maximum concurrent executions (50) reached")` | `src/api/services/direct_execution_service.py` |
| `AgentExecutionService.max_concurrent_executions` | Attribute assigned in `__init__` | 100 | Entries in its `active_executions` dict per process; a new execution raises `RuntimeError` at the cap | `src/api/services/agent_execution_service.py` |
| `ExecutionPlan.budget.max_parallel_tasks` | Field on the plan's budget, copied from the run's authority binding; must be at least 1 and at most the number of admitted workers | set per run | Concurrent worker tasks inside one execution (an `asyncio.Semaphore`) | `src/core/contracts/execution_plan.py`, `src/ai_brain/integration/execution_plan_topology.py` |
| `MixtureOfAgentsRequest.max_parallel` | Request body field of `POST /api/v1/agents/mixture` | 3 (allowed 1 to 5) | Agents run in parallel for one mixture request | `src/models/agent_api_models.py`, `src/api/services/agent_execution_service.py` |
| `MAX_PARALLEL_WORKERS` | Setting / environment variable | 5 | Upper bound on parallel workers the router allocates to a query | `src/core/config.py`, `src/ai_brain/router/factory.py` (`settings_to_router_config`) |
| `AGENTS_MAX_WORKERS_PER_SUPERVISOR` | Setting / environment variable | 10 | Upper bound on agents per query in the router (`max_agents`) | `src/core/config.py`, `src/ai_brain/router/factory.py` |
| `COST_MAX_PER_REQUEST` | Setting / environment variable | 0.10 (USD) | Copied to the router's `cost_threshold`, which nothing reads, so it limits nothing (see [MASR routing](reference/06-masr-routing.md)) | `src/core/config.py`, `src/ai_brain/router/factory.py` |

Both service counters are per process and live in memory. Each container admits up to 50 concurrent executions, and each Kubernetes replica adds its own. For how runs are admitted and stored, see [durable runs and events](reference/08-durable-runs-and-events.md); for routing allocation, see [MASR routing](reference/06-masr-routing.md).

## Database pool

`src/api/main.py` (`lifespan`) calls `init_db()` with no arguments, so `create_engine` in `src/models/db/session.py` uses its defaults. They are constructor arguments, not environment variables:

| Argument | Default | Meaning |
|---|---|---|
| `pool_size` | 20 | Connections kept open per process |
| `max_overflow` | 10 | Extra connections allowed beyond `pool_size` |
| `pool_timeout` | 30 | Seconds a request waits for a free connection |

The engine also sets `pool_pre_ping=True` and `pool_recycle=3600` and uses `AsyncAdaptedQueuePool` (SQLite URLs use `NullPool`). Because each Uvicorn worker builds its own engine, one container can hold up to 30 connections; Postgres `max_connections` has to cover that total across all replicas. To change the pool, pass arguments to `init_db(...)` in `lifespan`; there is no setting for them.

## Model calls

The application builds `GeminiService(api_key=settings.GEMINI_API_KEY)` (in `src/api/main.py`, `src/agents/llm_worker_base.py` and two services), so `GeminiConfig` takes its dataclass defaults:

| Setting (`GeminiConfig` field) | Default | Meaning |
|---|---|---|
| `rate_limit` / `rate_period` | 10 per 60 seconds | Token-bucket request budget for Gemini calls (`src/services/gemini_limiter.py`) |
| `max_concurrent` | 2 | Concurrent Gemini calls per service instance |
| `max_retries`, `retry_delay` | 3, 1.0 | Fields of the config; the call path retries with a `tenacity` decorator of 3 attempts and exponential waits between 5 and 30 seconds (`GeminiService._generate_content`) |
| `cache_enabled`, `cache_ttl` | `true`, 3600 seconds | Response caching, active only when a Redis `cache_client` is passed to `GeminiService`; the call sites above pass none, so it is inactive |

The environment variables `GEMINI_RATE_LIMIT`, `GEMINI_RATE_PERIOD`, `GEMINI_MAX_CONCURRENT`, `GEMINI_CACHE_ENABLED` and `GEMINI_CACHE_TTL` are read only by `GeminiConfig.from_env()`, which `GeminiService` calls only when it is created with neither an API key nor a config object. The call sites in this repository always pass an API key. To change the limits, construct the service with a `GeminiConfig`. For provider selection, see [LLM providers](reference/10-llm-providers.md).

## Routing cache

| Knob | Default | Source |
|---|---|---|
| `MASR_ENABLE_CACHING` | `true` | `src/core/config.py` |
| `MASR_CACHE_MAX_SIZE` | 1000 decisions | `src/core/config.py` |
| `MASR_CACHE_EVICTION_BATCH_SIZE` | 100 entries evicted when the cap is reached | `src/core/config.py` |

`RoutingCacheManager` (`src/ai_brain/router/routing_cache.py`) stores decisions in a plain dictionary inside the router, so the cache is per process: it is not shared across workers or replicas and is empty after a restart.

## Measuring

The `llm_*` Prometheus series on `/metrics` (`llm_call_duration_seconds`, `llm_tokens_total`, `llm_cost_usd_total`, `llm_request_cost_drift_ratio`, `llm_cost_drift_events_total`) show model latency, token use and cost for calls made through the provider package; `GeminiService` calls, the default client, are not recorded. `/metrics` requires a bearer token; see [security, audit and observability](reference/12-security-audit-observability.md) and [deployment and monitoring](deployment-monitoring-guide.md).

`make load-test` starts Locust with `tests/load/locustfile.py` against `http://localhost:8000`; see the [testing guide](testing-guide.md#load-tests).
