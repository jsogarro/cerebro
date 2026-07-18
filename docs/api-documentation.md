# Cerebro API Documentation

Canonical top-level inventory of Cerebro's HTTP and WebSocket surface.

Cerebro is a multi-agent LLM intelligence platform. Its current product focus is
**financial research (US equities)**. Natural-language queries are routed through
the in-process **MASR** (Multi-Agent System Router) to hierarchical domain
supervisors — Research, Content, Analytics, Finance — which coordinate specialist
LLM worker agents.

> **Infra naming.** The deployment identity is still the pre-rebrand
> `research-platform`: the FastAPI application title is `Research Platform API`,
> the health service field is `research-platform-api`, images are
> `research-platform-api`, the database is `research_db`, and the CLI entrypoints
> are `research-platform` / `research-cli`. These literal names are kept verbatim
> below where they are the actual infra artifact; the product name is Cerebro.

This page is an inventory and links out to the deep-dive references rather than
duplicating them:

- **Agent (bypass) API** — `docs/api/agent-api-reference.md`
- **MASR routing API** — `docs/api/masr-api-guide.md`

---

## Base URL

```
http://localhost:8000/api/v1
```

There is no hosted production endpoint. All examples target the local server.
`/docs` (Swagger) and `/redoc` are served only when `DEBUG=True`, which is off by
default.

## Request flow

```
Client -> FastAPI -> DirectExecutionService (asyncio background task)
       -> MASRouter -> MASRSupervisorBridge -> domain supervisors
       -> workers -> verification QA gate
```

Execution is fully in-process. `DirectExecutionService`
(`src/api/services/direct_execution_service.py`) spawns an asyncio background task
per query and persists progress via workflow checkpoints. It replaced the earlier
Temporal-based engine — Temporal is removed (no `temporalio` dependency; there are
no workflow IDs).

## Authentication

Cerebro uses JWT bearer tokens signed with **RS256** (RSA), validated per endpoint:

```
Authorization: Bearer <jwt_token>
```

- Access tokens expire in **15 minutes**; refresh tokens in **7 days**.
- Keys are PEM files at `/secrets/jwt_private.pem` and `/secrets/jwt_public.pem`.
- Passwords are bcrypt-hashed (12 rounds), minimum length **12** characters.

> **Authorization is not global.** `AuthMiddleware` is a no-op — it sets request
> state to `None` and validates nothing. Only endpoints that explicitly declare
> an auth dependency are protected: the `auth` router, and `research` (via
> `get_tenant_context` → `Depends(get_current_token)`). The `reports` and `users`
> routers — including `DELETE /api/v1/users/{user_id}/gdpr` — declare **no** auth
> dependency and are unauthenticated. As a result
> **`/api/v1/query/*`, `/api/v1/agents/*`, and `/api/v1/masr/*` are effectively
> unauthenticated.** Do not assume a token is required unless the specific
> endpoint's reference says so.

### Getting a token

```bash
curl -X POST "http://localhost:8000/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "correct horse battery"
  }'

# Response (AuthResponse — user + nested tokens)
{
  "user": {
    "id": "123e4567-e89b-12d3-a456-426614174000",
    "email": "user@example.com",
    "username": "username"
  },
  "tokens": {
    "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9...",
    "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9...",
    "token_type": "Bearer",
    "expires_in": 900
  }
}
```

WebSocket connections allow anonymous access when `ENVIRONMENT=development`;
otherwise the `token` query parameter is validated with the same RS256 key.

## Content types

All endpoints accept and return JSON unless otherwise specified:

```
Content-Type: application/json
Accept: application/json
```

## Error handling

Standard HTTP status codes; error details are returned in a nested `error`
envelope:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Error description",
    "details": {}
  }
}
```

The `code` is derived from the HTTP status (see `ERROR_CODES_BY_STATUS`):
`BAD_REQUEST` (400), `AUTHENTICATION_REQUIRED` (401), `FORBIDDEN` (403),
`NOT_FOUND` (404), `CONFLICT` (409), `VALIDATION_ERROR` (422),
`RATE_LIMIT_EXCEEDED` (429), `INTERNAL_SERVER_ERROR` (500).

Common codes: `200` Success, `201` Created, `202` Accepted (async started),
`204` No Content, `400` Bad Request, `401` Unauthorized, `403` Forbidden,
`404` Not Found, `422` Validation Error, `500` Internal Server Error,
`503` Service Unavailable.

## Rate limiting

A single global rate limiter applies to all endpoints: **100 requests/minute**
(`MAX_REQUESTS_PER_MINUTE=100`, `ENABLE_RATE_LIMITING=True`). There are no tiers,
no per-endpoint limits, no burst allowances, and no account quotas.

Responses include:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 42
```

`X-RateLimit-Reset` is the number of **seconds until** the window resets (the same
value is sent as `Retry-After` on a 429), not an absolute epoch timestamp.

---

## Endpoint inventory

Mounted routers and their effective prefixes. Endpoint counts reflect the live
`include_router` list; unmounted route modules (qa, costs, benchmarks,
improvement, memory, experiments) are not part of the API.

| Router | Prefix | Summary |
|---|---|---|
| health | (none) | `GET /health`, `/ready`, `/live` |
| query (primary/MASR) | `/api/v1/query` | NL query entry + execution status/results |
| agents (bypass) | `/api/v1/agents` | Direct agent execution, chain/mixture, metrics |
| masr | `/api/v1/masr` | Routing decisions, cost estimates, feedback |
| research | `/api/v1/research` | Research project CRUD + progress/refine/results |
| reports | `/api/v1/reports` | Report generation, download, search, integrity |
| supervisors | `/api/v1/supervisors` | Supervisor coordination (+ WebSocket) |
| talkhier | `/api/v1/talkhier` | Multi-round refinement sessions (+ WebSocket) |
| auth | `/api/v1/auth` | Registration, login, sessions, password flows |
| users | `/api/v1/users` | `DELETE /{user_id}/gdpr` (single endpoint) |
| websocket | `/ws*` | Real-time project/CLI event streams |
| metrics | `/metrics` | Prometheus exposition |

---

## Health endpoints

### GET /health

```json
{
  "status": "healthy",
  "service": "research-platform-api"
}
```

### GET /ready

Readiness check for Kubernetes deployments.

```json
{
  "status": "ready",
  "service": "research-platform-api",
  "checks": {
    "database": "ok",
    "redis": "ok",
    "temporal": "ok"
  }
}
```

> These checks are hardcoded `"ok"` values, not live probes. The `temporal` entry
> is a vestigial literal — Temporal is removed from the runtime.

### GET /live

```json
{
  "status": "alive"
}
```

---

## Primary query API (`/api/v1/query`)

The intelligence-first surface. Each query is routed by MASR and executed
asynchronously; the request returns immediately with an execution handle, and the
real routing/result data is fetched from the execution endpoints.

| Method & path | Purpose |
|---|---|
| `POST /api/v1/query/research` | General NL research query (MASR-routed) |
| `POST /api/v1/query/analyze` | Analysis-focused wrapper |
| `POST /api/v1/query/synthesize` | Synthesis-focused wrapper |
| `POST /api/v1/query/literature` | Literature-review wrapper |
| `POST /api/v1/query/methodology` | Methodology wrapper |
| `POST /api/v1/query/comparison` | Comparative-analysis wrapper |
| `GET /api/v1/query/execution/{execution_id}/status` | Live execution status + real routing metadata |
| `GET /api/v1/query/execution/{execution_id}/results` | Final aggregated results |
| `POST /api/v1/query/execution/{project_id}/resume` | Resume a checkpointed execution |
| `GET /api/v1/query/routing/strategies` | List available routing strategies |
| `GET /api/v1/query/routing/recommend` | Static routing recommendation for a query |

The `/analyze`, `/synthesize`, `/literature`, `/methodology`, and `/comparison`
paths are thin wrappers that build the same internal request and call the same
handler as `/research`.

**Example**

```bash
curl -X POST "http://localhost:8000/api/v1/query/research" \
  -H "Content-Type: application/json" \
  -d '{"query": "Value US regional banks after rate cuts", "domains": ["finance"]}'
```

> **Immediate response is a placeholder.** The synchronous response to
> `POST /api/v1/query/research` contains hardcoded fields — `selected_agents=[]`,
> `estimated_cost=0.015`, `estimated_quality=0.85`, `confidence=0.85`,
> `routing_time_ms=50.0` — not the real MASR decision. Poll
> `GET /api/v1/query/execution/{execution_id}/status` and `/results` for the actual
> selected agents, routing, and output. `GET /routing/recommend` likewise returns
> canned recommendations keyed by query length.

---

## Agent (bypass) API (`/api/v1/agents`)

Direct, MASR-bypassing access to individual agents — used for testing and
low-latency single-agent calls. Full request/response schemas:
**`docs/api/agent-api-reference.md`**.

| Method & path | Purpose |
|---|---|
| `GET /api/v1/agents` | List callable agent types |
| `GET /api/v1/agents/{agent_type}` | Agent capability descriptor |
| `POST /api/v1/agents/{agent_type}/execute` | Direct single-agent execution |
| `POST /api/v1/agents/chain` | Chain-of-Agents (sequential) |
| `POST /api/v1/agents/mixture` | Mixture-of-Agents (parallel + aggregate) |
| `POST /api/v1/agents/{agent_type}/validate` | Validate a proposed agent request |
| `GET /api/v1/agents/{agent_type}/metrics` | Per-agent metrics |
| `GET /api/v1/agents/{agent_type}/health` | Per-agent health |
| `GET /api/v1/agents/system/stats` | Aggregate agent-system stats |
| `GET /api/v1/agents/executions/active` | In-flight bypass executions |
| `POST /api/v1/agents/literature-review/search` | Literature-review convenience route |
| `POST /api/v1/agents/citation/format` | Citation-formatting convenience route |
| `POST /api/v1/agents/synthesis/combine` | Synthesis convenience route |
| `GET /api/v1/agents/health/summary` | Aggregate agent-health summary |
| `GET /api/v1/agents/performance/comparison` | Cross-agent performance comparison |
| `POST /api/v1/agents/{workflow}` | Prebuilt workflows (e.g. `workflows/literature-analysis`) |

The bypass surface exposes **10** agent types: `literature-review`, `citation`,
`methodology`, `comparative-analysis`, `synthesis`, `financial-analysis`,
`valuation`, `risk-assessment`, `financial-calculator`, `verification`. The four
Content and three Analytics workers are reachable only through the MASR-routed
query API, not here. Chain-of-Agents and Mixture-of-Agents exist **only** as these
bypass endpoints — MASR itself never selects them.

---

## MASR routing API (`/api/v1/masr`)

Inspect and influence routing decisions without executing a query. Full guide:
**`docs/api/masr-api-guide.md`**.

| Method & path | Purpose |
|---|---|
| `POST /api/v1/masr/route` | Return a routing decision for a query |
| `POST /api/v1/masr/estimate-cost` | Estimated execution cost breakdown |
| `POST /api/v1/masr/evaluate-strategies` | Compare routing strategies |
| `POST /api/v1/masr/analyze-complexity` | Analyze query complexity |
| `GET /api/v1/masr/strategies` | List available routing strategies |
| `GET /api/v1/masr/models` | List available models |
| `POST /api/v1/masr/feedback` | Submit post-hoc cost/quality feedback |
| `GET /api/v1/masr/status` | Router health and performance metrics |

MASR runs **in-process** (the `MASRouter` class). The standalone `masr-router`
container (`:9100`) in the compose file is legacy and is not on the query path.

---

## Research project endpoints (`/api/v1/research`)

Project-oriented interface backed by Postgres. Distinct from the `/api/v1/query`
surface: projects persist and expose progress/refine/results lifecycles.

### POST /api/v1/research/projects

```json
{
  "title": "US Regional Bank Valuation",
  "query": {
    "text": "How do rate cuts affect US regional bank valuations?",
    "domains": ["finance", "economics"],
    "timeframe": "last_5_years",
    "language": "en"
  },
  "user_id": "user-123",
  "scope": {
    "research_depth": "comprehensive",
    "paper_limit": 100,
    "include_preprints": true,
    "geographic_scope": "global"
  }
}
```

**Response (201):**

```json
{
  "id": "proj-550e8400-e29b-41d4-a716-446655440000",
  "title": "US Regional Bank Valuation",
  "user_id": "user-123",
  "status": "pending",
  "created_at": "2026-01-01T12:00:00Z",
  "updated_at": "2026-01-01T12:00:00Z",
  "scope": {
    "research_depth": "comprehensive",
    "paper_limit": 100,
    "include_preprints": true,
    "geographic_scope": "global"
  }
}
```

### GET /api/v1/research/projects/{project_id}

```json
{
  "id": "proj-550e8400-e29b-41d4-a716-446655440000",
  "title": "US Regional Bank Valuation",
  "user_id": "user-123",
  "status": "in_progress",
  "created_at": "2026-01-01T12:00:00Z",
  "updated_at": "2026-01-01T12:05:00Z",
  "completion_estimate": "2026-01-01T12:30:00Z"
}
```

### GET /api/v1/research/projects

List with filtering.

**Query parameters:** `user_id` (optional), `status` (`pending`, `planning`,
`in_progress`, `completed`, `failed`, `cancelled`), `limit` (default 10, max 100),
`offset` (default 0).

```json
[
  {
    "id": "proj-550e8400-e29b-41d4-a716-446655440000",
    "title": "US Regional Bank Valuation",
    "status": "in_progress",
    "created_at": "2026-01-01T12:00:00Z",
    "progress_percentage": 65.0
  }
]
```

### GET /api/v1/research/projects/{project_id}/progress

```json
{
  "project_id": "proj-550e8400-e29b-41d4-a716-446655440000",
  "total_tasks": 5,
  "completed_tasks": 3,
  "in_progress_tasks": 1,
  "pending_tasks": 1,
  "progress_percentage": 60.0,
  "current_agent": "synthesis_agent",
  "current_phase": "analysis",
  "estimated_completion": "2026-01-01T12:30:00Z",
  "agent_progress": {
    "literature_review": {"status": "completed", "confidence": 0.85},
    "comparative_analysis": {"status": "completed", "confidence": 0.85},
    "methodology": {"status": "completed", "confidence": 0.85},
    "synthesis": {"status": "in_progress", "progress": 0.4},
    "citation": {"status": "pending"}
  }
}
```

> Agent `confidence` values are hardcoded heuristics (0.85 on success, 0.3 on empty
> output, 0.8 on the fast path), not model-reported quality signals.

### POST /api/v1/research/projects/{project_id}/refine

> **Not implemented.** This endpoint is declared but unconditionally raises
> **HTTP 501 Not Implemented** (`detail: "Scope refinement not yet implemented"`).
> It accepts a `ResearchScope` body but never applies it and returns no updated
> project.

**Response (501):**

```json
{
  "error": {
    "code": "API_ERROR",
    "message": "Scope refinement not yet implemented",
    "details": {}
  }
}
```

### POST /api/v1/research/projects/{project_id}/cancel

**Response (204):** No content.

### GET /api/v1/research/projects/{project_id}/results

```json
{
  "project_id": "proj-550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "completion_time": "2026-01-01T12:28:00Z",
  "results": {
    "synthesis": {
      "main_conclusions": "Rate cuts widen net interest margins for...",
      "confidence_score": 0.85,
      "research_gaps": ["Long-horizon deposit-beta studies"]
    }
  },
  "quality_metrics": {
    "overall_confidence": 0.85,
    "source_reliability": 0.85,
    "methodology_rigor": 0.84
  }
}
```

---

## Report generation endpoints (`/api/v1/reports`)

### POST /api/v1/reports/generate

Generate a report asynchronously. (There is no `POST /api/v1/reports`; the only
bare-path route on this router is `GET /api/v1/reports`, the list endpoint below.)

```json
{
  "title": "US Regional Banks: Comprehensive Analysis",
  "query": "Impact of rate cuts on regional bank valuations",
  "domains": ["finance"],
  "project_id": "proj-550e8400-e29b-41d4-a716-446655440000",
  "user_id": "user-123",
  "report_type": "comprehensive",
  "citation_style": "APA",
  "formats": ["html", "pdf", "markdown"],
  "include_toc": true,
  "include_executive_summary": true,
  "include_citations": true,
  "save_to_storage": true
}
```

**Response (202):**

```json
{
  "id": "rpt-550e8400-e29b-41d4-a716-446655440000",
  "title": "US Regional Banks: Comprehensive Analysis",
  "report_type": "comprehensive",
  "generation_status": "generating",
  "formats_generated": [],
  "word_count": 0,
  "quality_score": 0.0,
  "created_at": "2026-01-01T12:30:00Z",
  "download_urls": {}
}
```

### GET /api/v1/reports/{report_id}

```json
{
  "id": "rpt-550e8400-e29b-41d4-a716-446655440000",
  "title": "US Regional Banks: Comprehensive Analysis",
  "generation_status": "completed",
  "formats_generated": ["html", "pdf", "markdown"],
  "word_count": 8547,
  "page_count": 23,
  "quality_score": 0.91,
  "created_at": "2026-01-01T12:30:00Z",
  "generation_time_seconds": 127.5,
  "download_urls": {
    "html": "/api/v1/reports/rpt-550e8400-e29b-41d4-a716-446655440000/download/html",
    "pdf": "/api/v1/reports/rpt-550e8400-e29b-41d4-a716-446655440000/download/pdf"
  }
}
```

### GET /api/v1/reports/{report_id}/download/{format_type}

Download a report. `format_type` is one of `html`, `pdf`, `latex`, `docx`,
`markdown`, `json`. Returns a file with the appropriate MIME type.

### GET /api/v1/reports

List with filtering and pagination. Query parameters: `user_id`, `status_filter`,
`report_type`, `page` (default 1), `page_size` (default 20, max 100).

### POST /api/v1/reports/search

Search by term and filters (`search_term`, `user_id`, `report_type`,
`min_quality_score`, `limit`, `offset`).

### GET /api/v1/reports/statistics

Aggregate generation statistics. Query parameters: `user_id`, `days`
(default 30, max 365).

### DELETE /api/v1/reports/{report_id}

Delete a report. Query parameter `delete_files` (default true).
**Response (204):** No content.

### GET /api/v1/reports/{report_id}/integrity

Verify report and file checksums.

```json
{
  "report_id": "rpt-550e8400-e29b-41d4-a716-446655440000",
  "integrity_status": "valid",
  "checksum_verification": {
    "html": {"expected": "abc123", "actual": "abc123", "valid": true}
  },
  "file_verification": {
    "html": {"exists": true, "size_bytes": 157834}
  },
  "last_verified": "2026-01-01T13:00:00Z"
}
```

---

## Supervisor endpoints (`/api/v1/supervisors`)

HTTP + WebSocket access to the domain supervisors (Research, Content, Analytics,
Finance). Each supervisor runs an internal LangGraph `StateGraph`. WebSocket
routes:

- `WS /api/v1/supervisors/coordination/ws` — cross-supervisor coordination stream
- `WS /api/v1/supervisors/{supervisor_type}/ws` — per-supervisor stream

---

## TalkHier endpoints (`/api/v1/talkhier`)

Multi-round refinement/consensus sessions. WebSocket routes:

- `WS /api/v1/talkhier/sessions/{id}/live` — live session updates
- `WS /api/v1/talkhier/interactive` — interactive refinement
- `WS /api/v1/talkhier/coordination` — coordination stream

---

## Authentication endpoints (`/api/v1/auth`)

| Method & path | Purpose |
|---|---|
| `POST /api/v1/auth/register` | Create an account |
| `POST /api/v1/auth/login` | Authenticate, receive tokens |
| `POST /api/v1/auth/refresh` | Exchange refresh token for a new access token |
| `POST /api/v1/auth/logout` | Revoke the current session |
| `POST /api/v1/auth/forgot-password` | Request a password-reset email |
| `POST /api/v1/auth/reset-password` | Complete a password reset with a token |
| `POST /api/v1/auth/change-password` | Change password for the logged-in user |
| `GET /api/v1/auth/verify-email` | Verify an email address via token |
| `GET /api/v1/auth/sessions` | List active sessions/devices |
| `DELETE /api/v1/auth/sessions/{device_id}` | Revoke one session |
| `POST /api/v1/auth/revoke-all` | Revoke all sessions |
| `GET /api/v1/auth/me` | Current authenticated user |

### POST /api/v1/auth/register

```json
{
  "email": "user@example.com",
  "username": "username",
  "password": "SecurePass123!@",
  "confirm_password": "SecurePass123!@",
  "full_name": "John Doe",
  "organization": "Research Lab",
  "accept_terms": true
}
```

> Password requirements: minimum **12** characters, with at least one uppercase,
> one lowercase, one digit, and one special character. `confirm_password` and
> `accept_terms` are **required** — omitting them fails 422 validation.

**Response (201):** `AuthResponse` — the same nested `{user, tokens}` shape as
login (see below).

```json
{
  "user": {
    "id": "123e4567-e89b-12d3-a456-426614174000",
    "email": "user@example.com",
    "username": "username",
    "full_name": "John Doe"
  },
  "tokens": {
    "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9...",
    "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9...",
    "token_type": "Bearer",
    "expires_in": 900
  }
}
```

### POST /api/v1/auth/login

```json
{
  "email": "user@example.com",
  "password": "SecurePass123!@"
}
```

> MFA (`mfa_code`) and `remember_me` are not currently implemented
> (`ENABLE_MFA=False`).

**Response (200):** `AuthResponse` — user and tokens are nested, not flat.

```json
{
  "user": {
    "id": "user-123",
    "email": "user@example.com",
    "username": "username",
    "full_name": "John Doe"
  },
  "tokens": {
    "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9...",
    "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9...",
    "token_type": "Bearer",
    "expires_in": 900
  }
}
```

### POST /api/v1/auth/refresh

```json
{ "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9..." }
```

Returns a fresh `access_token` / `refresh_token` pair with `expires_in: 900`.

### POST /api/v1/auth/logout

Takes the access token from the `Authorization` header and revokes it. Accepts
**no** request body.

```
Authorization: Bearer <access_token>
```

**Response (204):** No content.

---

## User data endpoints (`/api/v1/users`)

### DELETE /api/v1/users/{user_id}/gdpr

GDPR erasure — deletes a user and associated data. **No authorization is
required** — this endpoint declares no auth dependency. This is the only endpoint
under the `users` router.

---

## WebSocket endpoints

These are the only live WebSocket routes. There is no MASR WebSocket, no SSE, and
no experiments stream. Supervisor and TalkHier WebSockets are listed under their
respective sections above.

| Route | Purpose |
|---|---|
| `WS /ws` | System-wide event stream |
| `WS /ws/projects/{project_id}` | Project-scoped updates |
| `WS /ws/cli/{project_id}` | CLI-optimized stream (Rich terminal formatting) |
| `GET /ws/health` | WebSocket subsystem health |

### WS /ws/projects/{project_id}

```bash
ws://localhost:8000/ws/projects/proj-123?token=<jwt_token>
```

```json
{
  "type": "progress",
  "project_id": "proj-123",
  "timestamp": "2026-01-01T12:05:00Z",
  "data": {
    "progress_percentage": 25.0,
    "completed_tasks": 1,
    "total_tasks": 4,
    "current_agent": "literature_review_agent",
    "current_phase": "search"
  }
}
```

### GET /ws/health

```json
{
  "status": "healthy",
  "websocket_stats": {
    "active_connections": 15,
    "total_messages_sent": 1247,
    "uptime_seconds": 3600
  }
}
```

---

## Observability endpoint

### GET /metrics

Prometheus exposition (mounted as an ASGI app, not under `/api/v1`). LLM metrics
include `llm_call_duration_seconds`, `llm_tokens_total`, `llm_cost_usd_total`,
`llm_request_cost_drift_ratio`, and `llm_cost_drift_events_total`. Structured logs
use structlog; Langfuse tracing is opt-in (`LANGFUSE_ENABLED`, default off). There
is no OpenTelemetry backbone or Grafana/Loki/Jaeger integration.

---

## Data models

### ResearchProject

```json
{
  "id": "string (UUID)",
  "title": "string",
  "query": {
    "text": "string",
    "domains": ["string"],
    "timeframe": "string",
    "language": "string"
  },
  "user_id": "string",
  "status": "pending | planning | in_progress | completed | failed | cancelled",
  "created_at": "string (ISO 8601)",
  "updated_at": "string (ISO 8601)",
  "completion_estimate": "string (ISO 8601)",
  "scope": {
    "research_depth": "comprehensive | focused | quick",
    "paper_limit": "number",
    "include_preprints": "boolean",
    "geographic_scope": "string"
  }
}
```

### ResearchProgress

```json
{
  "project_id": "string (UUID)",
  "total_tasks": "number",
  "completed_tasks": "number",
  "in_progress_tasks": "number",
  "pending_tasks": "number",
  "progress_percentage": "number (0-100)",
  "current_agent": "string",
  "current_phase": "string",
  "estimated_completion": "string (ISO 8601)",
  "agent_progress": {
    "agent_name": {
      "status": "pending | in_progress | completed | failed",
      "progress": "number (0-1)",
      "confidence": "number (0-1)"
    }
  }
}
```

### Report

```json
{
  "id": "string (UUID)",
  "title": "string",
  "query": "string",
  "report_type": "comprehensive | executive_summary | academic | literature_review | methodology | synthesis",
  "generation_status": "generating | completed | failed",
  "formats_generated": ["string"],
  "word_count": "number",
  "page_count": "number",
  "quality_score": "number (0-1)",
  "confidence_score": "number (0-1)",
  "created_at": "string (ISO 8601)",
  "generation_time_seconds": "number",
  "download_urls": { "format": "string (URL)" }
}
```

---

## LLM providers

The default runtime is **Gemini-only** (`GEMINI_DEFAULT_MODEL=gemini-pro`).
OpenRouter multi-provider routing (DeepSeek for simple tiers, Claude Sonnet for
complex) is flag-gated **off** — it requires both
`MULTI_PROVIDER_ROUTING_ENABLED=True` and `OPENROUTER_API_KEY`.
`DEEPSEEK_ENABLED`, `LLAMA_ENABLED`, and `OPENROUTER_ENABLED` all default to
`False`.

---

## CLI

Two entrypoints are installed: `research-platform` and `research-cli`. There is no
`cerebro-cli` and no published package. Command tree:

- `config` — `show` | `set` | `save`
- `health`
- `completion`
- `agents` — `query`, `route`, `estimate`, `execute`, `chain`, `status`
- `projects` — `create`, `get`, `list`, `progress`, `cancel`, `results`, `refine`

Configuration lives in `~/.research-cli.env` (dotenv: `RESEARCH_API_URL`,
`RESEARCH_AUTH_TOKEN`). Global flags: `--format table|json|yaml|csv`, `--api-url`,
`--verbose`, `--no-color`.

```bash
research-cli health
research-cli projects create \
  --title "US Regional Bank Valuation" \
  --query "How do rate cuts affect regional bank valuations?" \
  --domains "finance,economics" \
  --user-id "researcher-001"
```

---

## Error codes

There are no domain-specific error-code strings. Every error `code` is derived
from the HTTP status by `ERROR_CODES_BY_STATUS`
(`src/api/middleware/error_envelope.py`); a handler-supplied `detail.code`
overrides it when present. The full set is:

- `BAD_REQUEST` — 400
- `AUTHENTICATION_REQUIRED` — 401
- `FORBIDDEN` — 403
- `NOT_FOUND` — 404
- `CONFLICT` — 409
- `VALIDATION_ERROR` — 422
- `RATE_LIMIT_EXCEEDED` — 429
- `INTERNAL_SERVER_ERROR` — 500

Any status without a mapping (e.g. 501) falls back to `API_ERROR`.

---

## Testing

```bash
# Get an access token
export TOKEN=$(curl -s -X POST "http://localhost:8000/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"TestPassword123!"}' \
  | jq -r '.tokens.access_token')

# Submit a MASR-routed query
curl -X POST "http://localhost:8000/api/v1/query/research" \
  -H "Content-Type: application/json" \
  -d '{"query": "Value US regional banks after rate cuts", "domains": ["finance"]}'

# Create a research project
curl -X POST "http://localhost:8000/api/v1/research/projects" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test Research Project",
    "query": {"text": "Impact of rate cuts on banks", "domains": ["finance"]},
    "user_id": "test-user"
  }'
```
