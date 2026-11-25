# HTTP API

The FastAPI application in `src/api/main.py` serves the HTTP and WebSocket API under the title "Research Platform API". Domain routers are mounted under `/api/v1`; the health probes and the `/ws` WebSocket routes are mounted at the root, and the supervisor and TalkHier (supervisor messaging, see [agents and supervisors](07-agents-and-supervisors.md#talkhier-api)) WebSocket routes sit under their routers' prefixes. Every request passes through the middleware stack described in [Conventions](#conventions) before it reaches a router.

## Summary

| Feature | Status | Entry point | Source |
|---|---|---|---|
| Health, readiness, liveness probes | Active | `GET /health`, `GET /ready`, `GET /live` | `src/api/routes/health.py` (`router`) |
| Prometheus metrics | Active | `GET /metrics` (bearer token required) | `src/api/main.py` (`metrics_app`) |
| Authentication | Active | `/api/v1/auth/*` | `src/api/auth/auth_router.py` (`router`) |
| User data erasure | Active | `DELETE /api/v1/users/{user_id}/gdpr` | `src/api/routes/users.py` (`delete_user_gdpr`) |
| Research projects | Active | `/api/v1/research/*` | `src/api/routes/research.py` (`router`) |
| Reports | Active | `/api/v1/reports/*` | `src/api/routes/reports.py` (`router`) |
| Query (MASR-routed execution) | Active | `/api/v1/query/*` | `src/api/routes/query_api.py` (`router`) |
| Direct agents | Active | `/api/v1/agents/*` | `src/api/routes/agent_api.py` (`router`) |
| MASR routing | Active | `/api/v1/masr/*` | `src/api/routes/masr_api.py` (`router`) |
| Supervisors | Active | `/api/v1/supervisors/*` | `src/api/routes/supervisor_api.py` (`router`) |
| TalkHier sessions | Active | `/api/v1/talkhier/*` | `src/api/routes/talkhier_api.py` (`router`) |
| Run event stream (SSE) | Active | `GET /api/v1/runs/{run_id}/events` | `src/api/routes/run_stream.py` (`stream_run_events`) |
| WebSockets | Active | `/ws`, `/ws/projects/{project_id}`, `/ws/cli/{project_id}`, `/ws/runs/{run_id}` | `src/api/routes/websocket.py` (`router`) |
| Idempotency keys | Active | `Idempotency-Key` header on `POST /api/v1/query/*` | `src/api/middleware/idempotency.py` (`IdempotencyMiddleware`) |
| Rate limiting | Active | every HTTP request outside the exempt paths | `src/api/middleware/rate_limiter.py` (`RateLimitMiddleware`) |
| MASR WebSocket | Not wired | none | `src/api/routes/masr_api.py` (commented-out `@router.websocket("/ws")`) |
| Scope refinement | Not wired | `POST /api/v1/research/projects/{project_id}/refine` | `src/api/routes/research.py` (`refine_research_scope`) |

## Conventions

**Base path.** Domain routes live under `/api/v1`. Request and response bodies are JSON, except the report download (a file stream) and the run event stream (`text/event-stream`).

**Middleware order.** The order is in the [architecture overview](01-architecture-overview.md#request-path). Authentication runs outside rate limiting and idempotency, so both read the caller identity that authentication sets on the request.

**Authority reference.** Request bodies that start execution accept an `authority_reference` object with `authority_id` and `authority_version`. The schema marks it optional, but a request without it is refused with `EXECUTION_AUTHORITY_REQUIRED` and never starts. It is an opaque handle that a trusted resolver turns into the execution authority for the caller's organization; see [research execution](05-research-execution.md).

**Idempotency-Key.** `IdempotencyMiddleware` acts only on `POST` requests that carry an `Idempotency-Key` header and whose path starts with `/api/v1/query` (the default `path_prefixes`, which `src/api/main.py` does not override).

- The cache key is a SHA-256 over organization id, user id, HTTP method, path, the header value and a SHA-256 of the request body. A second request with the same header value but a different body is a different key, and so is the same header from a different user or organization.
- A response with status 200 to 299 is stored for 24 hours (`DEFAULT_IDEMPOTENCY_TTL_SECONDS`) and replayed for matching requests. Other responses are returned but not stored.
- While a request holds its key, a concurrent request with the same key receives `409` with code `IDEMPOTENCY_KEY_IN_PROGRESS`. The in-flight reservation lasts at most 300 seconds (`RESERVATION_TTL_SECONDS`).
- Storage is Redis through `ResilientIdempotencyStore`; when Redis is unreachable the store falls back to an in-process dictionary.

**Tenant isolation.** A resource that belongs to another organization is reported exactly like one that does not exist: research projects, executions, runs and reports return `404`. An execution authority that belongs to another organization is reported as `EXECUTION_AUTHORITY_UNAVAILABLE` with status `422`. See [authentication and tenancy](04-authentication-and-tenancy.md).

**OpenAPI documents.** `/openapi.json` is always mounted (FastAPI default). `/docs` and `/redoc` are served only when `DEBUG=true` (`FastAPI(docs_url=..., redoc_url=...)` in `src/api/main.py`). None of the three is in the public-route allowlist, so all require a bearer token.

## Authentication requirement

`AuthMiddleware` denies by default. A request must carry `Authorization: Bearer <access token>` unless its exact method and path appear in `PUBLIC_ROUTE_ALLOWLIST` (`src/middleware/auth_middleware.py`):

| Method | Path | Reason recorded in code |
|---|---|---|
| `GET` | `/health` | liveness and process health probe |
| `GET` | `/live` | liveness probe |
| `GET` | `/ready` | readiness probe |
| `GET` | `/ws/health` | WebSocket service health probe |
| `POST` | `/api/v1/auth/login` | authentication bootstrap |
| `POST` | `/api/v1/auth/register` | authentication bootstrap |
| `POST` | `/api/v1/auth/refresh` | authentication bootstrap |
| `POST` | `/api/v1/auth/forgot-password` | password recovery bootstrap |
| `POST` | `/api/v1/auth/reset-password` | password recovery bootstrap |
| `GET` | `/api/v1/auth/verify-email` | email verification bootstrap |

Every other path, including `/metrics`, `/docs`, `OPTIONS` requests that are not terminated by the CORS preflight handler and unknown paths, requires a valid token. A missing or malformed header returns `401` with code `AUTHENTICATION_REQUIRED`; an invalid or revoked token returns `401`; an unavailable key or revocation store returns `503` with code `AUTHENTICATION_SERVICE_UNAVAILABLE`.

The domain routers add `get_current_token` as a router-level or route-level dependency as well. Routers marked tenant-scoped below additionally resolve a `TenantContext` and return `403` when the token has no `organization_id` claim. WebSocket routes authenticate through a `token` query parameter instead of the header. See [authentication and tenancy](04-authentication-and-tenancy.md).

## Routers

| Prefix | Module | Router-level dependency | Tenant-scoped | Detail |
|---|---|---|---|---|
| none (`/health`, `/ready`, `/live`) | `src/api/routes/health.py` | none | no | [Health and metrics](#health-and-metrics) |
| `/api/v1/auth` | `src/api/auth/auth_router.py` | none; per-route as listed | no | [Auth](#auth) |
| `/api/v1/users` | `src/api/routes/users.py` | none; route uses `get_current_active_user` | no | [Users](#users) |
| `/api/v1/research` | `src/api/routes/research.py` | none; `get_tenant_context` on routes except `refine`, which has only the token dependency | yes | [Research projects](#research-projects) |
| `/api/v1/reports` | `src/api/routes/reports.py` | none; `get_tenant_context` on routes | yes | [Reports](#reports) |
| `/api/v1/query` | `src/api/routes/query_api.py` | `get_current_token` | per route | [Query](#query) |
| `/api/v1/agents` | `src/api/routes/agent_api.py` | `get_current_token` | per route | [Agents](#agents) |
| `/api/v1/masr` | `src/api/routes/masr_api.py` | `get_current_token` | no | [MASR](#masr) |
| `/api/v1/supervisors` | `src/api/routes/supervisor_api.py` | none; `get_current_token` on each HTTP route | per route | [Supervisors](#supervisors) |
| `/api/v1/talkhier` | `src/api/routes/talkhier_api.py` | none; `get_current_token` on each HTTP route | per route | [TalkHier](#talkhier) |
| `/api/v1` (`/runs/{run_id}/events`) | `src/api/routes/run_stream.py` | none; route uses `get_current_token` | yes | [Run events](#run-events) |
| none (`/ws...`) | `src/api/routes/websocket.py` | none; query-token dependency on WebSocket routes | per route | [WebSockets](#websockets) |

## Health and metrics

| Method | Path | Purpose | Success status | Notable errors |
|---|---|---|---|---|
| `GET` | `/health` | Returns `{"status": "healthy", "service": "research-platform-api"}`. | 200 | none |
| `GET` | `/ready` | Probes the database (`SELECT 1`) and Redis (`PING`), each with a 2-second timeout. Returns `{"status": "ready" \| "not_ready", "service", "checks": {"database", "redis"}}`, each check `ok` or `unavailable`. | 200 | 503 when either check is `unavailable` |
| `GET` | `/live` | Returns `{"status": "alive"}`. | 200 | none |
| `GET` | `/metrics` | Prometheus exposition from `prometheus_client.make_asgi_app()`. Requires a bearer token. | 200 | 401 |
| `GET` | `/ws/health` | WebSocket service statistics; see [Fixed responses](#fixed-responses). | 200 | none |

## Auth

All routes are mounted at `/api/v1/auth`. Behaviour, request fields and token handling are in [authentication and tenancy](04-authentication-and-tenancy.md).

| Method | Path | Purpose | Success status | Notable errors |
|---|---|---|---|---|
| `POST` | `/register` | Create an account and return user plus token pair. Public. | 201 | 400 password rejected, 409 email or username taken, 422, 503 |
| `POST` | `/login` | Authenticate with email and password. Public. | 200 | 401, 403 account disabled, 422, 429 locked out |
| `POST` | `/refresh` | Exchange a refresh token for a new token pair. Public. | 200 | 401, 503 |
| `POST` | `/logout` | Revoke the presented access token and its refresh record. | 204 | 401, 503 |
| `POST` | `/forgot-password` | Persist a reset token for an active account; the response does not reveal whether the account exists. Public. | 202 | 503 |
| `POST` | `/reset-password` | Set a new password from a reset token and revoke every session. Public. | 200 | 400, 404, 503 |
| `POST` | `/change-password` | Change the caller's password and revoke every session. | 200 | 400, 401, 404 |
| `GET` | `/verify-email` | Consume an email verification token (`token` query parameter). Public. | 200 | 400, 503 |
| `GET` | `/sessions` | List the caller's refresh sessions. | 200 | 401, 404 |
| `DELETE` | `/sessions/{device_id}` | End the refresh session registered for a device id. | 204 | 404 |
| `POST` | `/revoke-all` | Revoke every token issued to the caller up to the moment of the call. | 204 | 503 |
| `GET` | `/me` | Return the caller's profile. | 200 | 404 |

## Users

| Method | Path | Purpose | Success status | Notable errors |
|---|---|---|---|---|
| `DELETE` | `/api/v1/users/{user_id}/gdpr` | Erase a user's account and dependent rows. The caller must be that user or a superuser. | 200 | 403, 503 token store unavailable |

Behaviour and the rows deleted are in [authentication and tenancy](04-authentication-and-tenancy.md).

## Research projects

Mounted at `/api/v1/research`. Every route except `/projects/{project_id}/refine` resolves `get_tenant_context`, so the token needs an `organization_id` claim (otherwise `403`). `refine` depends only on `get_current_token`.

| Method | Path | Purpose | Success status | Notable errors |
|---|---|---|---|---|
| `POST` | `/projects` | Create a project. The route attempts to start execution without an organization id, which the authority check refuses, so execution does not start; see [research execution](05-research-execution.md#entry-points). | 201 | 403 `user_id` differs from the token subject, 422, 500 |
| `GET` | `/projects` | List the caller's projects (`user_id`, `status`, `limit` 1-100 default 10, `offset`). | 200 | 403 `user_id` differs from the token subject |
| `GET` | `/projects/{project_id}` | Return one project, refreshing its status from the in-process execution record. | 200 | 404 |
| `GET` | `/projects/{project_id}/progress` | Return a `ResearchProgress`. | 200 | 404 |
| `POST` | `/projects/{project_id}/cancel` | Mark the project cancelled and cancel an in-flight execution when one exists. | 204 | 404 |
| `POST` | `/projects/{project_id}/refine` | Not wired; returns 501. | 501 | 501 for any valid token |
| `GET` | `/projects/{project_id}/results` | Return persisted results, or the results of a completed in-process execution. | 200 | 404 when the project is missing or has no results yet |

`project_id` is a UUID.

**`POST /projects` request (`CreateResearchProjectRequest`).**

| Field | Type | Constraints |
|---|---|---|
| `title` | string | 1 to 200 characters |
| `query` | `ResearchQuery` | required |
| `user_id` | string | 1 to 255 characters; must equal the token subject |
| `scope` | `ResearchScope` or null | optional |
| `authority_reference` | object or null | optional |

The project row is created first. The route calls `start_research_execution` without an `organization_id`, so the start attempt is refused, the failure is logged and the project is returned with status `pending`; there is no error response for that case. `POST /api/v1/query/research` is the route that starts execution.

**`ResearchQuery`** (`src/models/research_project.py`).

| Field | Type | Constraints |
|---|---|---|
| `text` | string | at least 1 character; whitespace-only is rejected; stored stripped |
| `domains` | list of strings | at least one; blank entries are dropped |
| `depth_level` | string | `survey`, `comprehensive` (default) or `exhaustive` |

**`ResearchScope`.**

| Field | Type | Default |
|---|---|---|
| `depth_level` | string | `comprehensive` |
| `domains` | list of strings | empty |
| `time_period_start`, `time_period_end` | datetime or null | null |
| `languages` | list of strings | `["en"]` |
| `geographic_scope` | list of strings | empty |
| `max_sources` | integer | 50; 1 to 1000 |

**`ResearchProject` response.**

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | generated |
| `title` | string | 1 to 500 characters |
| `query` | `ResearchQuery` | |
| `user_id` | string | |
| `status` | enum | `pending`, `planning`, `in_progress`, `completed`, `failed`, `cancelled`; a stored draft is reported as `pending` |
| `scope` | `ResearchScope` or null | returned by create; null on reads |
| `plan` | object or null | |
| `created_at`, `updated_at` | datetime | |
| `started_at`, `completed_at` | datetime or null | |
| `error_message` | string or null | |
| `results` | object or null | |
| `metadata` | object | empty by default |

The `status` filter on `GET /projects` accepts the same enum; `planning` always returns an empty list because no stored row has that status.

**`ResearchProgress` response.**

| Field | Type | Default |
|---|---|---|
| `project_id` | UUID | |
| `total_tasks`, `completed_tasks`, `failed_tasks`, `in_progress_tasks`, `pending_tasks` | integer | 0 |
| `progress_percentage` | float | 0.0 |
| `estimated_time_remaining_seconds` | integer or null | null |
| `current_agent` | string or null | null |
| `current_agent_activities` | list of objects | empty |
| `last_updated` | datetime | current UTC time at construction |

For a project with an in-process execution the route reports four phases (`total_tasks` 4), `completed_tasks` as the progress percentage divided by 25, and `current_agent` as the execution's current phase. Without an execution record it reports one task, complete when the stored status is `completed`.

## Reports

Mounted at `/api/v1/reports`; every route is tenant-scoped, and every route answers `403` when the token's `sub` or `organization_id` is not a UUID. Generation, formats and storage are described in [reports](11-reports.md).

| Method | Path | Purpose | Success status | Notable errors |
|---|---|---|---|---|
| `POST` | `/generate` | Persist a pending report row and generate it in a background task. | 202 | 403 `user_id` differs from the token subject, 404 project not found, 500 |
| `GET` | `/statistics` | Aggregate counts and averages for the caller's reports. | 200 | 503, 500 |
| `GET` | `/{report_id}` | Return one report. | 200 | 404, 503 |
| `GET` | `/{report_id}/download/{format_type}` | Stream the report in one format as an attachment. | 200 | 404, 503 |
| `GET` | `` (`/api/v1/reports`) | List reports (`user_id`, `status_filter`, `report_type`, `page`, `page_size` 1-100 default 20). | 200 | 403, 503 |
| `POST` | `/search` | Search reports by term, type and minimum quality score. | 200 | 403, 503 |
| `DELETE` | `/{report_id}` | Delete a report. | 204 | 404, 503 |
| `GET` | `/{report_id}/integrity` | Verify the stored files of a report. | 200 | 503, 500 |

## Query

Mounted at `/api/v1/query`; `get_current_token` is a router-level dependency. Routes that start or read an execution also resolve `get_tenant_context`.

| Method | Path | Purpose | Success status | Notable errors |
|---|---|---|---|---|
| `POST` | `/research` | Start a MASR-routed execution and return the routing decision and execution status. | 200 | 422 authority error, 500 |
| `POST` | `/analyze` | Same flow with analysis options (`analysis_type`, `depth`). | 200 | 500 (including authority errors, which this route wraps) |
| `POST` | `/synthesize` | Same flow with synthesis options (`synthesis_focus`, `narrative_style`, `citation_style`). | 200 | 500 (including authority errors, which this route wraps) |
| `GET` | `/execution/{execution_id}/status` | Status of an execution in the caller's organization. | 200 | 404, 500 |
| `GET` | `/execution/{execution_id}/results` | Results of an execution in the caller's organization: `final_output` when completed, `{"error": "Execution failed", "details": [...]}` when failed, otherwise `{"status", "progress"}`. | 200 | 404, 500 |
| `POST` | `/execution/{project_id}/resume` | Resume from the latest recoverable checkpoint. | 200 | 400 invalid UUID, 404, 500 |
| `POST` | `/literature` | Convenience wrapper over `/research` (`query` of at least 10 characters, `max_sources` 10-200, `depth` default `comprehensive`). It builds the request without an `authority_reference`. | none | 422 `EXECUTION_AUTHORITY_REQUIRED`, 500 |
| `POST` | `/methodology` | Convenience wrapper over `/research` (`research_type`: `quantitative`, `qualitative`, `mixed`). It builds the request without an `authority_reference`. | none | 422 `EXECUTION_AUTHORITY_REQUIRED`, 500 |
| `POST` | `/comparison` | Convenience wrapper over `/research` (`comparison_focus`: `approaches`, `theories`, `methods`, `findings`). It builds the request without an `authority_reference`. | none | 422 `EXECUTION_AUTHORITY_REQUIRED`, 500 |
| `GET` | `/routing/strategies` | Fixed description of the five routing strategies; see [Fixed responses](#fixed-responses). | 200 | none |
| `GET` | `/routing/recommend` | Query-length-based recommendation; see [Fixed responses](#fixed-responses). | 200 | 500 |

`POST /research` (`IntelligentQueryRequest`) takes `query` (1 to 2000 characters), `domains`, `context`, `routing_strategy`, `quality_preference` and `cost_preference` (0 to 1), `enable_real_time_updates`, `timeout_seconds` (60 to 1800, default 300), `user_id`, `session_id` and `authority_reference`. The response (`IntelligentQueryResponse`) reads `routing_decision`, `supervisor_type`, `selected_agents`, `estimated_cost`, `estimated_quality`, `results` and `quality_scores` from the execution status returned by the kernel; `confidence` and `routing_time_ms` are never set and are returned as `null`. The project that the call creates takes its `user_id` from the request body (default `api_user`), not from the token; the organization comes from the token.

## Agents

Mounted at `/api/v1/agents`; `get_current_token` is a router-level dependency. The agent catalogue is described in [agents and supervisors](07-agents-and-supervisors.md).

| Method | Path | Tenant dependency | Purpose | Notable errors |
|---|---|---|---|---|
| `GET` | `` (`/api/v1/agents`) | `get_tenant_identity` | List agents with capabilities. | 500 |
| `GET` | `/{agent_type}` | none | Describe one agent. | 404, 500 |
| `POST` | `/{agent_type}/execute` | `get_tenant_context` | Execute one agent. Requires `authority_reference`. | 422, 500 |
| `POST` | `/chain` | `get_tenant_context` | Chain-of-agents (sequential). Requires `authority_reference`. | 422, 500 |
| `POST` | `/mixture` | `get_tenant_context` | Mixture-of-agents (parallel with aggregation). Requires `authority_reference`. | 422, 500 |
| `POST` | `/{agent_type}/validate` | none | Validate input for an agent type. | 500 |
| `GET` | `/{agent_type}/metrics` | `get_tenant_identity` | Metrics for one agent type. | 500 |
| `GET` | `/{agent_type}/health` | none | Health of one agent type. | 500 |
| `GET` | `/system/stats` | `get_tenant_identity` | Service statistics for the caller's organization. | 500 |
| `GET` | `/executions/active` | `get_tenant_identity` | Active executions of the caller's organization. | 500 |
| `POST` | `/literature-review/search` | `get_tenant_context` | Convenience wrapper over the literature-review agent. | 422, 500 |
| `POST` | `/citation/format` | `get_tenant_context` | Convenience wrapper over citation formatting. | 422, 500 |
| `POST` | `/synthesis/combine` | `get_tenant_context` | Convenience wrapper over synthesis. | 422, 500 |
| `POST` | `/workflows/literature-analysis` | `get_tenant_context` | Chain-of-agents literature workflow. | 422, 500 |
| `POST` | `/workflows/comprehensive-research` | `get_tenant_context` | Mixture-of-agents research workflow. | 422, 500 |
| `GET` | `/health/summary` | none | Health summary across agent types. | 500 |
| `GET` | `/performance/comparison` | `get_tenant_identity` | Performance comparison across agent types. | 500 |

`execute`, `chain` and `mixture` return `422` with code `EXECUTION_AUTHORITY_REQUIRED` when the body has no `authority_reference`, `EXECUTION_AUTHORITY_UNAVAILABLE` when no resolver can supply it for the caller's organization and the code of the authority error otherwise. `/literature-review/search`, `/workflows/literature-analysis` and `/workflows/comprehensive-research` accept `authority_id` and `authority_version` as query parameters instead. `/citation/format` and `/synthesis/combine` take them as fields of the JSON body (for example `{"sources": [...], "style": "APA", "authority_id": "...", "authority_version": "..."}`). Giving only one of the two returns `422` with `AUTHORITY_REFERENCE_INCOMPLETE`. The caller's organization comes from the token, never from the request body.

## MASR

Mounted at `/api/v1/masr`; `get_current_token` is a router-level dependency and no route is tenant-scoped. Routing behaviour is in [MASR routing](06-masr-routing.md). Routes return `503` when the application has no `masr_routing_service` on `app.state`.

| Method | Path | Purpose | Success status | Notable errors |
|---|---|---|---|---|
| `POST` | `/route` | Routing decision for a query. | 200 | 400 `INVALID_REQUEST`, 500 `ROUTING_ERROR` |
| `POST` | `/estimate-cost` | Cost estimate with breakdown. | 200 | 500 `ESTIMATION_ERROR` |
| `POST` | `/evaluate-strategies` | Compare routing strategies. | 200 | 500 `EVALUATION_ERROR` |
| `POST` | `/analyze-complexity` | Query complexity features. | 200 | 500 `ANALYSIS_ERROR` |
| `GET` | `/strategies` | Available routing strategies. | 200 | 500 `STRATEGY_LIST_ERROR` |
| `GET` | `/models` | Available models and tiers. | 200 | 500 `MODEL_LIST_ERROR` |
| `POST` | `/feedback` | Acknowledge feedback on a routing decision; see [Fixed responses](#fixed-responses). | 200 | 400 `INVALID_FEEDBACK`, 500 `FEEDBACK_ERROR` |
| `GET` | `/status` | Router health and metrics. | 200 | 500 `STATUS_ERROR` |

There is no MASR WebSocket route; the handler in `masr_api.py` is commented out.

## Supervisors

Mounted at `/api/v1/supervisors`. `get_current_token` is a dependency on every HTTP route; the column shows which routes also resolve a tenant context. Supervisors are described in [agents and supervisors](07-agents-and-supervisors.md).

| Method | Path | Tenant dependency | Purpose | Notable errors |
|---|---|---|---|---|
| `POST` | `/{supervisor_type}/execute` | `get_tenant_context` | Execute a task through one supervisor. | 400, 500 |
| `GET` | `` (`/api/v1/supervisors`) | none | List supervisors. | 500 |
| `GET` | `/{supervisor_type}` | none | Describe one supervisor. | 404, 500 |
| `GET` | `/{supervisor_type}/workers` | none | Workers managed by a supervisor. | 404, 500 |
| `POST` | `/{supervisor_type}/coordinate` | `get_tenant_context` | Coordinate specific workers for a task. | 400, 500 |
| `POST` | `/multi/orchestrate` | `get_tenant_context` | Orchestrate several supervisors. | 500 |
| `GET` | `/{supervisor_type}/stats` | none | Performance statistics. | 404, 500 |
| `GET` | `/{supervisor_type}/health` | none | Health of one supervisor. | 404, 500 |
| `POST` | `/optimize-allocation` | none | Optimize worker allocation. | 500 |
| `POST` | `/resolve-conflicts` | `get_tenant_context` | Resolve conflicting worker outputs. | 500 |
| `GET` | `/performance/compare` | none | Compare supervisors. | 500 |
| `POST` | `/experiment` | `get_tenant_context` | Run a coordination experiment. | 500 |

`supervisor_type` is a `SupervisorType` enum value; an unknown value is rejected by validation with `422`. A `ValueError` from the service answers `400` and any other failure `500`.

## TalkHier

Mounted at `/api/v1/talkhier`. `get_current_token` is a dependency on every HTTP route; sessions are owned by the caller's user and organization. The session routes answer `503` when `app.state.talkhier_session_service` is missing.

| Method | Path | Tenant dependency | Purpose | Notable errors |
|---|---|---|---|---|
| `POST` | `/sessions` | `get_tenant_context` | Start a refinement session. | 500 |
| `GET` | `/sessions/{session_id}` | `get_tenant_context` | Session status. | 404, 500 |
| `POST` | `/sessions/{session_id}/round` | `get_tenant_context` | Execute a refinement round. | 404, 500 |
| `POST` | `/sessions/{session_id}/consensus` | `get_tenant_context` | Check consensus among participants. | 404, 500 |
| `POST` | `/sessions/{session_id}/close` | `get_tenant_context` | Close a session. | 404, 500 |
| `GET` | `/protocols` | none | List protocol configurations. | none |
| `POST` | `/validate` | none | Validate a communication structure. | 500 |
| `GET` | `/analytics` | `get_tenant_context` | Protocol analytics. | 500 |
| `POST` | `/coordinate` | `get_tenant_context` | Coordinate several sessions. | 404, 500 |
| `GET` | `/health` | `get_tenant_context` | Service health. | none |

## Run events

| Method | Path | Purpose | Success status | Notable errors |
|---|---|---|---|---|
| `GET` | `/api/v1/runs/{run_id}/events` | Server-sent events for one run, read from the durable event log. `cursor` query parameter or `Last-Event-ID` header resumes from a position. | 200 | 400 invalid cursor, 403 token has no organization claim, 404 run not found in the caller's organization, 503 |

Each frame carries the cursor as the SSE `id`, the event type as `event` and the message as JSON `data`. Delivery is at least once; clients deduplicate on the frame's `idempotency_key`. Event semantics are in [durable runs and events](08-durable-runs-and-events.md).

## WebSockets

Every mounted WebSocket route runs `require_authenticated_websocket`, which reads only the `token` query parameter (a `Bearer ` prefix is stripped). An `Authorization` header alone does not authenticate a WebSocket. Authentication failures close the connection with code 1008; an unavailable authentication service closes with 1011.

| Path | Purpose |
|---|---|
| `/ws` | General connection; clients send subscription messages for projects and user events. |
| `/ws/projects/{project_id}` | Subscribe to one project's updates; denied unless the project belongs to the caller. |
| `/ws/cli/{project_id}` | Text-oriented project updates for the command-line client (`format` query parameter, default `text`). |
| `/ws/runs/{run_id}` | Durable run events with `cursor` resume; the token must carry an organization claim. |
| `/api/v1/supervisors/coordination/ws` | Worker coordination progress (`coordination_id` query parameter). |
| `/api/v1/supervisors/{supervisor_type}/ws` | Live updates for one supervisor (`client_id` query parameter). |
| `/api/v1/talkhier/sessions/{session_id}/live` | Live updates for one TalkHier session. |
| `/api/v1/talkhier/interactive` | Interactive TalkHier dialogue. |
| `/api/v1/talkhier/coordination` | Monitoring of coordinated sessions. |

Message contracts, cursors and delivery guarantees are in [durable runs and events](08-durable-runs-and-events.md).

## Errors

**Envelope.** Every error response built by the application has the body `{"error": {"code": <string>, "message": <string>, "details": <object>}}` (`build_error_payload` in `src/api/middleware/error_envelope.py`). `details` is an empty object when there is nothing to add.

**Status to default code.** `ERROR_CODES_BY_STATUS` supplies the code when a route raises `HTTPException` with a plain string or no code.

| Status | `error.code` |
|---|---|
| 400 | `BAD_REQUEST` |
| 401 | `AUTHENTICATION_REQUIRED` |
| 403 | `FORBIDDEN` |
| 404 | `NOT_FOUND` |
| 409 | `CONFLICT` |
| 422 | `VALIDATION_ERROR` |
| 429 | `RATE_LIMIT_EXCEEDED` |
| 500 | `INTERNAL_SERVER_ERROR` |
| any other (501, 503, ...) | `API_ERROR` |

**Forwarded codes.** When `HTTPException.detail` is an object, `http_exception_handler` reads `code` (or `error_code`) for the code, `message` (or `error`) for the message, and `details` for the details. A string detail becomes the message. An object without a message yields the message `Request failed`.

**Validation (422).** `validation_exception_handler` returns code `VALIDATION_ERROR`, message `Invalid request` and `details.errors` holding the field error list.

**Unhandled errors (500).** `global_exception_handler` in `src/api/main.py` returns code `INTERNAL_SERVER_ERROR` and message `Internal server error`; the exception is logged, not returned. Several route handlers catch exceptions themselves and return `500` with a message that includes the exception text.

**Client-visible codes.**

| Code | Status | Emitted by |
|---|---|---|
| `AUTHENTICATION_REQUIRED` | 401 | `AuthMiddleware`, and any `401` raised by a route |
| `AUTHENTICATION_SERVICE_UNAVAILABLE` | 503 | `AuthMiddleware` when the key store or revocation store cannot be reached |
| `RATE_LIMIT_EXCEEDED` | 429 | `RateLimitMiddleware`; the login lockout in `/api/v1/auth/login` |
| `IDEMPOTENCY_KEY_IN_PROGRESS` | 409 | `IdempotencyMiddleware` on `/api/v1/query` |
| `VALIDATION_ERROR` | 422 | request validation on every route |
| `EXECUTION_AUTHORITY_REQUIRED` | 422 | `/api/v1/agents` execution routes; the authority error raised by `/api/v1/query` routes |
| `EXECUTION_AUTHORITY_UNAVAILABLE` | 422 | same routes; also returned for an authority that belongs to another organization |
| `AUTHORITY_REFERENCE_INCOMPLETE` | 422 | `/api/v1/agents` convenience routes given only one of `authority_id` and `authority_version` |
| `INVALID_REQUEST` | 400 | `POST /api/v1/masr/route` on a `ValueError` |
| `ROUTING_ERROR` | 500 | `POST /api/v1/masr/route` |
| `ESTIMATION_ERROR` | 500 | `POST /api/v1/masr/estimate-cost` |
| `EVALUATION_ERROR` | 500 | `POST /api/v1/masr/evaluate-strategies` |
| `ANALYSIS_ERROR` | 500 | `POST /api/v1/masr/analyze-complexity` |
| `STRATEGY_LIST_ERROR` | 500 | `GET /api/v1/masr/strategies` |
| `MODEL_LIST_ERROR` | 500 | `GET /api/v1/masr/models` |
| `INVALID_FEEDBACK` | 400 | `POST /api/v1/masr/feedback` for an unknown routing id |
| `FEEDBACK_ERROR` | 500 | `POST /api/v1/masr/feedback` |
| `STATUS_ERROR` | 500 | `GET /api/v1/masr/status` |

MASR errors carry `error`, `error_code`, `details` and `suggestions` in the exception detail; the envelope keeps the code, message and `details`.

## Rate limiting

`RateLimitMiddleware` applies a fixed-window counter to every HTTP request outside its exempt paths.

- **Default limit.** `MAX_REQUESTS_PER_MINUTE` (default 100) per 60-second window. `ENABLE_RATE_LIMITING` (default `true`) switches the limiter off when `false`.
- **Exempt paths.** Any path that starts with `/health`, `/ready`, `/live`, `/metrics`, `/docs` or `/openapi.json`.
- **Key.** `user:<user id>` when authentication has set the caller identity, otherwise `ip:<client address>`. Unauthenticated requests (login, register) are therefore counted per client address.
- **Headers.** Allowed responses carry `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset`. `X-RateLimit-Reset` is the number of seconds until the window resets, not an epoch timestamp. A rejected request returns `429` with the same headers plus `Retry-After` (seconds) and `details` of `limit`, `remaining` and `reset_seconds`.
- **Storage.** Redis (`RedisRateLimitStore`); when Redis is unreachable the counters move to an in-process store (`ResilientRateLimitStore`), so each worker counts separately.
- **Client address.** `client_ip` in `src/api/client_ip.py` returns the socket peer. When the peer is inside a network listed in `TRUSTED_PROXIES` (empty by default, IPs or CIDRs), it walks `X-Forwarded-For` from the right and returns the first address that is not itself a trusted proxy. A malformed hop makes it return the peer. Entries to the left of that hop are never read.

The failed-login lockout is a separate counter described in [authentication and tenancy](04-authentication-and-tenancy.md).

## Fixed responses

These routes return values that do not come from live computation.

- `GET /api/v1/query/routing/recommend` chooses one of three constant recommendations by query length (under 100 characters `simple`, under 500 `moderate`, otherwise `complex`) and returns a constant `confidence` of 0.85. Costs, times and agent lists are literals in the handler.
- `GET /api/v1/query/routing/strategies` returns a literal description of five strategies (`speed_first`, `cost_efficient`, `quality_focused`, `balanced`, `adaptive`) and `default_strategy` `balanced`.
- `GET /ws/health` returns `websocket_manager.get_stats()` plus a fixed `timestamp` (see [durable runs and events](08-durable-runs-and-events.md#project-progress-websockets)).
- `POST /api/v1/research/projects/{project_id}/refine` always returns `501`.
- `POST /api/v1/masr/feedback` acknowledges a known routing id with `status: accepted`, `learning_updated: false`, `recorded: false` and `eligible: false`; the feedback values are discarded. An unknown routing id returns `400` `INVALID_FEEDBACK`.

## Source map

- `src/api/main.py`: application, middleware registration, router mounts, exception handlers, `docs_url`.
- `src/api/routes/`: one module per router: `health.py`, `users.py`, `research.py`, `reports.py`, `query_api.py`, `agent_api.py`, `masr_api.py`, `supervisor_api.py`, `talkhier_api.py`, `run_stream.py`, `websocket.py`.
- `src/api/auth/auth_router.py`: `/api/v1/auth` routes.
- `src/api/middleware/error_envelope.py`: error envelope, status-to-code map, handlers.
- `src/api/middleware/idempotency.py`: `Idempotency-Key` handling and stores.
- `src/api/middleware/rate_limiter.py`: fixed-window limiter and stores.
- `src/api/client_ip.py`: client address and `TRUSTED_PROXIES`.
- `src/middleware/auth_middleware.py`: `PUBLIC_ROUTE_ALLOWLIST`, `AuthMiddleware`, `get_current_token`.
- `src/middleware/tenant_context.py`: `TenantContext`, `get_tenant_context`, `get_tenant_identity`.
- `src/api/websocket/auth.py`: WebSocket token dependency and project access check.
- `src/models/research_project.py`: research request and response models.
- `src/core/config.py`: `DEBUG`, `CORS_ORIGINS`, `ENABLE_RATE_LIMITING`, `MAX_REQUESTS_PER_MINUTE`, `TRUSTED_PROXIES`.
