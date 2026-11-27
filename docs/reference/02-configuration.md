# Configuration

Cerebro reads configuration from process environment variables, optional dotenv files and defaults declared on the `Settings` class. A few components read their own variables outside `Settings`. This page separates the settings that production code reads from the fields that are declared and never read.

## Summary

| Feature | Status | Entry point | Source |
|---|---|---|---|
| Dotenv and environment loading | Active | import of `src.core.config` | `src/core/environment.py` (`load_environment`) |
| Application and HTTP settings | Active | `get_settings()` | `src/core/config.py` (`Settings`) |
| Authentication settings | Active | `get_settings()` | `src/core/config.py` (`Settings`) |
| Database and Redis settings | Active | `get_settings()` | `src/core/config.py` (`Settings`) |
| MASR settings | Active | `get_settings()` | `src/core/config.py` (`Settings`) |
| Adaptive routing settings | Off by default | `get_settings()` | `src/core/config.py` (`Settings`) |
| Memory-informed routing settings | Not wired | `get_settings()` | `src/core/config.py` (`Settings`) |
| Providers settings | Active | `get_settings()` | `src/core/config.py` (`Settings`) |
| MCP tool path settings | Active | `get_settings()` | `src/core/config.py` (`Settings`) |
| Evaluation run settings | Active (evaluation harness only) | `get_settings()` | `src/core/config.py` (`Settings`) |
| Constraint pinning settings | Off by default | `get_settings()` | `src/core/config.py` (`Settings`) |
| Tracing and telemetry settings | Off by default | `get_settings()` | `src/core/config.py` (`Settings`) |
| WebSocket development bypass settings | Off by default | `get_settings()` | `src/core/config.py` (`Settings`) |
| Declared-only `Settings` fields | Not wired | none | `src/core/config.py` (`Settings`) |
| Gemini client variables (`GEMINI_MODEL` and related) | Wired | `GeminiConfig.from_env` | `src/services/gemini_config.py` (`GeminiConfig.from_env`) |
| Report variables | Active | `create_report_settings()` | `src/services/report_config.py` (`ReportSettings`) |
| CLI variables (`RESEARCH_*`) | Active | `research-cli` | `src/cli/config.py` (`CLIConfig.from_env`) |
| Document encryption key (`DOCUMENT_ENCRYPTION_KEY`) | Active | `require_document_key` at startup, `document_cipher()` | `src/core/memory_encryption.py` (`document_cipher`), `src/services/document_ingestion.py` |
| Memory encryption key (`MEMORY_ENCRYPTION_KEY`) | Not wired | the memory tiers only | `src/core/memory_encryption.py` (`MemoryEncryption`) |
| Compose-level variables | Active | `docker compose` | `docker-compose.yml`, `docker-compose.production.yml` |

## How configuration loads

`src/core/environment.py` (`load_environment`) runs when `src.core.config` is imported. It also runs inside `GeminiConfig.from_env` and `ReportSettings.__init__`. It merges two dotenv files into the process environment:

1. Variables already present in the process environment are authoritative and are never overwritten.
2. `~/.env` (the home file) overrides values from the repository `.env`.
3. The repository `.env` (the project root) supplies the remaining values.
4. Fields with no value anywhere use the defaults declared on `Settings`.

Dotenv files are parsed as data, so `${VAR}` references are resolved against the process environment and the merged dotenv values only. A key written with an empty value (`KEY=`) is kept as an empty string. A key written without `=` is skipped. Setting `CEREBRO_DISABLE_DOTENV` to `1`, `true`, `yes` or `on` skips file loading entirely.

`Settings` is a `pydantic_settings.BaseSettings` subclass with `case_sensitive=True` and `extra="ignore"`. It declares no `env_file`, so the dotenv merge above is the only file loading. Unknown variables are ignored. Defaults are validated, so a validator error is raised at import even when the variable is unset. `get_settings()` returns the module-level `settings` instance built at import; there is no cache to clear.

List and mapping fields (`CORS_ORIGINS`, `TRUSTED_PROXIES`, `MASR_COMPLEXITY_WEIGHTS`, `MASR_COMPLEXITY_THRESHOLDS`, `OPENROUTER_TIER_MAPPING`, `ADAPTIVE_ROUTING_ALLOWED_EVALUATORS`) are parsed from JSON. `CORS_ORIGINS=["https://app.example"]` loads; a bare comma-separated or single URL value raises a settings error at import. `CONSTRAINT_TYPES` is the exception: its validator accepts a JSON array or a comma-separated string.

## Settings read by code

A field is listed here when a production module outside `src/core/config.py` reads it. Fields that production code does not read are in [Settings with no effect](#settings-with-no-effect).

### Application and HTTP

| Setting | Default | Read by | Effect |
|---|---|---|---|
| `ENVIRONMENT` | `development` | `src/auth/jwt_service.py` (`JWTService.__init__`), `src/api/websocket/auth.py` (`verify_websocket_claims`, `verify_project_access`), `src/api/main.py` | `production` makes the JWT key files mandatory; any value other than `development` enables HSTS; `development` is a precondition of the WebSocket project-authorization bypass; `production` also arms the `SECRET_KEY` and `DATABASE_URL` validators |
| `DEBUG` | `false` | `src/api/main.py` (`app` construction) | serves `/docs` and `/redoc` when true; both URLs are disabled when false |
| `CORS_ORIGINS` | `["http://localhost:3000", "http://localhost:8000"]` | `src/api/main.py` (`CORSMiddleware`) | `allow_origins` of the CORS middleware; a JSON array |
| `TRUSTED_PROXIES` | `[]` | `src/api/client_ip.py` (`client_ip`) | peers whose `X-Forwarded-For` header is believed; each entry is validated as an IP address or network; a JSON array |
| `ENABLE_RATE_LIMITING` | `true` | `src/api/main.py` (`RateLimitMiddleware`) | `enabled` flag of the rate-limit middleware |
| `MAX_REQUESTS_PER_MINUTE` | `100` | `src/api/main.py` (`RateLimitMiddleware`) | `requests_per_minute` of the rate-limit middleware |

### Authentication

| Setting | Default | Read by | Effect |
|---|---|---|---|
| `LOGIN_MAX_FAILED_ATTEMPTS` | `5` | `src/api/auth/auth_router.py` (`get_login_throttle`) | failed logins per account identifier before lockout |
| `LOGIN_LOCKOUT_SECONDS` | `900` | `src/api/auth/auth_router.py` (`get_login_throttle`) | lockout window, measured from the first failure |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | `src/auth/jwt_service.py` (`JWTService.__init__`) | access-token lifetime |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `7` | `src/auth/jwt_service.py` (`JWTService.__init__`) | refresh-token lifetime |
| `JWT_PRIVATE_KEY_PATH` | `/secrets/jwt_private.pem` | `src/middleware/auth_middleware.py` (`get_jwt_service`), `src/auth/jwt_service.py` | RS256 private key file; in `production` the file must exist and load, otherwise the JWT service fails to initialise; in other environments a missing file produces a generated key pair, written to the path when it is writable |
| `JWT_PUBLIC_KEY_PATH` | `/secrets/jwt_public.pem` | `src/middleware/auth_middleware.py` (`get_jwt_service`), `src/auth/jwt_service.py` | RS256 public key file; same rules as the private key |
| `BCRYPT_ROUNDS` | `12` | `src/api/auth/auth_router.py` (`get_password_service`) | bcrypt cost factor |
| `PASSWORD_MIN_LENGTH` | `12` | `src/api/auth/auth_router.py` (`get_password_service`) | minimum password length |

### Database and Redis

| Setting | Default | Read by | Effect |
|---|---|---|---|
| `DATABASE_URL` | local development URL (`localhost:5432`, database `research_db`) | `src/models/db/session.py` (`get_database_url`), `src/api/main.py` (`lifespan`) | SQLAlchemy async URL; a URL containing `sqlite` makes the lifespan create tables from the ORM metadata; in `production` the validator rejects URLs containing default credentials |
| `REDIS_URL` | `redis://localhost:6379/0` | `src/api/main.py`, `src/api/auth/auth_router.py`, `src/middleware/auth_middleware.py`, `src/api/routes/health.py`, `src/api/services/event_publisher.py`, `src/ai_brain/router/factory.py` | Redis for rate limiting, idempotency, login throttling, token blacklist, event pub/sub, health probe and adaptive routing state |

### MASR

| Setting | Default | Read by | Effect |
|---|---|---|---|
| `MAX_PARALLEL_WORKERS` | `5` | `src/ai_brain/router/factory.py` (`settings_to_router_config`) | `max_parallel`, the upper bound on parallel workers per query |
| `MASR_DEFAULT_STRATEGY` | `balanced` | `src/ai_brain/router/factory.py` (`settings_to_router_config`) | `default_strategy` of the router |
| `MASR_ENABLE_CACHING` | `true` | `src/ai_brain/router/factory.py` (`settings_to_router_config`) | routing-decision cache on or off |
| `MASR_CACHE_MAX_SIZE` | `1000` | `src/ai_brain/router/factory.py` (`settings_to_router_config`) | routing cache capacity |
| `MASR_CACHE_EVICTION_BATCH_SIZE` | `100` | `src/ai_brain/router/factory.py` (`settings_to_router_config`) | entries evicted per eviction pass |
| `MASR_ENABLE_ADAPTIVE` | `true` | `src/ai_brain/router/factory.py` (`settings_to_router_config`) | older history-based strategy heuristic (`adaptive_strategy_enabled`); it applies once the metrics collector holds more than 100 history entries and never enables the Thompson-sampling allocator |
| `MASR_FAST_PATH_ENABLED` | `true` | `src/ai_brain/router/masr.py` (`MASRouter._should_use_fast_path`) | allows simple single-domain queries to bypass orchestration |
| `AGENTS_MAX_WORKERS_PER_SUPERVISOR` | `10` | `src/ai_brain/router/factory.py` (`settings_to_router_config`) | `max_agents`, the upper bound on agents per query |
| `COST_MAX_PER_REQUEST` | `0.1` | `src/ai_brain/router/factory.py` (`settings_to_router_config`) | copied to `MASRouter.cost_threshold`; no other code reads that attribute, so the value limits nothing |

### Adaptive routing

> **Off by default.** With `ADAPTIVE_ROUTING_ENABLED=true`, the Thompson-sampling allocator adjusts worker counts and collaboration mode from observed outcomes. Durable state needs the Redis instance at `REDIS_URL`.

| Setting | Default | Read by | Effect |
|---|---|---|---|
| `ADAPTIVE_ROUTING_ENABLED` | `false` | `src/ai_brain/router/factory.py` (`settings_to_router_config`, `create_masr_router`, `MASRRuntime.create`) | turns on the Thompson-sampling allocator; the runtime then keeps its state in Redis through `RedisAdaptiveStateStore`; with the flag off the state store is in-memory |
| `ADAPTIVE_ROUTING_MIN_HISTORY` | `300` | `src/ai_brain/router/factory.py` (`settings_to_router_config`, `create_masr_router`, `MASRRuntime.create`) | `safety_sample_size` of the allocation engine |
| `ADAPTIVE_ROUTING_MAX_WORKER_ADJUST` | `2` | `src/ai_brain/router/factory.py` (`settings_to_router_config`, `create_masr_router`, `MASRRuntime.create`) | bound on the adaptive change to the worker count (plus or minus this value) |
| `ADAPTIVE_ROUTING_UPDATE_INTERVAL_SECONDS` | `300` | `src/ai_brain/router/factory.py` (`settings_to_router_config`, `create_masr_router`, `MASRRuntime.create`) | update frequency of the allocation engine |
| `ADAPTIVE_ROUTING_POSTERIOR_TEMP_ENABLED` | `true` | `src/ai_brain/router/factory.py` (`settings_to_router_config`, `create_masr_router`, `MASRRuntime.create`) | sharpens posteriors after enough samples to shift from exploration to exploitation |
| `ADAPTIVE_ROUTING_POSTERIOR_TEMP_THRESHOLD` | `150` | `src/ai_brain/router/factory.py` (`settings_to_router_config`, `create_masr_router`, `MASRRuntime.create`) | samples per experiment before sharpening applies |
| `ADAPTIVE_ROUTING_POSTERIOR_TEMP_FACTOR` | `3.0` | `src/ai_brain/router/factory.py` (`settings_to_router_config`, `create_masr_router`, `MASRRuntime.create`) | scale applied to the posterior parameters |
| `ADAPTIVE_ROUTING_SCHEMA_VERSION` | `1` | `src/ai_brain/router/factory.py` (`settings_to_router_config`, `create_masr_router`, `MASRRuntime.create`) | schema version of persisted adaptive state and outcomes |
| `ADAPTIVE_ROUTING_POLICY_VERSION` | `masr-adaptive-v1` | `src/ai_brain/router/factory.py` (`settings_to_router_config`, `create_masr_router`, `MASRRuntime.create`) | policy version of persisted adaptive state and of the evaluator eligibility policy |
| `ADAPTIVE_ROUTING_ALLOWED_EVALUATORS` | `{}` | `src/ai_brain/router/factory.py` (`settings_to_router_config`, `create_masr_router`, `MASRRuntime.create`) | evaluator name to allowed versions, for `EvaluatorEligibilityPolicy`; a JSON object |

### Memory-informed routing

> **Not wired.** Memory-informed routing is read from settings and passed to the router and workers. `MASRouter.episodic_memory` and `LLMWorkerAgentBase.procedural_memory` start as `None` and no production code assigns them, so enabling the flag changes nothing. See [MASR routing](06-masr-routing.md).

| Setting | Default | Read by | Effect |
|---|---|---|---|
| `MEMORY_INFORMED_ROUTING_ENABLED` | `false` | `src/ai_brain/router/factory.py` (`settings_to_router_config`), `src/agents/llm_worker_base.py` (`_get_procedural_context`) | `create_masr_router` raises at startup when it is true; see the note above |
| `MEMORY_ROUTING_MAX_WORKER_ADJUST` | `2` | `src/ai_brain/router/factory.py` (`settings_to_router_config`) | copied into router configuration; consumed only by the memory path above |
| `MEMORY_ROUTING_FRESHNESS_DAYS` | `30` | `src/ai_brain/router/factory.py` (`settings_to_router_config`) | copied into router configuration; consumed only by the memory path above |
| `MEMORY_PROMPT_MAX_PROCEDURES` | `3` | `src/ai_brain/router/factory.py` (`settings_to_router_config`), `src/agents/llm_worker_base.py` (`_get_procedural_context`) | limit on procedure lines in the prompt; reached only when a procedural memory exists |

### Providers

| Setting | Default | Read by | Effect |
|---|---|---|---|
| `GEMINI_API_KEY` | unset | `src/api/main.py` (`lifespan`), `src/agents/llm_worker_base.py` (`_ensure_gemini_service`), `src/api/services/talkhier_round_executor.py`, `src/api/services/supervisor_coordination_service.py`, `src/core/config.py` (`get_ai_brain_config`) consumed by `src/agents/llm_worker_base.py` (`_build_plan_route`) | API key for `GeminiService` and the Gemini provider; when unset the lifespan logs a warning and workers return fallback text |
| `DEEPSEEK_API_KEY` | unset | `src/core/config.py` (`get_ai_brain_config`) consumed by `src/agents/llm_worker_base.py` (`_build_plan_route`) | key for the DeepSeek provider on plan-backed routes |
| `DEEPSEEK_ENDPOINT` | `https://api.deepseek.com/v1` | `src/core/config.py` (`get_ai_brain_config`) consumed by `src/agents/llm_worker_base.py` (`_build_plan_route`) | DeepSeek base URL on plan-backed routes |
| `DEEPSEEK_DEFAULT_MODEL` | `deepseek-v3` | `src/core/config.py` (`get_ai_brain_config`) consumed by `src/agents/llm_worker_base.py` (`_build_plan_route`) | `default_model` of the DeepSeek provider on plan-backed routes |
| `LLAMA_ENDPOINT` | `http://localhost:11434` | `src/core/config.py` (`get_ai_brain_config`) consumed by `src/agents/llm_worker_base.py` (`_build_plan_route`) | Ollama base URL on plan-backed routes |
| `LLAMA_DEFAULT_MODEL` | `llama3.3:70b` | `src/core/config.py` (`get_ai_brain_config`) consumed by `src/agents/llm_worker_base.py` (`_build_plan_route`) | `default_model` of the Llama provider on plan-backed routes |
| `LLAMA_KEEP_ALIVE` | `5m` | `src/core/config.py` (`get_ai_brain_config`) consumed by `src/agents/llm_worker_base.py` (`_build_plan_route`) | `keep_alive` sent to Ollama on plan-backed routes |
| `GEMINI_DEFAULT_MODEL` | `gemini-pro` | `src/core/config.py` (`get_ai_brain_config`) consumed by `src/agents/llm_worker_base.py` (`_build_plan_route`) | `default_model` of the Gemini provider on plan-backed routes |
| `OPENROUTER_API_KEY` | unset | `src/agents/llm_worker_base.py` (`_build_legacy_openrouter_route`), `src/core/config.py` (`get_ai_brain_config`) consumed by `src/agents/llm_worker_base.py` (`_build_plan_route`) | key for the OpenRouter provider; the multi-provider route also needs `MULTI_PROVIDER_ROUTING_ENABLED` |
| `OPENROUTER_ENDPOINT` | `https://openrouter.ai/api/v1/chat/completions` | `src/agents/llm_worker_base.py` (`_build_legacy_openrouter_route`), `src/core/config.py` (`get_ai_brain_config`) consumed by `src/agents/llm_worker_base.py` (`_build_plan_route`) | OpenRouter chat-completions URL |
| `OPENROUTER_TIER_MAPPING` | three tiers: `simple`, `balanced`, `complex` | `src/agents/llm_worker_base.py` (`_build_legacy_openrouter_route`), `src/core/config.py` (`get_ai_brain_config`) consumed by `src/agents/llm_worker_base.py` (`_build_plan_route`) | tier name to OpenRouter model slug; a JSON object |
| `MULTI_PROVIDER_ROUTING_ENABLED` | `false` | `src/agents/llm_worker_base.py` (`_build_legacy_openrouter_route`) | with `OPENROUTER_API_KEY`, routes non-plan worker calls through a `ModelRouter` with OpenRouter |

### MCP tool path

> **On by default.** Plan workers receive the mediated tool path (capability grants, audit store, source snapshots) unless `MCP_TOOL_PATH_ENABLED=false`. The bridge needs an explicit audit store or a session factory, otherwise `MASRSupervisorBridge._execute_plan_worker` raises `RuntimeError`; the API supplies its session factory only when the database initialized at startup.

| Setting | Default | Read by | Effect |
|---|---|---|---|
| `MCP_TOOL_PATH_ENABLED` | `true` | `src/ai_brain/integration/masr_supervisor_bridge.py` (`_mcp_tool_path_enabled`, `_execute_plan_worker`) | when true (the default), plan workers are configured with an `MCPIntegration` (capability grants, audit store, evidence recorder); when false, the bridge adds no such configuration |
| `SOURCE_SNAPSHOT_DIR` | `data/source_snapshots` | `src/ai_brain/integration/masr_supervisor_bridge.py` (`_execute_plan_worker`) | root of the filesystem snapshot store for cited sources; the validator resolves a relative path against the working directory at startup |

### Evaluation runs

| Setting | Default | Read by | Effect |
|---|---|---|---|
| `EVAL_RUN_DATABASE_URL` | unset | `src/evals/trial_setup.py` (`build_harvest_service`), `src/evals/cost_pilot.py` (`preflight`) | database the agent cost pilot writes its runs to; the API never reads it. The pilot refuses to start when it is unset or names the same database as `DATABASE_URL` (see [Live evaluations](../testing-guide.md#live-evaluations)) |

### Constraint pinning

> **Off by default.** With `ENABLE_CONSTRAINT_PINNING=true`, supervisors extract constraints before compaction and only log what they find.

| Setting | Default | Read by | Effect |
|---|---|---|---|
| `ENABLE_CONSTRAINT_PINNING` | `false` | `src/agents/supervisors/base_supervisor.py` (`execute`) | extracts constraints from the original query before compaction and logs the extraction; extraction only |
| `CONSTRAINT_TYPES` | `["routing", "qa", "format", "security"]` | `src/agents/supervisors/base_supervisor.py` (`execute`) | constraint types to extract; accepts a JSON array or a comma-separated string; each element must be one of `routing`, `qa`, `format`, `security` |

### Tracing and telemetry

> **Off by default.** Langfuse tracing (`LANGFUSE_ENABLED=true`, plus both keys) and compaction telemetry (`ENABLE_CONTEXT_COMPACTION_TELEMETRY=true`) are disabled.

| Setting | Default | Read by | Effect |
|---|---|---|---|
| `ENABLE_CONTEXT_COMPACTION_TELEMETRY` | `false` | `src/core/telemetry.py` (`telemetry_enabled`) | token-count measurements logged by supervisors and `DirectExecutionService` |
| `LANGFUSE_ENABLED` | `false` | `src/core/tracing.py` (`_initialize_langfuse`), `src/api/main.py` (`lifespan`) | enables the Langfuse client, created eagerly at startup |
| `LANGFUSE_PUBLIC_KEY` | unset | `src/core/tracing.py` (`_initialize_langfuse`) | required when enabled; with a missing key the client is not created and a warning is logged |
| `LANGFUSE_SECRET_KEY` | unset | `src/core/tracing.py` (`_initialize_langfuse`) | required when enabled; same rule as the public key |
| `LANGFUSE_HOST` | unset | `src/core/tracing.py` (`_initialize_langfuse`) | Langfuse server URL; unset uses Langfuse cloud |

### WebSocket development bypass

> **Off by default.** With `ENVIRONMENT=development` and `DEV_ALLOW_ANONYMOUS_WEBSOCKETS=true` both set, project authorization is skipped for callers who authenticated. It does not admit a connection without a token: every WebSocket route requires one.

| Setting | Default | Read by | Effect |
|---|---|---|---|
| `DEV_ALLOW_ANONYMOUS_WEBSOCKETS` | `false` | `src/api/websocket/auth.py` (`verify_websocket_claims`, `verify_project_access`) | with `ENVIRONMENT=development`, `verify_project_access` returns `True` without checking project ownership; a WebSocket connection still needs a token |

Notes on the provider rows:

- The `DEEPSEEK_*`, `LLAMA_*` and `GEMINI_DEFAULT_MODEL` rows reach providers only through `Settings.get_ai_brain_config()["providers"]`. That method has one production caller, `_build_plan_route` in `src/agents/llm_worker_base.py`. It reads only the entry for the plan's primary provider and forces `enabled` to true for it.
- `OPENROUTER_TIER_MAPPING` maps `simple` to one slug and maps both `balanced` and `complex` to a second slug; the slugs are defined in `src/core/config.py`.
- `DATABASE_URL` defaults to a local development URL with default credentials. In `production` the validator rejects any URL containing `research:research123`, `postgres:postgres` or `:password@`.

See [LLM providers](10-llm-providers.md) for how workers choose a provider.

## Settings with no effect

> **Not wired.** These fields are declared on `Settings`, and some are copied into `get_ai_brain_config()`. No production code reads them (the only caller of `get_ai_brain_config` reads just its `providers` entries), so setting them changes nothing.

The table lists 96 rows: 95 fields of `Settings` and `OTEL_EXPORTER_OTLP_ENDPOINT`, which `Settings` does not declare. Together with the 62 `Settings` rows above they account for 157 of the 168 fields of `Settings`; the ten `DOCUMENT_*` fields and `PROVIDER_TERMS_VERIFIED_ON` are not listed on this page.

| Setting | Default | Family |
|---|---|---|
| `API_HOST` | `0.0.0.0` | Application and server |
| `API_PORT` | `8000` | Application and server |
| `API_WORKERS` | `4` | Application and server |
| `SECRET_KEY` | `MUST_SET_IN_ENV` (rejected by the validator) | Application and server |
| `LOG_LEVEL` | `INFO` | Application and server |
| `TEMPORAL_HOST` | `localhost:7233` | Workers, MCP server and Temporal |
| `TEMPORAL_NAMESPACE` | `default` | Workers, MCP server and Temporal |
| `WORKER_CONCURRENCY` | `10` | Workers, MCP server and Temporal |
| `TASK_TIMEOUT_SECONDS` | `300` | Workers, MCP server and Temporal |
| `MCP_TOOLS_ENABLED` | `true` | Workers, MCP server and Temporal |
| `ENABLE_METRICS` | `true` | Monitoring and caching flags |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | Monitoring and caching flags |
| `ENABLE_CACHE` | `true` | Monitoring and caching flags |
| `JWT_ALGORITHM` | `RS256` | Authentication, sessions and OAuth |
| `PASSWORD_HISTORY_LIMIT` | `5` | Authentication, sessions and OAuth |
| `CHECK_PASSWORD_BREACHES` | `true` | Authentication, sessions and OAuth |
| `SESSION_SECRET_KEY` | unset | Authentication, sessions and OAuth |
| `SESSION_EXPIRE_HOURS` | `24` | Authentication, sessions and OAuth |
| `RATE_LIMIT_REQUESTS` | `100` | Authentication, sessions and OAuth |
| `RATE_LIMIT_PERIOD` | `60` | Authentication, sessions and OAuth |
| `ENABLE_MFA` | `false` | Authentication, sessions and OAuth |
| `MFA_ISSUER` | `ResearchPlatform` | Authentication, sessions and OAuth |
| `AI_BRAIN_ENABLED` | `true` | Brain mode switches |
| `AI_BRAIN_MODE` | `hybrid` | Brain mode switches |
| `MODEL_PROVIDERS_ENABLED` | `true` | Provider switches and model router |
| `DEEPSEEK_ENABLED` | `false` | Provider switches and model router |
| `LLAMA_ENABLED` | `false` | Provider switches and model router |
| `GEMINI_ENABLED` | `true` | Provider switches and model router |
| `OPENROUTER_ENABLED` | `false` | Provider switches and model router |
| `MODEL_ROUTER_HEALTH_CHECK_INTERVAL` | `300` | Provider switches and model router |
| `MODEL_ROUTER_MAX_RETRIES` | `3` | Provider switches and model router |
| `MODEL_ROUTER_ENABLE_FALLBACK` | `true` | Provider switches and model router |
| `MEMORY_ENABLE_CROSS_TIER` | `true` | Memory tiers |
| `MEMORY_MAX_RECALL_ITEMS` | `10` | Memory tiers |
| `MEMORY_CONSOLIDATION_INTERVAL` | `3600` | Memory tiers |
| `WORKING_MEMORY_ENABLED` | `true` | Memory tiers |
| `WORKING_MEMORY_DEFAULT_TTL` | `3600` | Memory tiers |
| `WORKING_MEMORY_MAX_MEMORY_MB` | `512` | Memory tiers |
| `WORKING_MEMORY_CLEANUP_INTERVAL` | `300` | Memory tiers |
| `WORKING_MEMORY_CONVERSATION_TTL` | `7200` | Memory tiers |
| `WORKING_MEMORY_MAX_MESSAGES_IN_CONTEXT` | `50` | Memory tiers |
| `EPISODIC_MEMORY_ENABLED` | `true` | Memory tiers |
| `EPISODIC_MEMORY_RETENTION_DAYS` | `90` | Memory tiers |
| `EPISODIC_MEMORY_MAX_SESSION_DURATION_HOURS` | `24` | Memory tiers |
| `SEMANTIC_MEMORY_ENABLED` | `true` | Memory tiers |
| `SEMANTIC_MEMORY_VECTOR_DB_URL` | `http://localhost:6333` | Memory tiers |
| `SEMANTIC_MEMORY_COLLECTION_NAME` | `cerebro_semantic` | Memory tiers |
| `SEMANTIC_MEMORY_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Memory tiers |
| `SEMANTIC_MEMORY_EMBEDDING_DIMENSION` | `384` | Memory tiers |
| `PROCEDURAL_MEMORY_ENABLED` | `true` | Memory tiers |
| `PROCEDURAL_MEMORY_STORAGE_PATH` | `data/procedural_memory.json` | Memory tiers |
| `PROCEDURAL_MEMORY_MIN_CONFIDENCE` | `0.7` | Memory tiers |
| `PROCEDURAL_MEMORY_MIN_USAGE_PROMOTION` | `5` | Memory tiers |
| `AGENTS_MAX_CONCURRENT` | `20` | Agent system |
| `AGENTS_MAX_SUPERVISORS` | `5` | Agent system |
| `AGENTS_ENABLE_DYNAMIC_SPAWNING` | `true` | Agent system |
| `AGENTS_DEFAULT_TIMEOUT` | `300` | Agent system |
| `AGENTS_MAX_RETRIES` | `3` | Agent system |
| `HIERARCHICAL_MAX_DEPTH` | `3` | Agent system |
| `HIERARCHICAL_ENABLE_AUTO_SCALING` | `true` | Agent system |
| `HIERARCHICAL_SCALE_UP_THRESHOLD` | `0.8` | Agent system |
| `HIERARCHICAL_SCALE_DOWN_THRESHOLD` | `0.3` | Agent system |
| `MULTI_DOMAIN_MERGE_STRATEGY` | `concat` | Agent system |
| `MULTI_DOMAIN_MERGE_PER_DOMAIN_CHAR_LIMIT` | `4000` | Agent system |
| `SELF_IMPROVEMENT_REFLECTION_FREQUENCY` | `100` | Learning and fine-tuning |
| `SELF_IMPROVEMENT_PERFORMANCE_THRESHOLD` | `0.8` | Learning and fine-tuning |
| `SELF_IMPROVEMENT_ENABLE_PROMPT_OPTIMIZATION` | `true` | Learning and fine-tuning |
| `FINE_TUNING_ENABLED` | `false` | Learning and fine-tuning |
| `FINE_TUNING_MIN_EXAMPLES` | `100` | Learning and fine-tuning |
| `FINE_TUNING_QUALITY_THRESHOLD` | `0.9` | Learning and fine-tuning |
| `FINE_TUNING_AUTO_TRIGGER` | `false` | Learning and fine-tuning |
| `CLOUD_RUN_ENABLED` | `false` | Cloud Run, performance and cost |
| `CLOUD_RUN_REGION` | `us-central1` | Cloud Run, performance and cost |
| `CLOUD_RUN_MAX_INSTANCES` | `100` | Cloud Run, performance and cost |
| `CLOUD_RUN_MIN_INSTANCES` | `1` | Cloud Run, performance and cost |
| `CLOUD_RUN_CPU_LIMIT` | `2` | Cloud Run, performance and cost |
| `CLOUD_RUN_MEMORY_LIMIT` | `4Gi` | Cloud Run, performance and cost |
| `PERF_ENABLE_PROFILING` | `false` | Cloud Run, performance and cost |
| `PERF_METRICS_RETENTION_DAYS` | `30` | Cloud Run, performance and cost |
| `PERF_SLOW_QUERY_THRESHOLD_MS` | `1000` | Cloud Run, performance and cost |
| `PERF_ENABLE_QUERY_OPTIMIZATION` | `true` | Cloud Run, performance and cost |
| `COST_OPTIMIZATION_ENABLED` | `true` | Cloud Run, performance and cost |
| `COST_DAILY_BUDGET_LIMIT` | `100.0` | Cloud Run, performance and cost |
| `COST_ENABLE_BUDGET_ALERTS` | `true` | Cloud Run, performance and cost |
| `DEV_ENABLE_AI_BRAIN_DEBUG` | `false` | Development switches |
| `DEV_ENABLE_MEMORY_DEBUG` | `false` | Development switches |
| `DEV_ENABLE_ROUTING_DEBUG` | `false` | Development switches |
| `DEV_MOCK_PROVIDERS` | `false` | Development switches |
| `DEV_MOCK_MEMORY` | `false` | Development switches |
| `MASR_COMPLEXITY_WEIGHTS` | `linguistic` 0.15, `reasoning` 0.25, `domain` 0.20, `data` 0.15, `output` 0.15, `time` 0.05, `quality` 0.05 | MASR |
| `MASR_COMPLEXITY_THRESHOLDS` | `simple` 0.12, `moderate` 0.22, `complex` 1.0 | MASR |

Notes on specific rows:

- `MASR_COMPLEXITY_WEIGHTS` and `MASR_COMPLEXITY_THRESHOLDS` reach `QueryComplexityAnalyzer` through `settings_to_router_config`; the level cutoffs are the `simple` and `moderate` values ([MASR routing](06-masr-routing.md#query-analysis)).
- `SECRET_KEY` is never used for signing or encryption. Its validator still runs: it rejects any value shorter than 32 characters in every environment, which includes the placeholder default (in production the placeholder also gets its own error message), so the application cannot import `src.core.config` without a `SECRET_KEY` of at least 32 characters.
- `JWT_ALGORITHM` is not read; `JWTService` fixes the algorithm at RS256.
- `LOG_LEVEL` is the minimum level of the structlog events the API process emits (`configure_logging` in `src/core/observability.py`, called when `src.api.main` is imported). It takes a standard level name in any case; any other value stops the process at startup. Uvicorn's own access and error logs do not follow it.
- `DEEPSEEK_ENABLED`, `LLAMA_ENABLED`, `GEMINI_ENABLED` and `OPENROUTER_ENABLED` are emitted into the provider configuration, but `_build_plan_route` overwrites `enabled` with true and no other path reads them. `MODEL_PROVIDERS_ENABLED` is emitted as `providers["enabled"]`, which no code reads.
- `MODEL_ROUTER_*` values are not passed to `ModelRouter`, which reads `health_check_interval`, `max_retries` and `enable_fallback` from its own configuration dictionary with its own defaults.
- `CLOUD_RUN_*`, `PERF_*`, `COST_DAILY_BUDGET_LIMIT`, `COST_ENABLE_BUDGET_ALERTS` and `COST_OPTIMIZATION_ENABLED` configure nothing. `COST_MAX_PER_REQUEST` is the only `COST_*` field read, and its value limits nothing (see the MASR table).
- `AI_BRAIN_ENABLED` and `AI_BRAIN_MODE` are read only by `Settings.is_ai_brain_mode` and `Settings.is_research_only_mode`, which have no callers.

## Environment variables read outside Settings

### Gemini client

`GeminiConfig.from_env` reads the variables below. `GeminiService` calls it for every construction, including the production ones that pass `api_key` (`src/api/main.py`, `src/agents/llm_worker_base.py`, `src/api/services/talkhier_round_executor.py`, `src/api/services/supervisor_coordination_service.py`), and the `api_key` argument replaces `GEMINI_API_KEY`. Explicit `model_name` and keyword arguments override the environment.

| Variable | Default | Effect |
|---|---|---|
| `GEMINI_API_KEY` | required | API key; `from_env` raises `ValueError` when it is empty |
| `GEMINI_MODEL` | `gemini-2.5-flash` | model name |
| `GEMINI_TEMPERATURE` | `0.7` | sampling temperature |
| `GEMINI_TOP_P` | `0.9` | nucleus sampling |
| `GEMINI_TOP_K` | `40` | top-k sampling |
| `GEMINI_MAX_TOKENS` | `32768` | `max_output_tokens` |
| `GEMINI_RATE_LIMIT` | `10` | requests per period |
| `GEMINI_RATE_PERIOD` | `60` | period in seconds |
| `GEMINI_MAX_CONCURRENT` | `2` | concurrent requests |
| `GEMINI_CACHE_ENABLED` | `true` | response cache switch |
| `GEMINI_CACHE_TTL` | `3600` | cache TTL in seconds |

No variable named `GEMINI_TIMEOUT` or `GEMINI_RETRY_*` is read anywhere in `src`.

### Reports

`src/services/report_config.py` (`ReportSettings`) is a separate `BaseSettings` class. Each field has a `validation_alias`, so the variable names below are the aliases; the `RESEARCH_` prefix declared on the class does not apply to aliased fields. The class is case-insensitive and also accepts the field name (for example `report_storage_path`) and the `RESEARCH_`-prefixed field name, at the `pydantic-settings` version pinned in `uv.lock`. Defaults are in the table; see [Reports](11-reports.md) for what each switch does.

| Variable | Default | Read by | Effect |
|---|---|---|---|
| `REPORT_STORAGE_PATH` | `./reports` | `src/services/report_generator.py`, `src/services/report_storage.py` | directory for generated report files |
| `REPORT_TEMPLATE_PATH` | `./src/templates/reports` | `src/services/template_renderer.py` | Jinja template directory |
| `ENABLE_PDF_GENERATION` | `true` | `src/services/report_output_generator.py` (`generate_pdf`) | false makes a PDF request fail |
| `ENABLE_LATEX_GENERATION` | `true` | `src/services/report_output_generator.py` (`generate_latex`) | false makes a LaTeX request fail |
| `PARALLEL_REPORT_GENERATION` | `true` | `src/services/report_output_generator.py` (`generate_formats`) | formats are generated concurrently when more than one is requested |
| `ENABLE_TEMPLATE_CACHE` | `true` | `src/services/template_renderer.py` (`_get_template`) | caches loaded templates in the renderer instance |
| `REQUIRE_CITATIONS` | `true` | `src/services/report_config.py` (`ReportQualityConfig.requires_citations`), `src/services/report_structure_builder.py` | a report without citations logs a warning |
| `MAX_REPORT_SIZE_MB` | `100` | none | no effect |
| `DEFAULT_REPORT_FORMAT` | `html` | none | no effect |
| `DEFAULT_CITATION_STYLE` | `APA` | none | no effect |
| `CUSTOM_CSS_PATH` | unset | none | no effect |
| `REPORT_GENERATION_TIMEOUT` | `300` | none | no effect |
| `MAX_CONCURRENT_GENERATIONS` | `3` | none | no effect |
| `TEMPLATE_CACHE_TTL` | `3600` | none | no effect |
| `MIN_REPORT_WORD_COUNT` | `100` | `ReportQualityConfig.get_min_word_count` | fallback for a report type with no entry; every `ReportType` has an entry, so the value is never used |
| `MIN_REPORT_SOURCES` | `1` | `ReportQualityConfig.get_min_sources` | fallback for a report type with no entry; every `ReportType` has an entry, so the value is never used |
| `ENABLE_VISUALIZATIONS` | `true` | `VisualizationGenerator` | when false no chart image is rendered and charts appear as value tables |
| `MAX_VISUALIZATIONS_PER_REPORT` | `20` | `VisualizationGenerator` | caps the charts rendered per report |
| `DEFAULT_CHART_WIDTH` | `800` | `VisualizationGenerator` | chart width in pixels |
| `DEFAULT_CHART_HEIGHT` | `600` | `VisualizationGenerator` | chart height in pixels |

### CLI

`src/cli/config.py` (`CLIConfig.from_env`) loads `~/.research-cli.env` when it exists, otherwise `.env.cli` in the working directory, and then reads these variables:

| Variable | Default | Effect |
|---|---|---|
| `RESEARCH_API_URL` | `http://localhost:8000` | API base URL; must start with `http://` or `https://`; a trailing slash is removed |
| `RESEARCH_API_TIMEOUT` | `30` | request timeout in seconds |
| `RESEARCH_OUTPUT_FORMAT` | `table` | one of `table`, `json`, `yaml`, `csv` |
| `RESEARCH_VERBOSE` | `false` | verbose output; `true`, `1` or `yes` enable it |
| `RESEARCH_COLOR` | `true` | coloured output; `true`, `1` or `yes` enable it |
| `RESEARCH_MAX_RETRIES` | `3` | retries for failed requests |
| `RESEARCH_AUTH_TOKEN` | unset | JWT stored in the configuration |

See [CLI and web clients](13-cli-and-web-clients.md) for the commands.

### Document encryption

`DOCUMENT_ENCRYPTION_KEY` (default empty, no fallback to another variable) is a Fernet key; a comma-separated list rotates keys (the first encrypts, all decrypt). `document_cipher()` builds a `MemoryEncryption` from it and raises `DocumentEncryptionUnavailable` when it is blank, so a missing key never stores plaintext. The `*_enc` columns of `uploaded_documents`, `critique_findings`, `similarity_matches` and the document critique and similarity repositories use it. At startup `require_document_key` (called from the API lifespan) refuses to start when `DOCUMENT_UPLOADS_ENABLED`, `DOCUMENT_CRITIQUE_ENABLED` or `SIMILARITY_CHECKS_ENABLED` is true and the key is blank or not a valid Fernet key.

### Memory encryption

> **Not wired.** The `MemoryEncryption` class is live, but only through `document_cipher()` above, which ignores `MEMORY_ENCRYPTION_KEY`. The `MEMORY_ENCRYPTION_KEY` fallback is read only by `WorkingMemoryManager` and `EpisodicMemoryManager`, which no production code constructs.

### Database migrations

`alembic/env.py` reads `DATABASE_URL` from the process environment, rewrites a `postgres://` or `postgresql://` scheme to `postgresql+asyncpg://` and falls back to `sqlalchemy.url` in `alembic.ini` and then to the local development URL. It does not read `.env` files; the container entrypoint (`docker/entrypoint.sh`) runs `alembic upgrade head` before starting the application and refuses to start when the migration fails.

### Compose-level variables

Compose interpolates these variables from the shell or an `.env` file next to the compose file:

| Variable | Default | File | Effect |
|---|---|---|---|
| `API_HOST_PORT` | `8000` | `docker-compose.yml` | host port mapped to the API |
| `POSTGRES_HOST_PORT` | `5432` | `docker-compose.yml` | host port mapped to Postgres |
| `REDIS_HOST_PORT` | `6379` | `docker-compose.yml` | host port mapped to Redis |
| `NGINX_HTTP_HOST_PORT` | `80` | `docker-compose.yml` | host port for nginx HTTP |
| `NGINX_HTTPS_HOST_PORT` | `443` | `docker-compose.yml` | host port for nginx HTTPS |
| `WEB_HOST_PORT` | `3000` | `docker-compose.yml` | host port for the web workbench |
| `GEMINI_API_KEY` | empty | both | passed to the API container |
| `SECRET_KEY` | placeholder in `docker-compose.yml`; no default in `docker-compose.production.yml` | both | passed to the API container |
| `MASR_ENABLE_ADAPTIVE`, `ADAPTIVE_ROUTING_ENABLED`, `MEMORY_INFORMED_ROUTING_ENABLED` | `true`, `false`, `false` | `docker-compose.yml` | passed to the API container |
| `DB_PASSWORD` | none | `docker-compose.production.yml` | Postgres password, also embedded in `DATABASE_URL` |
| `REDIS_PASSWORD` | none | `docker-compose.production.yml` | Redis password, also embedded in `REDIS_URL` |
| `CORS_ORIGINS` | none | `docker-compose.production.yml` | passed to the API container |
| `GRAFANA_PASSWORD` | none | `docker-compose.production.yml` | Grafana admin password |
| `JWT_PRIVATE_KEY_FILE`, `JWT_PUBLIC_KEY_FILE` | none; compose stops with an error when either is unset | `docker-compose.production.yml` | host paths of the RSA key pair, exposed to the API as compose secrets and mounted at `/run/secrets` |

The production compose file also sets literal environment entries that no code reads: `MASR_ENABLE_ROUTING`, `MASR_ENABLE_LEARNING`, `MASR_MAX_AGENTS` and `MASR_QUALITY_THRESHOLD`, all on the `api` service. The production image starts `uvicorn` with `--workers 1`.

## Example environment files

| File | Purpose |
|---|---|
| `.env.example` | local development template; copy to `.env` |
| `.env.production.example` | production template; copy and fill in |
| `.env.cli.example` | `research-cli` settings (`RESEARCH_*`); every key is read |

`.env.example` holds 54 uncommented keys, plus the commented `SOURCE_SNAPSHOT_DIR`. 27 are read by code or interpolated by a compose file. The other 27 are read by nothing: `ALERT_EMAIL`, `ALERT_WEBHOOK_URL`, `API_HOST`, `API_PORT`, `API_WORKERS`, `AVAILABILITY_ZONE`, `DB_HOST`, `DB_NAME`, `DB_PORT`, `DB_USER`, `ENABLE_CACHE`, `ENABLE_METRICS`, `INSTANCE_ID`, `JWT_ALGORITHM`, `MCP_TOOLS_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `REDIS_DB`, `REDIS_HOST`, `REDIS_PORT`, `REGION`, `SECRETS_PROVIDER`, `TASK_TIMEOUT_SECONDS`, `TEMPORAL_HOST`, `TEMPORAL_NAMESPACE`, `TEMPORAL_PORT`, `VALIDATE_ENV_VARS`, `WORKER_CONCURRENCY`.

The file also sets values that differ from the code defaults: `DEBUG=true` (default false), and `ENABLE_RATE_LIMITING=false` (default true).

`.env.production.example` holds 68 keys. 13 are read by code or interpolated by `docker-compose.production.yml`: `CORS_ORIGINS`, `DATABASE_URL`, `DB_PASSWORD`, `DEBUG`, `ENABLE_RATE_LIMITING`, `ENVIRONMENT`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `MAX_REQUESTS_PER_MINUTE`, `REDIS_PASSWORD`, `REDIS_URL` and `SECRET_KEY`. `GEMINI_MODEL=gemini-pro` overrides the default `gemini-2.5-flash` through `GeminiConfig.from_env`. `LOG_LEVEL` is read as well. The other 55 are read by nothing. The non-secret ones are `ALERT_EMAIL`, `ALERT_WEBHOOK_URL`, `API_HOST`, `API_PORT`, `API_WORKERS`, `AUDIT_LOG_FILE`, `AUDIT_LOGGING_ENABLED`, `AVAILABILITY_ZONE`, `BACKUP_ENABLED`, `BACKUP_RETENTION_DAYS`, `BACKUP_S3_BUCKET`, `CDN_ENABLED`, `CDN_URL`, `COMPRESSION_LEVEL`, `CSP_POLICY`, `DB_HOST`, `DB_NAME`, `DB_PORT`, `DB_USER`, `ENABLE_CACHE`, `ENABLE_HTTP2`, `ENABLE_KEEPALIVE`, `ENABLE_METRICS`, `ENABLE_RESPONSE_COMPRESSION`, `INSTANCE_ID`, `JWT_ALGORITHM`, `JWT_EXPIRATION_MINUTES`, `KEEPALIVE_TIMEOUT`, `MAX_CONCURRENT_REQUESTS`, `MAX_REQUEST_SIZE`, `MAX_UPLOAD_SIZE`, `MAX_WEBSOCKET_CONNECTIONS`, `MCP_TOOLS_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `RATE_LIMIT_PER_DAY`, `RATE_LIMIT_PER_HOUR`, `RATE_LIMIT_PER_MINUTE`, `REDIS_DB`, `REDIS_HOST`, `REDIS_PORT`, `REGION`, `SECURITY_HEADERS_ENABLED`, `SSL_CA_FILE`, `SSL_CERT_FILE`, `TASK_TIMEOUT_SECONDS`, `TEMPORAL_HOST`, `TEMPORAL_NAMESPACE`, `TEMPORAL_PORT`, `VALIDATE_ENV_VARS` and `WORKER_CONCURRENCY`. The remainder are a JWT secret entry, a TLS key entry, `SECRETS_PROVIDER` and the secret-provider keys. JWT signing uses the RSA key files named by `JWT_PRIVATE_KEY_PATH` and `JWT_PUBLIC_KEY_PATH`, which the production compose file sets to `/run/secrets/jwt_private.pem` and `/run/secrets/jwt_public.pem`.

## Validation and environment-dependent behaviour

`Settings` validators run at import:

| Field | Rule |
|---|---|
| `TRUSTED_PROXIES` | every entry must parse as an IP address or network (`ipaddress.ip_network`, non-strict) |
| `SOURCE_SNAPSHOT_DIR` | expanded (`~`) and resolved to an absolute path against the working directory |
| `SECRET_KEY` | in `production` the placeholder `MUST_SET_IN_ENV` is rejected; in every environment a value shorter than 32 characters is rejected |
| `DATABASE_URL` | in `production`, a URL containing `research:research123`, `postgres:postgres` or `:password@` is rejected |
| `CONSTRAINT_TYPES` | a JSON array or comma-separated string is normalised to a list; every element must be one of `routing`, `qa`, `format`, `security`; an empty value loads as an empty list, which `ConstraintRegistry` replaces with all four types and logs `constraint_registry_empty_types_fallback` |

`ENVIRONMENT` changes:

- `JWTService` treats `production` as requiring pre-provisioned RSA key files and fails closed when they are unreadable. Other values generate a key pair when the file is missing.
- `SecurityHeadersMiddleware` receives `hsts_enabled=True` for every value except `development`.
- The WebSocket project-authorization bypass requires exactly `development`.
- The `SECRET_KEY` and `DATABASE_URL` validators apply their production rules.

`DEBUG` controls only whether `/docs` and `/redoc` are served.

## Source map

- `src/core/config.py`: `Settings`, validators, `get_settings`, `get_ai_brain_config`
- `src/core/environment.py`: dotenv merge and precedence (`load_environment`)
- `src/services/gemini_config.py`: `GeminiConfig` and its `from_env` reader
- `src/services/report_config.py`: `ReportSettings` and report format, template and quality tables
- `src/cli/config.py`: `CLIConfig` and `RESEARCH_*` variables
- `src/core/memory_encryption.py`: `MemoryEncryption`, `document_cipher` (`DOCUMENT_ENCRYPTION_KEY`) and the `MEMORY_ENCRYPTION_KEY` fallback
- `src/ai_brain/router/factory.py`: maps `Settings` onto the MASR router configuration
- `alembic/env.py`: `DATABASE_URL` for migrations
- `.env.example`, `.env.production.example`, `.env.cli.example`: environment templates
- `docker-compose.yml`, `docker-compose.production.yml`: compose-level variables and literal service environment
