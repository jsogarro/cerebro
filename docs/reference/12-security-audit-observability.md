# Security, audit and observability

The API records an audit trail of requests, sets browser security headers on every response except genuine CORS preflights, which `CORSMiddleware` answers itself, redacts personal data from log lines and traces and exports Prometheus metrics. Langfuse tracing and context-compaction telemetry are off by default. Authentication, rate limiting and tenancy are covered in [authentication and tenancy](04-authentication-and-tenancy.md) and the [HTTP API](03-http-api.md).

## Summary

| Feature | Status | Entry point | Source |
|---|---|---|---|
| Request audit trail | Active | every route except the unaudited paths | `src/security/audit_middleware.py` (`AuditTrailMiddleware`) |
| Audit logger (buffer, flush, alerts) | Active | started in the application `lifespan` | `src/security/audit_logger.py` (`AuditLogger`) |
| Audit query and compliance report | Not wired | none | `src/security/audit_logger.py` (`AuditLogger.query_logs`, `AuditLogger.generate_compliance_report`) |
| Rejected-request log line | Active | every request | `src/security/audit_middleware.py` (`log_rejected_requests`) |
| Security response headers | Active | every response except CORS preflights answered by `CORSMiddleware` | `src/security/headers.py` (`SecurityHeadersMiddleware`), `src/security/security_headers.py` |
| Delimiter escaping of worker output | Active | research supervisor prompts | `src/security/content_sanitizer.py` (`escape_for_delimited_prompt`) |
| Prompt-injection sanitizer | Active | MCP tool path (default on), constraint pinning (`ENABLE_CONSTRAINT_PINNING=true`) | `src/security/content_sanitizer.py` (`ContentSanitizer`) |
| PII redaction for logs and traces | Active | log call sites, Langfuse trace input | `src/core/pii_redactor.py` (`redact_pii`) |
| Memory encryption | Not wired | none | `src/core/memory_encryption.py` (`MemoryEncryption`) |
| Client address resolution | Active | audit, rate limiting, auth, login | `src/api/client_ip.py` (`client_ip`) |
| Prometheus `/metrics` | Active | `GET /metrics` | `src/api/main.py`, `src/core/observability.py` |
| LLM cost drift middleware | Active | every request | `src/api/middleware/cost_drift.py` (`LLMCostDriftMiddleware`) |
| Health probes | Active | `/health`, `/live`, `/ready`, `/ws/health` | `src/api/routes/health.py`, `src/api/routes/websocket.py` |
| Langfuse tracing | Off by default | `LANGFUSE_ENABLED` | `src/core/tracing.py` |
| Context-compaction telemetry | Off by default | `ENABLE_CONTEXT_COMPACTION_TELEMETRY` | `src/core/telemetry.py` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Not wired | none | `src/core/config.py` (`Settings`) |
| Tracing verification script | Active | `python scripts/verify_tracing.py` | `scripts/verify_tracing.py` |
| Sanitizer probe script | Active | `python scripts/red_team_security_probe.py` | `scripts/red_team_security_probe.py` |

## Audit trail

`AuditLogger` is created in the application `lifespan` with the durable session factory and started there. It is stored as `app.state.audit_logger` and stopped, with a final buffer flush, on shutdown. The logger buffers events in memory (`buffer_size` 1000, `flush_interval` 5 seconds by default), and a background task flushes the buffer on the interval. A full buffer also triggers a flush. Each flush opens a fresh session from the factory and writes one `audit_logs` row per event.

`AuditTrailMiddleware` calls `AuditLogger.log_event` and then `flush_buffer`, so every recorded event is written before the middleware continues. It skips these paths: `/health`, `/health/`, `/ready`, `/live`, `/metrics` and `/favicon.ico`.

The middleware is registered inside authentication and rate limiting. A request that either layer rejects never reaches it and costs no audit write.

| Request | Records | If the audit write fails |
|---|---|---|
| Mutating: any method other than `GET`, `HEAD`, `OPTIONS`, plus `GET /api/v1/auth/verify-email` | An `admission` event and flush before the endpoint runs, then an `outcome` event and flush after the response | Admission failure returns `503` with error code `AUDIT_PERSISTENCE_UNAVAILABLE` and header `X-Audit-Status: unavailable`, and the endpoint does not run. Outcome failure keeps the response and adds `X-Audit-Status: unavailable`. |
| Safe: `GET`, `HEAD`, `OPTIONS` | An `outcome` event after the response | The response is returned with `X-Audit-Status: unavailable`. |

A successful outcome write adds `X-Audit-Status: persisted` to the response. When `app.state.audit_logger` is missing, a mutating request returns the same `503` and a safe request returns its response with `X-Audit-Status: unavailable`.

Each event takes its type from the path, method and status:

| Condition | `AuditEventType` |
|---|---|
| Path ends `/auth/login`, admission phase | `DATA_ACCESSED` |
| Path ends `/auth/login`, status below 400 / 400 or above | `LOGIN_SUCCESS` / `LOGIN_FAILED` |
| Path ends `/auth/logout` | `LOGOUT` |
| Status 401 or 403 | `UNAUTHORIZED_ACCESS` |
| Status 429 | `RATE_LIMIT_EXCEEDED` |
| Method `DELETE` | `DATA_DELETED` |
| Method `POST`, `PUT`, `PATCH` | `DATA_MODIFIED` |
| Anything else | `DATA_ACCESSED` |

Severity is `ERROR` for status 500 and above, `WARNING` for 401, 403 and 429, and `INFO` otherwise. The event metadata holds `method`, `path`, `phase` and `status_code`. Attribution comes from the authenticated request: `log_event` reads `request.state.user_id`, and `organization_id` from request state or the token payload goes into the event metadata. The `user_id` column is filled by a subquery against `users`, so a deleted user is stored as `NULL` and the original subject stays in the metadata key `subject`. A subject that is not a UUID is dropped by `_subject_uuid` before the event is built, so the column is `NULL` and the subject is not recorded. The client address comes from `client_ip` (see [Request limits and client address](#request-limits-and-client-address)).

The `audit_logs` table has one row per event. The `AuditEventType` enum has 45 members (login, account, MFA, OAuth, session, API key, permission, threat and data events) and `AuditSeverity` has `INFO`, `WARNING`, `ERROR` and `CRITICAL`.

### Alerts

After buffering an event, `AuditLogger` checks alert conditions and writes a `security_alerts` row when one holds:

- `SYSTEM_BREACH`, `DATA_EXFILTRATION`, `SQL_INJECTION_ATTEMPT`, `XSS_ATTEMPT` and `UNAUTHORIZED_ACCESS` events always create a `CRITICAL` alert.
- `LOGIN_FAILED` events that carry a user id create a `HIGH` alert at 5 failures in 15 minutes (threshold key `failed_login_attempts`).
- `RATE_LIMIT_EXCEEDED` events create a `MEDIUM` alert at 10 events in 5 minutes (threshold key `rate_limit_exceeded`), counted per user id or client address.

The other keys in the default threshold map (`suspicious_activity`, `unauthorized_access`, `data_exfiltration`, `sql_injection_attempt`, `xss_attempt`) are never read. `AlertType` has 26 members, `AlertSeverity` has `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` and `AlertStatus` has `NEW`, `ACKNOWLEDGED`, `INVESTIGATING`, `RESOLVED`, `FALSE_POSITIVE`, `ESCALATED`.

### Rejected requests

`log_rejected_requests` is registered after authentication, so it wraps the responses that authentication and rate limiting produce. For status 401, 403 or 429 it writes one `request_rejected` log line with the status, method, path and client host. It writes nothing to the database.

### Audit queries

> **Not wired.** `AuditLogger.query_logs` filters `audit_logs` and `AuditLogger.generate_compliance_report` summarises a date range, but no route or other production code outside `AuditLogger` calls either (`generate_compliance_report` calls `query_logs`). No HTTP route exposes audit logs or security alerts. `generate_compliance_report` produces a summary only for `compliance_type="gdpr"`.

## Security headers

`SecurityHeadersMiddleware` is defined in `src/security/headers.py` and imported through `src/security/security_headers.py`. `src/api/main.py` registers it with `hsts_enabled=settings.ENVIRONMENT != "development"`, so HSTS is sent only when `ENVIRONMENT` is not `development`. Every other option keeps its default: CSP on, nonce on, no report URI.

| Header | Value |
|---|---|
| `Content-Security-Policy` and `Content-Security-Policy-Report-Only` | The same policy in both: `default-src 'self'`; `script-src 'self' 'nonce-<nonce>' 'strict-dynamic'`; `style-src 'self' 'nonce-<nonce>'`; `img-src 'self' data: https:`; `font-src 'self' data:`; `connect-src 'self' https:`; `media-src 'self'`; `object-src 'none'`; `frame-ancestors 'none'`; `base-uri 'self'`; `form-action 'self'`; `upgrade-insecure-requests`; `block-all-mixed-content`. A new nonce is generated per request and stored on `request.state.csp_nonce`. |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains; preload` (not sent when `ENVIRONMENT` is `development`) |
| `X-Frame-Options` | `DENY` |
| `X-Content-Type-Options` | `nosniff` |
| `X-XSS-Protection` | `1; mode=block` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | 23 features; all are disabled (`=()`) except `autoplay`, `encrypted-media`, `fullscreen`, `picture-in-picture`, `publickey-credentials-get` and `sync-xhr`, which allow `self` |
| `X-Permitted-Cross-Domain-Policies` | `none` |
| `Cross-Origin-Embedder-Policy` | `require-corp` |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Cross-Origin-Resource-Policy` | `same-origin` |

The middleware also deletes `Server`, `X-Powered-By`, `X-AspNet-Version` and `X-AspNetMvc-Version` from the response when present. `Report-To` is added only when a report URI is configured, and `main.py` configures none.

## Content safety and redaction

**Delimiter escaping.** `escape_for_delimited_prompt` replaces `&`, `<` and `>` with entities so worker output cannot close the XML-style block it is placed in. `ResearchSupervisor` applies it to literature findings and methodology text before building a prompt.

> **On by default.** `ContentSanitizer.sanitize` neutralizes instruction-like text in external content: goal-hijacking phrases, delimiter and fake system-tag escapes, long runs of capitals and encoded payloads, replaced by markers such as `[INSTRUCTION_NEUTRALIZED]`. It runs when `MCPIntegration` is built for supervisor workers, which `MCP_TOOL_PATH_ENABLED` (default `true`) controls, and when `ENABLE_CONSTRAINT_PINNING=true` makes `BaseSupervisor` build a `ConstraintRegistry` that sanitizes each pinned constraint (default `false`).

`MCPIntegration` applies the sanitizer to the results of its tool specs in `src/agents/integrations/mcp_tool_specs.py`. `src/security/__init__.py` exports `ContentSanitizer`.

**PII redaction.** `redact_pii` (`PIIRedactor.redact`) replaces email addresses with `[EMAIL]`, US-style phone numbers with `[PHONE]`, `NNN-NN-NNNN` numbers with `[SSN]` and 13 to 19 digit runs (spaces and hyphens allowed between digits) with `[CREDIT_CARD]`. It is called where query text or error text reaches logs: the query, agent and talkhier routes (query previews), `MASRouter`, `QueryAnalyzer`, the OpenRouter provider (error text) and the Langfuse trace input.

> **Not wired.** `MemoryEncryption` encrypts memory payloads with Fernet using the key passed in or read from `MEMORY_ENCRYPTION_KEY`, and passes data through unchanged when no key is set. Its only consumers are `WorkingMemoryManager` and `EpisodicMemoryManager`, and no production code constructs the memory tiers. See [not wired](15-not-wired.md).

## Request limits and client address

Rate-limit behaviour and its response headers are documented in the [HTTP API](03-http-api.md), and failed-login lockout (`LOGIN_MAX_FAILED_ATTEMPTS` 5, `LOGIN_LOCKOUT_SECONDS` 900) in [authentication and tenancy](04-authentication-and-tenancy.md).

`client_ip` returns the socket peer address unless the peer is inside a network listed in `TRUSTED_PROXIES` (empty by default; entries must be IP addresses or networks). For a trusted peer it walks `X-Forwarded-For` from right to left and returns the first hop that is not itself a trusted proxy, or the peer when every hop is trusted. A malformed hop makes it return the peer. Hops to the left of the first untrusted hop are never read. The audit logger, the rate limiter, the authentication middleware and the auth router all use it.

## Metrics

`GET /metrics` serves the default Prometheus registry through an ASGI app mounted in `src/api/main.py`. It is not in the public-route allowlist, so a scrape needs a valid bearer token like any other protected route. Series defined in `src/core/observability.py`:

| Series | Type | Labels |
|---|---|---|
| `llm_call_duration_seconds` | Histogram | `model`, `provider` |
| `llm_tokens_total` | Counter | `model`, `provider`, `type` (`prompt`, `completion`) |
| `llm_cost_usd_total` | Counter | `model`, `provider` |
| `llm_request_cost_drift_ratio` | Histogram | `method`, `route` |
| `llm_cost_drift_events_total` | Counter | `method`, `route`, `direction` (`over`, `under`) |

`record_llm_call`, called by the base provider when it records a provider response, updates the first three series. Two more families are registered by the routing and plan-admission code:

| Series | Type | Labels |
|---|---|---|
| `cerebro_masr_outcomes_total` | Counter | `source`, `application_status`, `eligible` |
| `cerebro_masr_outcome_metric_availability_total` | Counter | `metric`, `availability` |
| `cerebro_masr_adaptive_effective_state` | Gauge | `state` (`disabled`, `fixture_off`, `cold`, `degraded`, `active`) |
| `cerebro_execution_plan_admissions_total` | Counter | `collaboration_mode`, `result` |
| `cerebro_execution_plan_rejections_total` | Counter | `code` |

These families are defined in `src/ai_brain/router/routing_observability.py` and `src/ai_brain/integration/execution_plan_observability.py`. The app exports no `http_requests_total`, `http_request_duration_seconds` or `memory_usage_bytes` series.

### LLM cost drift

`LLMCostDriftMiddleware` opens a request-scoped cost tracker for each request. The research handler in `src/api/routes/query_api.py` records the MASR estimate with `set_llm_request_estimated_cost` when the response carries an `estimated_cost`, and each `record_llm_call` adds the actual provider cost. After the response, `record_llm_request_cost_drift` computes `|actual - estimate| / estimate` when both are greater than zero, observes it in `llm_request_cost_drift_ratio`, and, above the threshold of `0.2`, increments `llm_cost_drift_events_total` and logs `llm_request_cost_drift_detected` with the direction.

## Health probes

All four probes are in the public-route allowlist.

| Route | Response |
|---|---|
| `GET /health` | `200` `{"status": "healthy", "service": "research-platform-api"}` |
| `GET /live` | `200` `{"status": "alive"}` |
| `GET /ready` | `200` `{"status": "ready", "service": ..., "checks": {"database": "ok", "redis": "ok", "audit_logger": "ok", "jwt_keys": "ok"}}` when a `SELECT 1` on Postgres, a `PING` on Redis, a running audit-logger flush task and a loadable JWT key pair each succeed within 2 seconds. Otherwise `503` with `"status": "not_ready"` and `"unavailable"` for each failed check. |
| `GET /ws/health` | `200` `{"status": "healthy", "websocket_stats": ..., "timestamp": ...}`. `websocket_stats` comes from the connection manager. `timestamp` is a fixed string, not the current time (see [durable runs and events](08-durable-runs-and-events.md#project-progress-websockets)). |

## Tracing

> **Off by default.** With `LANGFUSE_ENABLED=true`, Langfuse tracing records MASR routing decisions as `span` observations and OpenRouter provider calls as nested `generation` observations. It also needs `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`; `LANGFUSE_HOST` is optional and defaults to Langfuse cloud.

`src/core/tracing.py` builds the client lazily and every Langfuse call is guarded, so a tracing failure never reaches the caller: with tracing disabled, keys missing or the SDK failing, the context managers yield `None` and the traced code runs unchanged. The trace input is the query after `redact_pii`, cut to 500 characters, and the trace id is derived from the query id. The `lifespan` in `src/api/main.py` pre-initialises the client in a worker thread when `LANGFUSE_ENABLED` is true and, on shutdown, calls `shutdown_langfuse` with a 5 second timeout to flush pending traces and stop the export threads.

`python scripts/verify_tracing.py` routes one query through `MASRouter`. With tracing disabled it checks that no client is created and exits non-zero if one is. With tracing enabled, a `None` client only prints a warning and the script still ends with `Verification PASSED` and exit status 0; only an exception raised while routing exits non-zero.

> **Off by default.** Context-compaction telemetry logs token counts: `DirectExecutionService` logs `domain_output_token_measurement` events, and `BaseSupervisor`, `WorkingMemoryManager` and `MultiTierMemorySystem` each measure token counts behind the same flag, `ENABLE_CONTEXT_COMPACTION_TELEMETRY=true`. Counting needs `tiktoken`; without it the count is 0.

> **Not wired.** `OTEL_EXPORTER_OTLP_ENDPOINT` (default `http://localhost:4317`) is declared in `Settings` and read by no code under `src/`. Setting it changes nothing. See [configuration](02-configuration.md).

## Security probe script

`python scripts/red_team_security_probe.py` runs three checks and exits `0` when all pass, `1` otherwise.

- **Sanitization:** three injection payloads (goal hijacking, delimiter escape, base64) must each be modified by `ContentSanitizer` and a benign academic title must pass unchanged.
- **Provenance:** `TalkHierContent` defaults to `ProvenanceType.LLM_GENERATED` and keeps an explicit `EXTERNAL_WEB` source type and provenance chain.
- **Delimited feedback:** a hard-coded `<REVISION_FEEDBACK>` template contains the malicious text inside its delimiters and places the warning after the block. This check inspects a string defined in the script, not the supervisor code.

## Source map

- `src/security/audit_middleware.py`: `AuditTrailMiddleware`, `log_rejected_requests`
- `src/security/audit_logger.py`: `AuditLogger`: buffering, flush, alerts, query and compliance methods
- `src/security/headers.py`: `SecurityHeadersMiddleware`; `src/security/security_headers.py` re-exports it
- `src/security/content_sanitizer.py`: `ContentSanitizer`, `escape_for_delimited_prompt`
- `src/security/login_throttle.py`: failed-login lockout counter
- `src/core/pii_redactor.py`: `PIIRedactor`, `redact_pii`
- `src/core/memory_encryption.py`: `MemoryEncryption`
- `src/api/client_ip.py`: `client_ip`, `TRUSTED_PROXIES` handling
- `src/core/observability.py`: LLM metrics and request cost tracking
- `src/api/middleware/cost_drift.py`: `LLMCostDriftMiddleware`
- `src/ai_brain/router/routing_observability.py`, `src/ai_brain/integration/execution_plan_observability.py`: routing and plan-admission metrics
- `src/core/tracing.py`: Langfuse client and trace context managers
- `src/core/telemetry.py`: compaction telemetry helpers
- `src/api/routes/health.py`: `/health`, `/ready`, `/live`; `src/api/routes/websocket.py`: `/ws/health`
- `src/models/db/audit_log.py`, `src/models/db/security_alert.py`: `audit_logs` and `security_alerts` tables and enums
- `src/api/main.py`: middleware order, audit logger and Langfuse lifecycle, `/metrics` mount
- `scripts/verify_tracing.py`, `scripts/red_team_security_probe.py`: operator scripts
