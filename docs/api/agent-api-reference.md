# Agent Framework API Reference

Canonical, detailed reference for Cerebro's Agent Framework APIs: the **Primary API**
(MASR-routed, via `/api/v1/query/*`) and the **Bypass API** (direct agent access, via
`/api/v1/agents/*`). This is the detailed companion to `docs/api-documentation.md`.

> **Cerebro** is the product; current focus is **financial research (US equities)**. The
> deployment artifacts still carry the pre-rebrand **`research-platform`** identity (FastAPI
> title `Research Platform API`, k8s namespace, `research-platform-api` images, `research_db`,
> the `research-cli`/`research-platform` CLI entrypoints). Those infra names are legacy and are
> not the product name.

## Base URL

- **Development**: `http://localhost:8000`

`/docs` (Swagger) and `/redoc` are served only when `DEBUG=True`; `DEBUG` defaults to `False`,
so interactive docs are off unless explicitly enabled.

## Authentication

`AuthMiddleware` in the middleware stack is a **no-op**: it initializes
`request.state.user`/`token_payload`/`organization_id` to `None` and validates nothing.
Authentication is enforced **per endpoint** via FastAPI `Depends(...)`, not globally.

Consequently, the Agent Framework surfaces documented here — **`/api/v1/query/*`,
`/api/v1/agents/*`, and `/api/v1/masr/*`** — are **effectively unauthenticated**. Only endpoints
that declare `Depends(get_current_user)` / `require_*` (the `auth`, `users` GDPR, and parts of
`research`/`reports` routers) are protected.

Where auth *is* enforced it uses:

- JWT **RS256** (keys at `/secrets/jwt_private.pem` + `/secrets/jwt_public.pem`)
- Access tokens **15 min**, refresh tokens **7 days**
- bcrypt password hashing (12 rounds), `PASSWORD_MIN_LENGTH=12`

Do not describe the Agent Framework API as JWT-gated. It is not, with default configuration.

---

## Request flow (what actually happens)

```
Client -> FastAPI -> DirectExecutionService (asyncio background task)
       -> MASRouter -> MASRSupervisorBridge -> domain supervisors -> workers
       -> verification QA gate
```

- **`DirectExecutionService`** (`src/api/services/direct_execution_service.py`) is the in-process
  asyncio execution engine. It **replaced Temporal**; Temporal is removed (no `temporalio`
  dependency; `TEMPORAL_HOST`/`TEMPORAL_NAMESPACE` are dead vestigial settings).
- **MASR (Multi-Agent System Router)** is the in-process `MASRouter` class
  (`src/ai_brain/router/masr.py`). The standalone `masr-router` container (:9100) is legacy and
  is **not** on the query path (`MASR_SERVICE_URL` is not read anywhere in `src/`).
- **`MASRSupervisorBridge`** maps MASR routing decisions onto domain supervisors.
- **Domain supervisors** — Research, Content, Analytics, Finance — each run an internal LangGraph
  `StateGraph`. LangGraph exists only inside supervisors; the top-level `src/orchestration/`
  subsystem was deleted.
- Workers subclass **`LLMWorkerAgentBase`** and are **LLM-reasoning (prompt-driven)**, not coded
  decision engines. Confidence scores they report are **hardcoded heuristics** (0.85 on success,
  0.3 on empty output, 0.8 on the fast path), not measured quality signals.

### Provider default

Runtime is **Gemini-only** by default (`GEMINI_DEFAULT_MODEL=gemini-pro`). OpenRouter
multi-provider routing (DeepSeek for simple tiers, Claude Sonnet for complex tiers) is
**flag-gated OFF**: it requires both `MULTI_PROVIDER_ROUTING_ENABLED=True` **and**
`OPENROUTER_API_KEY`. `DEEPSEEK_ENABLED`, `LLAMA_ENABLED`, and `OPENROUTER_ENABLED` all default
to `False`.

---

## Primary API — Intelligent Query (`/api/v1/query/*`)

The Primary API submits a query for MASR-routed execution. `POST /research` is the main handler;
`/analyze`, `/synthesize`, `/literature`, `/methodology`, and `/comparison` are thin wrappers
that build an equivalent request and call the same handler.

MASR selects a **`CollaborationMode`** — `FAST_PATH`, `DIRECT`, `PARALLEL`, `HIERARCHICAL`,
`DEBATE`, or `ENSEMBLE`. `FAST_PATH` is a single LLM call that bypasses supervisors entirely.
MASR never selects Chain-of-Agents or Mixture-of-Agents; those exist only as Bypass endpoints
(see below).

### Submit a research query

```http
POST /api/v1/query/research
```

**Request** (representative fields):
```json
{
  "query": "Estimate a DCF fair value for a US large-cap given these fundamentals",
  "domains": ["finance"],
  "context": {},
  "routing_strategy": "balanced",
  "user_id": "researcher-123",
  "session_id": "session-456"
}
```

**Immediate response — contains hardcoded placeholders, not real routing output.**
The handler returns before execution finishes, and several fields are stubbed:

```json
{
  "execution_id": "exec-789",
  "query_id": "…",
  "status": "pending",
  "supervisor_type": "research",
  "selected_agents": [],
  "estimated_cost": 0.015,
  "estimated_quality": 0.85,
  "confidence": 0.85,
  "routing_time_ms": 50.0,
  "results": {},
  "quality_scores": {},
  "execution_time_seconds": 0.0
}
```

> **Do not treat these as live metrics.** `selected_agents=[]`, `estimated_cost=0.015`,
> `estimated_quality=0.85`, `confidence=0.85`, and `routing_time_ms=50.0` are hardcoded in the
> handler. `status` starts at `pending` (or the current status if already available). For real
> routing decisions, agent selection, and results, **poll the execution status/results
> endpoints below.**

**Example**:
```bash
curl -X POST "http://localhost:8000/api/v1/query/research" \
  -H "Content-Type: application/json" \
  -d '{"query": "Compare two US equities on valuation multiples", "domains": ["finance"]}'
```

### Wrapper endpoints

Each accepts its own request shape and delegates to the same execution handler:

```http
POST /api/v1/query/analyze
POST /api/v1/query/synthesize
POST /api/v1/query/literature
POST /api/v1/query/methodology
POST /api/v1/query/comparison
```

### Execution status, results, resume

Real routing data and outputs are available only via these endpoints once the background task
has progressed:

```http
GET  /api/v1/query/execution/{execution_id}/status
GET  /api/v1/query/execution/{execution_id}/results
POST /api/v1/query/execution/{project_id}/resume
```

**`GET …/status`** response:
```json
{
  "execution_id": "exec-789",
  "status": "running",
  "progress_percentage": 65.0,
  "current_phase": "supervisor_execution",
  "supervisor_type": "finance",
  "workers_used": 3,
  "execution_time_seconds": 142.3,
  "errors": []
}
```

**`GET …/results`** returns the completed execution's outputs, quality scores, and metadata.

**`POST …/resume`** resumes an execution from its last checkpoint
(`masr_routing` / `supervisor_execution` / `fast_path_completed` / `completed`).

### Routing intelligence

```http
GET /api/v1/query/routing/strategies
GET /api/v1/query/routing/recommend?query=<text>
```

`GET /routing/strategies` lists the available routing strategies.

`GET /routing/recommend` returns **static, canned recommendations keyed by query length** — it
does not run MASR. Treat its `estimated_*` fields and suggested agents as a rough, length-based
heuristic, not a routing decision:

```json
{
  "query_analysis": {
    "complexity": "moderate",
    "estimated_domains": [],
    "confidence": 0.85
  },
  "routing_recommendation": {
    "suggested_strategy": "balanced",
    "expected_agents": ["literature-review", "methodology", "synthesis"],
    "estimated_cost": 0.015,
    "estimated_time_seconds": 180,
    "estimated_quality": 0.85
  },
  "explanation": "Canned recommendation selected by query length."
}
```

---

## Bypass API — Direct Agent Access (`/api/v1/agents/*`)

The Bypass API calls a single agent (or an explicit multi-agent pattern) directly, skipping MASR
routing. It is a catalog surface backed by `AgentFactory` — useful for testing and targeted
execution, not the MASR-routed production path.

### Callable agent types (10)

The Bypass `AgentType` enum (`src/models/agent_api_models.py:16-28`) exposes exactly **10**
values:

| Agent type | Domain |
|---|---|
| `literature-review` | Research |
| `citation` | Research |
| `methodology` | Research |
| `comparative-analysis` | Research |
| `synthesis` | Research |
| `financial-analysis` | Finance |
| `valuation` | Finance |
| `risk-assessment` | Finance |
| `financial-calculator` | Finance (deterministic, no LLM) |
| `verification` | Cross-cutting QA gate |

> **Content and Analytics workers are NOT bypass-callable.** They exist in the runtime registry
> but are not exposed through `/api/v1/agents`.

This differs from the platform's full **17-agent registry** — 15 domain workers (Research 5,
Content 4, Analytics 3, Finance 3) plus `verification` and `financial_calculator`
(`src/agents/factory.py:48-66`). `AgentFactory` is the catalog for the Bypass API; it is **not**
the MASR-routed runtime execution path (supervisors instantiate their own workers).

`financial-calculator` is a **deterministic** tool agent: pure finance math (DCF, NPV, ratios,
amortization, descriptive stats) with no LLM, no API keys, and no external data.

### List agents

```http
GET /api/v1/agents
```

Returns the catalog of Bypass-callable agents with their capabilities and per-agent endpoint
paths.

### Get agent info

```http
GET /api/v1/agents/{agent_type}
```

```bash
curl "http://localhost:8000/api/v1/agents/financial-analysis"
```

### Execute a single agent

```http
POST /api/v1/agents/{agent_type}/execute
```

**Request**:
```json
{
  "query": "Compute liquidity and leverage ratios from these financials",
  "context": {"domain": "finance"},
  "parameters": {},
  "timeout_seconds": 300,
  "quality_threshold": 0.8,
  "user_id": "researcher-123"
}
```

**Response** (`ExecutionMode.DIRECT`):
```json
{
  "execution_id": "agent-exec-123",
  "agent_type": "financial-analysis",
  "status": "completed",
  "output": {},
  "confidence": 0.85,
  "quality_score": 0.85,
  "execution_time_seconds": 42.3,
  "errors": [],
  "warnings": []
}
```

> `confidence`/`quality_score` are the same hardcoded worker heuristics described above, not
> measured quality.

### Chain-of-Agents

```http
POST /api/v1/agents/chain
```

Sequential execution (`ExecutionMode.CHAIN`) over an explicit `agent_chain`, optionally passing
intermediate results forward. CoA is a Bypass-only pattern; MASR never selects it.

```json
{
  "query": "Full research pass over a US equity",
  "agent_chain": ["literature-review", "methodology", "comparative-analysis", "synthesis"],
  "pass_intermediate_results": true,
  "quality_threshold": 0.85,
  "timeout_per_agent_seconds": 180
}
```

### Mixture-of-Agents

```http
POST /api/v1/agents/mixture
```

Parallel execution (`ExecutionMode.MIXTURE`) across `agent_types`, then aggregation. MoA is a
Bypass-only pattern; MASR never selects it.

```json
{
  "query": "Multi-perspective valuation view",
  "agent_types": ["financial-analysis", "valuation", "risk-assessment"],
  "aggregation_strategy": "consensus",
  "consensus_threshold": 0.8,
  "max_parallel": 3
}
```

### Per-agent utility endpoints

```http
POST /api/v1/agents/{agent_type}/validate
GET  /api/v1/agents/{agent_type}/metrics
GET  /api/v1/agents/{agent_type}/health
```

- `…/validate` checks a query/parameters payload before execution.
- `…/metrics` and `…/health` return per-agent counters and status. **Several of these surfaces
  return stub or hardcoded values** — treat them as scaffolding, not production telemetry.

### Convenience endpoints

```http
POST /api/v1/agents/literature-review/search?query=<text>&max_sources=<n>
POST /api/v1/agents/citation/format
POST /api/v1/agents/synthesis/combine
POST /api/v1/agents/workflows/literature-analysis
POST /api/v1/agents/workflows/comprehensive-research
```

For `literature-review/search`, `query` and `max_sources` are query-string parameters. For
`citation/format` and `synthesis/combine`, the parameters are **request-body fields, not query
strings**: `citation/format` takes `sources: list[str]` plus `style` (body, default `"APA"`), and
`synthesis/combine` takes `findings: list[dict]` plus `synthesis_focus` (body, default
`"comprehensive"`).

### System-monitoring endpoints

```http
GET /api/v1/agents/system/stats
GET /api/v1/agents/executions/active
GET /api/v1/agents/health/summary
GET /api/v1/agents/performance/comparison
```

> These aggregate views may return stub or hardcoded values in the current build; do not present
> their numbers as measured system metrics.

---

## Related mounted routers

The following routers are live alongside the Query and Agents APIs and are the correct
endpoints for MASR, supervisor, and TalkHier interaction:

- `/api/v1/masr` — routing, cost estimation, strategy evaluation, feedback, status.
- `/api/v1/supervisors` — supervisor coordination and per-supervisor endpoints (plus the
  WebSocket routes below).
- `/api/v1/talkhier` — TalkHier structured multi-round refinement (plus the WebSocket routes
  below).

Health/liveness: `GET /health`, `GET /ready`, `GET /live`. Prometheus metrics: `GET /metrics`.

---

## WebSocket API

These are the **only** WebSocket routes that exist. There is no `ws/query/execution/{id}` and no
`ws/agents/{type}/interactive` stream, and there is no MASR WebSocket (it is commented out) and
no SSE.

| Route | Purpose |
|---|---|
| `/ws` | General WebSocket entry |
| `/ws/projects/{project_id}` | Per-project updates |
| `/ws/cli/{project_id}` | CLI-driven session channel |
| `GET /ws/health` | WebSocket health probe |
| `/api/v1/supervisors/coordination/ws` | Supervisor coordination stream |
| `/api/v1/supervisors/{type}/ws` | Per-supervisor stream |
| `/api/v1/talkhier/sessions/{id}/live` | Live TalkHier session |
| `/api/v1/talkhier/interactive` | Interactive TalkHier channel |
| `/api/v1/talkhier/coordination` | TalkHier coordination channel |

WebSocket auth allows anonymous connections when `ENVIRONMENT=='development'`; otherwise the
token is validated with the shared RS256 `JWTService`.

**Example** (project updates):
```javascript
const ws = new WebSocket('ws://localhost:8000/ws/projects/project-123');
ws.onmessage = (event) => {
  const update = JSON.parse(event.data);
  console.log(update);
};
```

---

## Error handling

### Standard error response

```json
{
  "error": {
    "code": "INTERNAL_SERVER_ERROR",
    "message": "Agent execution failed",
    "details": {}
  }
}
```

The envelope shape (`{"error": {code, message, details}}`) is always the same, but the `code` is
derived from the HTTP status, not from a bespoke per-failure vocabulary.

### Error codes

The `code` field comes from `ERROR_CODES_BY_STATUS` in
`src/api/middleware/error_envelope.py`, keyed by HTTP status:

- `BAD_REQUEST` (400)
- `AUTHENTICATION_REQUIRED` (401)
- `FORBIDDEN` (403)
- `NOT_FOUND` (404)
- `CONFLICT` (409)
- `VALIDATION_ERROR` (422)
- `RATE_LIMIT_EXCEEDED` (429)
- `INTERNAL_SERVER_ERROR` (500)
- `API_ERROR` — fallback when the status has no mapped code.

Agent handlers raise `HTTPException`s with string details, so failures map onto these
status-derived codes rather than agent-specific strings:

- An **invalid agent type** is rejected by the enum path parameter and surfaces as
  `VALIDATION_ERROR` (422).
- An **agent execution failure** surfaces as `INTERNAL_SERVER_ERROR` (500).
- The **capacity limit** — when `DirectExecutionService` hits its
  `max_concurrent_executions` cap and raises a `RuntimeError` — also surfaces as
  `INTERNAL_SERVER_ERROR` (500), not a dedicated capacity code.

### Recovery behavior

- **Fast-path escalation**: if a `FAST_PATH` response fails the quality gate (too short, or an
  error/apology prefix), the execution mutates its collaboration mode to `DIRECT` and falls
  through to the full supervisor path. A single request can therefore incur both a fast-path LLM
  call and a full supervisor execution.
- **Verification QA gate**: verification runs as a cross-cutting gate (initial attempt plus at
  most one revision) rather than as a normal worker.
- **Checkpoint/resume**: executions checkpoint at defined phases and can be resumed via
  `POST /api/v1/query/execution/{project_id}/resume`.

> There is **no whole-workflow automatic retry**. (An earlier `@retry` on the workflow was a bug
> that re-ran the entire pipeline and has been removed.)

---

## Rate limiting

A **single global rate limiter** applies: **100 requests per minute**
(`MAX_REQUESTS_PER_MINUTE=100`, `ENABLE_RATE_LIMITING=True`). It is **not** per-hour, not
per-endpoint, not per-role, and there is no concurrent-execution or burst tier. On the inbound
request path the limiter runs **after** the (no-op) auth and cost-drift middleware: because
Starlette wraps the last-added middleware outermost, the effective order is
Auth -> CostDrift -> RateLimit -> Idempotency -> CORS.

---

## Observability

- **Prometheus** at `/metrics` is the real LLM observability surface. Metrics include
  `llm_call_duration_seconds`, `llm_tokens_total`, `llm_cost_usd_total`,
  `llm_request_cost_drift_ratio`, and `llm_cost_drift_events_total`.
- **structlog** provides structured logging throughout.
- **Langfuse** tracing is opt-in (`LANGFUSE_ENABLED` defaults to `False`).

There is no OpenTelemetry backbone and no Grafana/Loki/Jaeger/CloudWatch/Sentry integration in
this codebase.

---

## Notes on interpreting responses

- The immediate `POST /research` response is a placeholder; poll `…/status` and `…/results`.
- `GET /routing/recommend` is canned by query length; it is not a MASR decision.
- Agent `metrics`/`health` and the `system-monitoring` endpoints may return stub values.
- Reported `confidence`/`quality_score` values are hardcoded heuristics, not measured quality.
- The design-target split of ~90% Primary / ~10% Bypass usage is a goal, not a measurement.
- Cited "50–60% cost reduction" / "20–25% quality improvement" figures are **research-paper
  targets from the routing literature, not measurements of Cerebro**.
