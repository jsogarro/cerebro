# Error Code Reference

This document describes error handling in **Cerebro** (the multi-agent LLM research platform; current focus: financial research on US equities). The FastAPI service ships under the legacy infra identity "Research Platform API", so log records, container names, and image tags still say `research-platform` / `research_db` — that is deployment naming, not the product name.

## Framing: what is real vs. what is proposed

This reference has two clearly separated parts. Read the framing before relying on any code below.

1. **The actual error surface (implemented, authoritative).** Cerebro emits HTTP status codes plus a single JSON error envelope produced by `src/api/middleware/error_envelope.py`. These are the codes and shapes you will actually see in a live response. This is the source of truth.
2. **A proposed error-code taxonomy (NOT implemented).** The `{SERVICE}-{CATEGORY}-{NUMBER}` scheme (e.g. `API-AUT-001`, `AGT-EXE-003`) is a *target standard for a future release*. **No code in `src/` emits these codes today** — there is no `ResearchPlatformError` class, no `ERROR_RESOLUTIONS` table, and no per-subsystem structured code registry. Treat this part as a design proposal, not documentation of current behavior. Do not build clients that expect these strings.

## Table of Contents
- [Actual Error Surface (implemented)](#actual-error-surface-implemented)
- [HTTP Status Codes](#http-status-codes)
- [Proposed Error-Code Taxonomy (not implemented)](#proposed-error-code-taxonomy-not-implemented)
  - [Application Error Codes](#application-error-codes)
  - [Agent Error Codes](#agent-error-codes)
  - [Workflow Error Codes](#workflow-error-codes)
  - [Database Error Codes](#database-error-codes)
  - [External Service Error Codes](#external-service-error-codes)
- [Troubleshooting Guide](#troubleshooting-guide)

## Actual Error Surface (implemented)

Every API error is rendered through one JSON envelope. Three exception handlers are registered on the app (`src/api/main.py`): `HTTPException`, `RequestValidationError`, and a catch-all `Exception` handler. All three call `build_error_payload` (`src/api/middleware/error_envelope.py`), so the body is always:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Project not found",
    "details": {}
  }
}
```

- `code` — a stable string constant (see table below), or a custom code supplied by an endpoint via `HTTPException(detail={"code": ...})`.
- `message` — a human-readable description.
- `details` — an object (`{}` when absent). Validation errors put the field-level list under `details.errors`.

### Default code for each status

`ERROR_CODES_BY_STATUS` (`src/api/middleware/error_envelope.py:10-19`) maps HTTP status to the default `code` string. When an endpoint raises `HTTPException` with a plain string or no explicit code, this is what the client receives:

| HTTP status | `error.code` |
|------|---------|
| 400 | `BAD_REQUEST` |
| 401 | `AUTHENTICATION_REQUIRED` |
| 403 | `FORBIDDEN` |
| 404 | `NOT_FOUND` |
| 409 | `CONFLICT` |
| 422 | `VALIDATION_ERROR` |
| 429 | `RATE_LIMIT_EXCEEDED` |
| 500 | `INTERNAL_SERVER_ERROR` |
| any other | `API_ERROR` (fallback) |

Notes on real behavior:
- **Validation errors** always return HTTP 422 with `code: "VALIDATION_ERROR"` and the Pydantic/FastAPI error list under `details.errors`.
- **Unhandled exceptions** return HTTP 500 with `code: "INTERNAL_SERVER_ERROR"` and a generic message (internal details are logged via `structlog`, never leaked to the client).
- **Custom codes** are possible: an endpoint may raise `HTTPException(detail={"code": "SOMETHING", "message": "...", "details": {...}})` and the handler forwards those verbatim (`_detail_to_error`). These are ad-hoc and not centrally registered.

## HTTP Status Codes

These are the transport-level codes Cerebro uses. They are accurate and stable.

### 2xx Success Codes

| Code | Message | Description |
|------|---------|-------------|
| 200 | OK | Request successful |
| 201 | Created | Resource created successfully |
| 202 | Accepted | Request accepted for processing (async execution started) |
| 204 | No Content | Request successful, no response body |

### 4xx Client Error Codes

| Code | Message | Common Causes | Resolution |
|------|---------|---------------|------------|
| 400 | Bad Request | Invalid request syntax, malformed JSON | Validate request format and parameters |
| 401 | Unauthorized | Missing or invalid JWT on a protected endpoint | Obtain/refresh a token from `/api/v1/auth` |
| 403 | Forbidden | Insufficient permissions | Verify user permissions for the resource |
| 404 | Not Found | Resource or endpoint doesn't exist | Check resource ID and endpoint path |
| 409 | Conflict | Duplicate resource or state conflict | Resolve the conflicting resource |
| 422 | Unprocessable Content | Request body failed validation | Fix the fields listed in `details.errors` |
| 429 | Too Many Requests | Global rate limit exceeded (100 req/min) | Wait before retrying |

> Auth note: only endpoints that declare `Depends(get_current_user/...)` are actually protected (auth, GDPR delete, parts of research/reports). `AuthMiddleware` is a no-op, so `/api/v1/query`, `/api/v1/agents`, and `/api/v1/masr` are effectively unauthenticated in the current build and will not return 401.

> Rate-limit note: rate limiting is a single global limiter (`MAX_REQUESTS_PER_MINUTE=100`, `ENABLE_RATE_LIMITING=True`). There are no per-endpoint tiers, burst allowances, or per-plan quotas.

### 5xx Server Error Codes

| Code | Message | Common Causes | Resolution |
|------|---------|---------------|------------|
| 500 | Internal Server Error | Unhandled server error | Check server logs and report bug |
| 502 | Bad Gateway | Upstream service unavailable | Check dependent service status |
| 503 | Service Unavailable | Service temporarily down | Wait and retry, check service status |
| 504 | Gateway Timeout | Upstream service timeout | Check network and service health |

---

## Proposed Error-Code Taxonomy (not implemented)

> **Everything below this line is a design proposal.** These structured codes are **not emitted by any code path today**. They document an intended future taxonomy so that error semantics can be standardized later. Until they ship, clients see only the [actual error surface](#actual-error-surface-implemented) above.

The proposed format is `{SERVICE}-{CATEGORY}-{NUMBER}`:

- **SERVICE**: Component that generated the error (API, AGT, WFL, DB, EXT)
- **CATEGORY**: Error category (AUT, VAL, CON, EXE, etc.)
- **NUMBER**: Unique identifier within the category (001-999)

**Examples:** `API-AUT-001` (API Authentication error #1), `AGT-EXE-003` (Agent Execution error #3), `WFL-LNG-001` (LangGraph workflow error #1).

### Application Error Codes

#### Authentication Errors (API-AUT-xxx)

Cerebro auth is JWT **RS256**, 15-minute access / 7-day refresh, keys at `/secrets/jwt_private.pem` and `/secrets/jwt_public.pem`, bcrypt (12 rounds), `PASSWORD_MIN_LENGTH=12`. MFA is disabled by default (`ENABLE_MFA=False`).

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| API-AUT-003 | JWT token invalid | JWT signature verification failed | Re-authenticate to get a new token |
| API-AUT-004 | JWT token expired | Access token has expired (15-min lifetime) | Refresh via `/api/v1/auth/refresh` or re-authenticate |
| API-AUT-005 | Invalid credentials | Email/password incorrect | Check credentials and try again |
| API-AUT-006 | Account locked | Too many failed login attempts | Wait for the lockout period or contact an admin |
| API-AUT-008 | Session expired | Refresh token expired (7-day lifetime) | Log in again |

> MFA-specific codes are intentionally omitted: MFA is off by default in this build.

#### Validation Errors (API-VAL-xxx)

In the current implementation these all surface as HTTP 422 `VALIDATION_ERROR` with per-field detail under `details.errors`. The finer-grained codes below are proposed sub-classifications.

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| API-VAL-001 | Missing required field | Required field not provided | Include all required fields |
| API-VAL-002 | Invalid field format | Field format doesn't match expected | Check field format requirements |
| API-VAL-005 | Invalid enum value | Value not in allowed enum list | Use one of the allowed enum values |
| API-VAL-006 | Invalid UUID format | UUID/ID format is incorrect | Provide a valid UUID |
| API-VAL-007 | Invalid email format | Email format is incorrect | Provide a valid email address |
| API-VAL-009 | Value out of range | Numeric value outside allowed range | Use a value within the specified range |

#### Connection Errors (API-CON-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| API-CON-001 | Database connection failed | Cannot connect to Postgres | Check database server status and credentials |
| API-CON-002 | Redis connection failed | Cannot connect to Redis | Check Redis server status and configuration |
| API-CON-004 | Provider API unavailable | Upstream LLM provider not responding | Check provider status (Gemini by default) |
| API-CON-005 | Connection pool exhausted | No available database connections | Increase pool size or check for connection leaks |
| API-CON-006 | Network timeout | Network operation timed out | Check connectivity and increase timeout |

> There is no Temporal connection error: Temporal was removed and replaced by the in-process `DirectExecutionService` (`src/api/services/direct_execution_service.py`). `TEMPORAL_HOST`/`TEMPORAL_NAMESPACE` are dead vestigial settings.

#### Rate Limiting Errors (API-RTE-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| API-RTE-001 | Rate limit exceeded | More than 100 requests in the 1-minute window | Wait before making more requests |

> Cerebro has a single global limiter only. Per-plan quotas and per-endpoint concurrency limits (proposed as `API-RTE-002`/`003` in earlier drafts) do not exist.

### Agent Error Codes

Domain workers subclass `LLMWorkerAgentBase` — they are **LLM-reasoning (prompt-driven)** agents, not coded decision engines, and they do not call external data sources. Confidence scores attached to results are hardcoded heuristics (0.85 success / 0.3 empty / 0.8 fast-path), not measured quality signals. The one deterministic exception is the finance-math tool (`financial_calculator`), which runs pure functions with no LLM and no external data.

#### Execution Errors (AGT-EXE-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| AGT-EXE-001 | Agent initialization failed | Worker failed to initialize | Check agent configuration and dependencies |
| AGT-EXE-002 | Agent execution timeout | Worker exceeded its time budget | Increase timeout or simplify the query |
| AGT-EXE-004 | Invalid agent input | Input doesn't match the worker's expected task | Validate input data format and content |
| AGT-EXE-006 | Agent validation failed | Output failed the verification QA gate | Review the query; the verifier may trigger a revision pass |

> The verification worker is a cross-cutting QA gate (initial attempt + at most one revision, `MAX_VERIFICATION_REVISION_ROUNDS=2`); a failed gate can trigger one revision before the result is returned with a downgraded quality signal.

#### Literature Review Agent Errors (AGT-LIT-xxx)

The literature-review worker is **LLM-driven** — it reasons over the prompt; it does **not** query academic databases, journal APIs, or DOI resolvers. Codes here concern the query and the model output only.

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| AGT-LIT-001 | Search query invalid | The literature query is malformed or empty | Refine the query |
| AGT-LIT-002 | No results found | The model produced no usable findings | Broaden the topic or rephrase |
| AGT-LIT-004 | Parse error | Failed to parse the structured model output | Retry; check the agent's Pydantic schema |

#### Comparative Analysis Agent Errors (AGT-CMP-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| AGT-CMP-001 | Insufficient data | Not enough input for a comparison | Provide more input context |
| AGT-CMP-003 | Analysis failed | Comparative analysis step failed | Check input quality and format |

#### Methodology Agent Errors (AGT-MTH-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| AGT-MTH-001 | Invalid research type | Research type not supported | Use a supported methodology type |
| AGT-MTH-002 | Methodology conflict | Conflicting methodology requirements | Resolve conflicts in the input |

#### Synthesis Agent Errors (AGT-SYN-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| AGT-SYN-001 | Conflicting findings | Cannot reconcile conflicting inputs | Review inputs for inconsistencies |
| AGT-SYN-002 | Synthesis failed | Synthesis step failed | Check input completeness |
| AGT-SYN-003 | Output too large | Output exceeds size limits | Reduce input scope |

#### Citation Agent Errors (AGT-CIT-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| AGT-CIT-001 | Citation format invalid | Citation doesn't match the requested style | Use a supported citation style |
| AGT-CIT-002 | Source not verifiable | Referenced source cannot be checked | The citation agent is LLM-based and cannot confirm external sources |

### Workflow Error Codes

Cerebro coordinates work through **domain supervisors** (Research, Content, Analytics, Finance), each of which runs its own internal **LangGraph `StateGraph`**. There is no longer a top-level orchestrator: the standalone `src/orchestration/` subsystem was deleted. Coordination now lives entirely inside per-supervisor graphs and the `MASRSupervisorBridge`.

#### LangGraph Workflow Errors (WFL-LNG-xxx)

LangGraph is a hard dependency and every supervisor builds a `StateGraph`; these codes cover graph-level failures inside a supervisor.

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| WFL-LNG-001 | Graph validation failed | A supervisor's workflow graph has an invalid structure | Fix the graph definition and node wiring |
| WFL-LNG-002 | Node execution failed | A graph node (worker step) failed | Check the node implementation and its inputs |
| WFL-LNG-003 | State corruption | Workflow state is inconsistent | Reset the execution or restart |
| WFL-LNG-004 | Checkpoint failed | Failed to persist a workflow checkpoint | Check Postgres/`CheckpointRepository` and retry |
| WFL-LNG-005 | Recovery failed | Failed to resume from a checkpoint | Verify checkpoint integrity; see `/execution/{id}/resume` |

#### Supervisor Coordination Errors (WFL-ORC-xxx)

These replace the old "orchestration" codes. They cover routing/coordination across supervisors — MASR routing (`MASRouter`), the `MASRSupervisorBridge`, and concurrent multi-domain execution.

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| WFL-ORC-001 | Supervisor coordination failed | The bridge could not dispatch a routing decision to a supervisor | Check the routing decision and the supervisor registry |
| WFL-ORC-002 | Unmapped domain | A routed domain has no supervisor class (e.g. the `service` domain) | Request falls back to the Research supervisor; map or remove the domain |
| WFL-ORC-003 | Multi-domain partial failure | One or more domain subqueries failed during concurrent execution | Partial results are returned with warnings; inspect per-domain output |

### Database Error Codes

Cerebro uses **Postgres** via async **SQLAlchemy**, with **Alembic** migrations. These codes are accurate to the data layer.

#### Connection Errors (DB-CON-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| DB-CON-001 | Connection failed | Cannot establish a database connection | Check the database server and credentials |
| DB-CON-002 | Connection timeout | Database connection timed out | Increase the timeout or check the network |
| DB-CON-003 | Pool exhausted | The async connection pool has no free connections | Increase pool size or check for leaks |
| DB-CON-004 | Authentication failed | Database authentication failed | Verify `DATABASE_URL` credentials |

#### Query Errors (DB-QRY-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| DB-QRY-001 | Syntax error | SQL statement has a syntax error | Fix the query |
| DB-QRY-002 | Constraint violation | A database constraint was violated | Adjust the data to satisfy constraints |
| DB-QRY-003 | Deadlock detected | A transaction deadlock occurred | Retry the transaction |
| DB-QRY-004 | Transaction rolled back | The transaction was rolled back | Check transaction logic and retry |
| DB-QRY-005 | Table not found | A referenced table doesn't exist | Run Alembic migrations |
| DB-QRY-006 | Column not found | A referenced column doesn't exist | Run migrations or check the column name |

#### Migration Errors (DB-MIG-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| DB-MIG-001 | Migration failed | An Alembic migration failed | Check the migration script and database state |
| DB-MIG-002 | Version conflict | Migration revision conflict | Resolve the revision graph manually |
| DB-MIG-003 | Rollback failed | A downgrade failed | Manually restore the database state |

### External Service Error Codes

The only external LLM provider active by default is **Gemini** (`GEMINI_DEFAULT_MODEL=gemini-pro`). OpenRouter multi-provider routing (DeepSeek for simple tiers, Claude Sonnet for complex) is **flag-gated OFF** — it requires both `MULTI_PROVIDER_ROUTING_ENABLED=True` and `OPENROUTER_API_KEY`. `DEEPSEEK_ENABLED`, `LLAMA_ENABLED`, and `OPENROUTER_ENABLED` all default to `False`.

> There are **no academic-database error codes**. Cerebro has no academic-database, journal, or DOI integrations; the literature-review agent is LLM-driven and the QA/fact-check paths (`src/qa`, apart from `mast.py`) are stubs that return empty results. Any earlier `EXT-ACD-xxx` codes described integrations that do not exist and have been removed.

#### Gemini API Errors (EXT-GEM-xxx)

| Code | Message | Description | Resolution |
|------|---------|-------------|------------|
| EXT-GEM-001 | API key invalid | The Gemini API key is invalid | Verify and update `GEMINI_API_KEY` |
| EXT-GEM-002 | Quota exceeded | Gemini API quota exceeded | Wait for the quota reset or raise the limit |
| EXT-GEM-003 | Request too large | Request exceeds Gemini size limits | Reduce the request size |
| EXT-GEM-004 | Model not found | The requested model is unavailable | Use an available model |
| EXT-GEM-005 | Rate limit exceeded | Too many requests to the Gemini API | Back off and retry (exponential backoff) |
| EXT-GEM-006 | Content filtered | Content blocked by Gemini safety filters | Adjust the content |
| EXT-GEM-007 | Service unavailable | Gemini temporarily unavailable | Retry with exponential backoff |

---

## Troubleshooting Guide

### Error Investigation Steps

1. **Read the JSON envelope** — the `error.code`, `error.message`, and `error.details` fields are the primary signal (see [Actual Error Surface](#actual-error-surface-implemented)).
2. **Check the HTTP status** — it tells you the class of problem (auth, validation, rate limit, server).
3. **Check the logs** — the service logs structured records via `structlog`; 500s log the full exception server-side while returning a generic message.
4. **Verify configuration** — most connection failures are misconfigured `DATABASE_URL`, `REDIS_URL`, or a missing `GEMINI_API_KEY`.
5. **Check dependencies** — confirm Postgres and Redis are up.

### Common Resolution Patterns

#### Authentication Issues

There is **no auth CLI group** — the `research-cli` command tree is `config`, `health`, `completion`, `agents`, and `projects`. Tokens are issued by the auth API, not the CLI.

```bash
# Show current CLI configuration (valid: `config show`)
research-cli config show

# Check API health from the CLI
research-cli health

# Obtain a token from the auth API (JWT RS256; 15-min access / 7-day refresh)
# Access tokens come from POST /api/v1/auth/login; refresh via /api/v1/auth/refresh.

# Call a protected endpoint with the token
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/auth/me
```

> Note: `/api/v1/query`, `/api/v1/agents`, and `/api/v1/masr` are effectively unauthenticated in the current build, so a 401 there is unexpected.

#### Connection Issues

```bash
# Check service status
docker-compose ps

# Test database connection (Postgres 16)
docker-compose exec postgres pg_isready

# Test Redis connection (Redis 7)
docker-compose exec redis redis-cli ping

# Check API reachability
curl http://localhost:8000/health
```

#### Resource Issues

```bash
# Check disk space
df -h

# Check memory usage
free -h

# Check container resources
docker stats
```

### Error Response Envelope (implementation)

All error responses are built by a single helper — `build_error_payload` in `src/api/middleware/error_envelope.py`. There is no custom exception hierarchy or resolution-lookup table in the codebase; endpoints raise FastAPI's `HTTPException` (optionally with a `detail` dict carrying a custom `code`), and the registered handlers render the envelope.

```python
# src/api/middleware/error_envelope.py
def build_error_payload(
    *,
    code: str,
    message: str,
    details: Any | None = None,
) -> dict[str, dict[str, Any]]:
    """Build the standard API error response body."""
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        }
    }
```

To return a domain-specific error from an endpoint:

```python
from fastapi import HTTPException

raise HTTPException(
    status_code=404,
    detail={"code": "NOT_FOUND", "message": "Project not found", "details": {}},
)
```

### Structured Logging

Errors are logged with `structlog`. A 500 logs the unhandled exception with request path and method (`src/api/main.py:233`), for example:

```json
{
  "event": "Unhandled exception",
  "level": "error",
  "path": "/api/v1/query/research",
  "method": "POST",
  "exception": "..."
}
```

### Metrics & Monitoring

LLM-level observability is exposed as **Prometheus** metrics at `GET /metrics` (`src/core/observability.py`). The real metric names are:

- `llm_call_duration_seconds`
- `llm_tokens_total`
- `llm_cost_usd_total`
- `llm_request_cost_drift_ratio`
- `llm_cost_drift_events_total`

`LLMCostDriftMiddleware` compares MASR-estimated vs. actual provider cost and emits a warning when drift exceeds 0.2. **Langfuse tracing** is opt-in (`LANGFUSE_ENABLED`, default `False`). There is no OpenTelemetry backbone, Grafana/Loki/Jaeger stack, or Sentry integration in this build — do not configure alerts against metrics that are not in the list above.
