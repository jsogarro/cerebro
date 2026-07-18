# Configuration Reference

This document is the reference for the configuration options exposed by **Cerebro** — a multi-agent
LLM research platform (current focus: financial research, US equities). All runtime settings are
declared on the single `Settings(BaseSettings)` class in `src/core/config.py`; environment variables
are loaded from `.env` (`env_file=".env"`, `case_sensitive=True`).

> **Unknown variables are silently discarded.** `Settings` is configured with `extra="ignore"`
> (`src/core/config.py:22`). Any environment variable that does not correspond to a field defined on
> `Settings` is **accepted and ignored** at startup — no error, no warning. A typo'd or obsolete
> variable therefore has **no effect** and will not be caught for you. Only the variables documented
> below (i.e. the fields actually declared in `config.py`) change behavior.

> **Infra naming caveat.** The product is **Cerebro**, but the deployment artifacts still carry the
> pre-rebrand **`research-platform`** identity: the FastAPI title is `Research Platform API`, the
> Kubernetes namespace is `research-platform`, container images are
> `gcr.io/PROJECT_ID/research-platform-api`, the default database is `research_db`, and the CLI
> entrypoints are `research-platform` / `research-cli`. These names are kept verbatim in infra
> artifacts throughout this document; they are not the product name.

## Table of Contents
- [Environment Variables](#environment-variables)
- [Configuration Files](#configuration-files)
- [CLI Configuration](#cli-configuration)
- [Docker Configuration](#docker-configuration)
- [Kubernetes Configuration](#kubernetes-configuration)
- [Configuration Examples](#configuration-examples)
- [Security Considerations](#security-considerations)

## Environment Variables

### Core Application Settings

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `ENVIRONMENT` | string | `development` | Deployment environment (development/staging/production). Gates production validators and WebSocket anonymous access. (`/docs` exposure is gated by `DEBUG`, not `ENVIRONMENT`.) | No |
| `DEBUG` | boolean | `false` | Enable debug mode. `/docs` and `/redoc` are served only when `DEBUG=true`. | No |
| `LOG_LEVEL` | string | `INFO` | Logging level (DEBUG/INFO/WARNING/ERROR/CRITICAL) | No |
| `API_HOST` | string | `0.0.0.0` | Server host binding | No |
| `API_PORT` | integer | `8000` | Server port | No |
| `API_WORKERS` | integer | `4` | Declared on `Settings` but **unconsumed** — nothing reads it. The production image hardcodes the uvicorn worker count (`--workers 4` in the `Dockerfile` CMD), so setting `API_WORKERS` has no effect. | No |
| `SECRET_KEY` | string | `MUST_SET_IN_ENV` | Application signing secret. Validated at startup: must be set (not the placeholder) in production **and** at least 32 characters in every environment. | Yes (production) |
| `WORKER_CONCURRENCY` | integer | `10` | In-process task concurrency | No |
| `TASK_TIMEOUT_SECONDS` | integer | `300` | Per-task timeout (seconds) | No |
| `MCP_PORT` | integer | `9000` | MCP tool-server port | No |
| `MCP_TOOLS_ENABLED` | boolean | `true` | Enable MCP tool servers | No |

> There are no `HOST`, `PORT`, or `WORKERS` settings — the real field names are `API_HOST`,
> `API_PORT`, and `API_WORKERS`. Setting the former names has no effect (`extra="ignore"`).

### Database Configuration

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `DATABASE_URL` | string | `postgresql+asyncpg://research:research123@localhost:5432/research_db` | Async PostgreSQL connection string. In `production`, a startup validator **rejects** default/dangerous credentials (`research:research123`, `postgres:postgres`, `:password@`). | No (has default) |

**Database URL Format:**
```bash
# Standard async format (asyncpg driver required)
DATABASE_URL=postgresql+asyncpg://username:password@host:port/database

# With SSL
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db?ssl=require
```

> There is a single database setting. Pool sizing, overflow, timeout, recycle, and SQL-echo knobs
> (`DATABASE_POOL_SIZE`, `DATABASE_ECHO`, etc.) are **not** defined on `Settings` and are ignored if
> set. The async engine is created from `DATABASE_URL` alone (`src/models/db/session.py`).

### Redis Configuration

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `REDIS_URL` | string | `redis://localhost:6379/0` | Redis connection string (used by working memory, the idempotency store, and the rate limiter) | No |

**Redis URL Format:**
```bash
# Basic Redis
REDIS_URL=redis://localhost:6379/0

# With authentication
REDIS_URL=redis://username:password@host:6379/0
```

> As with the database, there is a single Redis setting. `REDIS_MAX_CONNECTIONS`,
> `REDIS_DECODE_RESPONSES`, and similar knobs are not defined on `Settings` and are ignored.

### AI Service Configuration (Gemini)

Gemini is the **default (and, with default flags, the only) runtime provider**. Multi-provider
routing through OpenRouter is flag-gated OFF — see [Multi-Provider Routing (PR #56)](#multi-provider-routing-pr-56).

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `GEMINI_API_KEY` | string | `null` | Google Gemini API key | Yes (for any LLM call) |
| `GEMINI_ENABLED` | boolean | `true` | Keep Gemini as the default provider | No |
| `GEMINI_DEFAULT_MODEL` | string | `gemini-pro` | Gemini model name | No |

> Only these three Gemini fields exist. Temperature, max-tokens, rate-limit, retry, and caching knobs
> (`GEMINI_MODEL`, `GEMINI_TEMPERATURE`, `GEMINI_MAX_TOKENS`, `GEMINI_RATE_LIMIT`, `GEMINI_TIMEOUT`,
> `GEMINI_RETRY_*`, `GEMINI_CACHE_*`) are **not** defined on `Settings` and are ignored if set.

### AI Brain Platform Configuration

#### Context Compaction (PR #78)

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `ENABLE_CONSTRAINT_PINNING` | boolean | `false` | Enable constraint extraction before compaction points. **Dark-launch flag**: extraction runs and is logged, but the [PINNED CONSTRAINTS] block is not injected into the model context until PR3 wires the callsite. Safe to enable in production for observability. | No |
| `CONSTRAINT_TYPES` | list | `["routing","qa","format","security"]` | Constraint types to extract. Accepted values: `routing`, `qa`, `format`, `security`. Env formats: JSON array (`["routing","qa"]`) or comma-separated (`routing,qa`). Any unrecognised value causes a startup validation error. | No |

**`ENABLE_CONSTRAINT_PINNING` lifecycle**:
- **PR2 (current)**: When `true`, the supervisor extracts constraints from the incoming user query on every request and logs `constraints_extracted_before_compaction`. The `inject()` method is fully implemented and hardened but has no production callsite yet.
- **PR3**: `inject()` will be called at the compaction boundary; `ENABLE_CONSTRAINT_PINNING=true` will then cause the [PINNED CONSTRAINTS] block to appear in the compacted context before it is handed to the model.

**`CONSTRAINT_TYPES` accepted values**:
| Value | Captures |
|---|---|
| `routing` | `routing strategy:`, `route to:`, `routing mode:`, `use … routing` |
| `qa` | `quality threshold:`, `verification required:`, `must verify:`, `qa criteria:` |
| `format` | `format:`, `output format:`, `must be formatted as:`, `structured as:` |
| `security` | `security requirement:`, `must sanitize:`, `authentication required:`, `access control:` |

**Security note**: constraint content originates from raw user queries. The `inject()` method applies the PR #72 defense chain (newline stripping, delimiter neutralisation, `ContentSanitizer`) before interpolating content into the [PINNED CONSTRAINTS] block.

#### Multi-Provider Routing (PR #56)

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `MULTI_PROVIDER_ROUTING_ENABLED` | boolean | `false` | Enable multi-provider model routing via ModelRouter | No |
| `OPENROUTER_ENABLED` | boolean | `false` | Enable OpenRouter multi-provider gateway | No |
| `OPENROUTER_API_KEY` | string | - | OpenRouter API key for multi-provider access | No (required if enabled) |
| `OPENROUTER_ENDPOINT` | string | `https://openrouter.ai/api/v1/chat/completions` | OpenRouter API endpoint | No |
| `OPENROUTER_TIER_MAPPING` | dict | See below | Model selection by complexity tier | No |
| `OPENROUTER_VALIDATE_SLUGS_ON_STARTUP` | boolean | `true` | Validate tier_mapping model slugs against live OpenRouter catalog at startup | No |

**OpenRouter Tier Mapping (default)**:
```python
{
    "simple": "deepseek/deepseek-chat",        # Cost-minimized tier
    "balanced": "anthropic/claude-sonnet-4.6", # Mid-tier quality
    "complex": "anthropic/claude-sonnet-4.6"   # Quality-focused tier
}
```

**Model slug validation**: When `OPENROUTER_VALIDATE_SLUGS_ON_STARTUP` is enabled (default), the provider fetches the live model catalog from OpenRouter at initialization and validates that every slug in `OPENROUTER_TIER_MAPPING` exists in the catalog. If stale slugs are detected (models no longer available), an **ERROR-level** log event is emitted naming each invalid tier→slug pair, and the provider's health status is marked as `degraded` to surface the issue in monitoring. This prevents silent fallback failures when model slugs change (e.g., `anthropic/claude-3.5-sonnet` → `anthropic/claude-sonnet-4.6`). The validation is non-blocking — network failures skip validation with a warning, and stale slugs do not crash the provider (runtime fallback still handles failures). Set to `false` to skip validation entirely (e.g., air-gapped dev environments).

**How to enable**: Set `OPENROUTER_API_KEY` in environment, then set `MULTI_PROVIDER_ROUTING_ENABLED=true`. When disabled or API key is absent, all requests fall back to `GeminiService` with byte-for-byte prior behavior preserved. The gate is checked in both the fast path (`direct_execution_service.py`) and the workers (`llm_worker_base.py`): OpenRouter is used only when `MULTI_PROVIDER_ROUTING_ENABLED` **and** `OPENROUTER_API_KEY` are both truthy. Note that `DEEPSEEK_ENABLED`, `LLAMA_ENABLED`, and `OPENROUTER_ENABLED` all default `false`.

#### Memory-Informed Routing (PR #55)

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `MEMORY_INFORMED_ROUTING_ENABLED` | boolean | `false` | Enable episodic/procedural memory to influence routing decisions | No |
| `MEMORY_ROUTING_MAX_WORKER_ADJUST` | integer | `2` | Maximum ±N worker count adjustment from analytic baseline | No |
| `MEMORY_ROUTING_FRESHNESS_DAYS` | integer | `30` | Decay weight for older routing history (exponential decay) | No |
| `MEMORY_PROMPT_MAX_PROCEDURES` | integer | `3` | Maximum procedural memory items to inject into worker prompts | No |
| `ADAPTIVE_ROUTING_ENABLED` | boolean | `false` | Enable adaptive routing with multi-armed bandit allocation optimization (**ships dark, pending eval**) | No |
| `ADAPTIVE_ROUTING_MIN_HISTORY` | integer | `300` | Minimum routing history samples required before adaptation begins (Hoeffding bound: ~450 samples across 15 mode-arm contexts for 95% confidence) | No |
| `ADAPTIVE_ROUTING_MAX_WORKER_ADJUST` | integer | `2` | Maximum ±N worker count adjustment from adaptive engine (from memory-adjusted baseline) | No |
| `ADAPTIVE_ROUTING_UPDATE_INTERVAL_SECONDS` | integer | `300` | Interval for bandit model updates (seconds) | No |
| `ADAPTIVE_ROUTING_POSTERIOR_TEMP_ENABLED` | boolean | `true` | Convergence lever: sharpen Thompson posteriors after warm-up to shift exploration toward exploitation | No |
| `ADAPTIVE_ROUTING_POSTERIOR_TEMP_THRESHOLD` | integer | `150` | Per-experiment sample count after which posterior sharpening activates | No |
| `MASR_FAST_PATH_ENABLED` | boolean | `true` | Single-agent fast path: classifier-approved trivial queries (SIMPLE, single-domain, one subtask, uncertainty <= 0.3, non-critical) bypass supervisors/TalkHier/verification for one routed simple-tier LLM call, with automatic escalation to DIRECT on quality-gate failure | No |
| `ADAPTIVE_ROUTING_POSTERIOR_TEMP_FACTOR` | float | `3.0` | Sharpening factor applied to Beta posterior parameters (higher = stronger exploitation) | No |

**Behavior when enabled**:
- Episodic memory nudges worker allocation based on past similar queries (bounded by `±MAX_WORKER_ADJUST`)
- Procedural memory adds context to worker prompts with successful past approaches
- Freshness decay: older history contributes less weight (exponential decay over `FRESHNESS_DAYS`)

**Behavior when disabled** (default): Routing uses purely analytic complexity scoring; no memory influence. Worker prompts contain no procedural context.

#### Adaptive Routing (PR #63)

**Status**: Ships **DARK** (flag default `false`). Pending offline eval review and A/B test promotion.

**Behavior when enabled**:
- 5-arm Thompson Sampling bandit recommends worker_count deltas {-2, -1, 0, +1, +2} from memory-adjusted baseline
- Cold start: No adaptation until `routing_history` ≥ `ADAPTIVE_ROUTING_MIN_HISTORY` (grace period)
- Bounded: Adaptive adjustment capped to ±`ADAPTIVE_ROUTING_MAX_WORKER_ADJUST` from (memory-adjusted) baseline
- Sequential composition: Memory prior applied first, then adaptive adjustment (both respect individual bounds + system hard caps)
- Graceful fallback: Engine error → routing proceeds with memory prior only (zero impact)
- In-memory state: Bandit state resets per process (no persistence yet)

**Behavior when disabled** (default): No adaptive engine calls. Routing uses analytic baseline + optional memory prior (if `MEMORY_INFORMED_ROUTING_ENABLED=true`).

**Reward signal**: `quality_score` from `routing_history` entries (0.0-1.0, higher is better).

**Notes**:
- Composes with memory-informed routing (PR #55): memory adjusts first, adaptive sees memory-adjusted baseline
- All adjustments respect shared hard caps (`max_parallel_workers`, `max_agents_per_query`)
- Structured log event emitted when adaptation changes allocation (includes deltas, confidence)

#### Langfuse Observability

Opt-in distributed tracing for MASR routing decisions and LLM provider calls,
using the Langfuse v4 SDK. Routing decisions are recorded as `span` observations
and provider calls as `generation` observations (with first-class model, token
usage, and cost fields). Tracing is **no-op safe**: when disabled, missing keys,
or on any SDK error, the tracing layer never raises and never touches the SDK.

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `LANGFUSE_ENABLED` | boolean | `false` | Master switch for Langfuse tracing. When `false`, no client is created and the SDK is never imported. | No |
| `LANGFUSE_PUBLIC_KEY` | string | `null` | Langfuse public API key (`pk-lf-...`). Only required when `LANGFUSE_ENABLED=true`. | Only when enabled |
| `LANGFUSE_SECRET_KEY` | string | `null` | Langfuse secret API key (`sk-lf-...`). Only required when `LANGFUSE_ENABLED=true`. | Only when enabled |
| `LANGFUSE_HOST` | string | `null` | Langfuse server URL. Leave unset to use Langfuse cloud. | No |

**Behavior when enabled**:
- Each `route()` call opens a `masr_routing` span whose trace ID is derived
  deterministically from the query ID (correlates with execution logs).
- The trace input is the **PII-redacted, length-capped** (≤500 char) query — the
  raw query is never sent.  Exception strings sent on error paths are also
  PII-redacted and capped at 300 characters before being sent to Langfuse.
- **Active today**: MASR routing spans (`masr_routing`) are the primary
  observability output.  The provider code implements `generation` observation
  support (model, token usage, cost, latency fields) and correctly attaches them
  when a parent trace handle is present in `request.metadata["_langfuse_trace"]`.
  However, the execution path that threads the trace handle from the router into
  provider requests is not yet wired up, so provider-generation observations are
  not produced in the current flow.  This is a planned follow-up.
- Pending traces are flushed and background export threads stopped on API
  shutdown (`shutdown_langfuse` in the lifespan handler), with a 5-second
  timeout so an unreachable Langfuse endpoint cannot block process termination.

**Behavior when disabled** (default): Zero overhead. No client is initialized,
the `langfuse` package is not imported, and all tracing calls are no-ops. If
both keys are absent while enabled, tracing degrades to a no-op with a logged
warning rather than failing.

> `LANGFUSE_ENABLED` is the tracing switch; it is **unrelated** to the `ENABLE_TRACING`
> flag in [Monitoring and Observability](#monitoring-and-observability).

#### Multi-Domain Execution (PR #54)

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `MAX_DOMAIN_PARALLELISM` | integer | `4` | Maximum concurrent domain supervisors in multi-domain queries | No |

**Note**: This is a code constant in `src/api/services/direct_execution_service.py`, not an environment variable. Multi-domain execution is always enabled; this constant bounds concurrency to prevent resource exhaustion.

**Behavior**: When a query spans multiple domains, the system:
1. Decomposes the query into per-domain sub-queries
2. Dispatches up to `MAX_DOMAIN_PARALLELISM` domain supervisors concurrently (via `asyncio.Semaphore`)
3. Merges results with `_merge_domain_results()` (labeled concatenation by default, or LLM synthesis when `MULTI_DOMAIN_MERGE_STRATEGY=llm` — see [Multi-Domain Merge Configuration](#multi-domain-merge-configuration))
4. Returns partial results if some domains fail (resilient to partial failure)

Single-domain queries bypass this path entirely (zero overhead).

#### Verification Revision Loop (PR #53)

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `MAX_VERIFICATION_REVISION_ROUNDS` | integer | `2` | Maximum verification attempts (initial + N revisions) | No |

**Note**: This is a code constant in `src/agents/supervisors/base_supervisor.py`, not an environment variable.

**Behavior**: Supervisor verifier QA now implements a bounded revision loop:
1. Worker executes and returns output
2. Verifier checks output → verdict: `PASS` or `REVISE` with issues
3. On `PASS`: accept output and continue
4. On `REVISE` with rounds remaining: append feedback to worker prompt and re-run
5. On `REVISE` at final round: accept output with ×0.85 quality penalty
6. Total attempts = `MAX_VERIFICATION_REVISION_ROUNDS` (default 2 = initial + 1 revision)

#### Parallel Worker Execution (PR #10)

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `MAX_PARALLEL_WORKERS` | integer | `5` | Maximum concurrent workers in PARALLEL supervision mode | No |

**Purpose**: Bounds concurrent worker execution within supervisors operating in PARALLEL mode via an `asyncio.Semaphore`.

**Behavior**: When `SupervisionMode.PARALLEL` is active:
1. Allocated workers execute concurrently via `asyncio.gather(..., return_exceptions=True)`
2. Concurrency bounded by semaphore with limit = `MAX_PARALLEL_WORKERS` (default 5)
3. Worker failures are isolated: failed workers logged and excluded, successful workers proceed
4. Partial results: if some workers fail, supervisor continues with successful results; failure metadata recorded
5. All workers fail: graceful degradation (empty results dict), logged as error
6. Sequential mode unchanged: workers execute one at a time in allocation order

**Revision loop compatibility**: Re-runs after REVISE verdicts also respect the parallel/sequential mode.

#### MAST Failure Labeling (PR #71)

**Note**: MAST (Multi-Agent System failure Taxonomy) labeling in Phase S has no configurable settings — it operates as a zero-cost, deterministic rule-based system. All configuration constants are hardcoded for Phase S. Future phases will add opt-in LLM classifier and guard behavior flags.

**Current Behavior** (Phase S):
1. Every verification QA-gate call automatically applies MAST labeling (zero LLM cost, ~0ms overhead)
2. Labels stored in `AgentResult.metadata["mast_failures"]` as list of mode codes (e.g., `["1.3", "1.1"]`)
3. Per-round history tracked in `AgentResult.metadata["revision_history"]` (revision-loop path only, not yet wired into production — issue #74; the single-shot QA gate emits `mast_labels`/`mast_confidence` with no round history)
4. Guards observe and log patterns but **do not block execution** (observability-only)
5. Five modes detected via rule-based heuristics:
   - **FM-1.1** (Task spec violation): Keywords in issues ("missing required", "cannot be empty", "<2 items")
   - **FM-1.3** (Step repetition): Content hash match + adjacent-round comparison
   - **FM-1.5** (No termination): REVISE verdict at `round_num >= MAX_VERIFICATION_REVISION_ROUNDS`
   - **FM-2.6** (Reasoning-action mismatch): Pattern detection ("claims X but Y")
   - **FM-3.2** (Incomplete verification): Keywords in issues ("missing", "incomplete")

**Hardcoded Constants** (code-level, non-configurable in Phase S):

| Constant | Location | Value | Purpose |
|----------|----------|-------|---------|
| `MAST_TAXONOMY` | `src/qa/mast.py:MASTLabeler` | 14-mode dict | Mode code → name mapping |
| `max_revision_rounds` | `MASTLabeler.__init__` | `MAX_VERIFICATION_REVISION_ROUNDS` | Inherited from verification loop config |
| Hash algorithm | `ContentHashTracker` | SHA-256, 16-char prefix | Step repetition detection |
| Confidence scores | Various `_detect_*` methods | 0.75-1.0 (mode-specific) | Heuristic confidence levels |

**Phase M/L Settings** (future, not implemented):

Phase M (3-4 weeks) will add:
- `MAST_ENABLE_LLM_CLASSIFIER` (boolean, default `False`): Opt-in LLM-based refinement for ambiguous cases
- `MAST_GUARD_BLOCK_REPETITION` (boolean, default `False`): Block execution on FM-1.3 loops
- `MAST_GUARD_ENFORCE_SPEC` (boolean, default `False`): Enforce spec conformance before accepting REVISE

Phase L (6-8 weeks) will add:
- `MAST_ROUTING_AVOID_HIGH_FM_SUPERVISORS` (boolean): Route queries away from supervisors with high FM-1.3/1.5 rates
- `MAST_TRACE_STORAGE_ENABLED` (boolean): Store full traces to Postgres `mast_execution_traces` table
- `MAST_DASHBOARD_ENABLED` (boolean): Enable Grafana MAST panel

**Read-Only Side Effect**:
- MAST labeling does **not** modify verification verdicts or control flow in Phase S
- Labels stored in **new metadata keys** only: `mast_failures`, `mast_confidence`, `revision_history`
- Existing keys (`verdict`, `report`, `issues`) remain unchanged
- Zero behavior change guarantee: existing verification/revision suites pass UNMODIFIED

### Authentication Configuration

Authentication uses **JWT signed with RS256** (asymmetric) — signed with a private PEM key and
verified with a public PEM key. There is no symmetric JWT secret; the application signing secret is
`SECRET_KEY` (see [Core Application Settings](#core-application-settings)).

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `JWT_ALGORITHM` | string | `RS256` | JWT signing algorithm (asymmetric) | No |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | integer | `15` | Access token expiry (minutes) | No |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | integer | `7` | Refresh token expiry (days) | No |
| `JWT_PRIVATE_KEY_PATH` | string | `/secrets/jwt_private.pem` | Path to the RS256 private signing key (PEM) | No |
| `JWT_PUBLIC_KEY_PATH` | string | `/secrets/jwt_public.pem` | Path to the RS256 public verification key (PEM) | No |
| `BCRYPT_ROUNDS` | integer | `12` | bcrypt cost factor for password hashing | No |
| `PASSWORD_MIN_LENGTH` | integer | `12` | Minimum password length | No |
| `PASSWORD_HISTORY_LIMIT` | integer | `5` | Number of previous password hashes retained to block reuse | No |
| `CHECK_PASSWORD_BREACHES` | boolean | `true` | Check new passwords against a breach corpus | No |
| `SESSION_SECRET_KEY` | string | `null` | Optional session signing secret | No |
| `SESSION_EXPIRE_HOURS` | integer | `24` | Session lifetime (hours) | No |
| `MAX_SESSIONS_PER_USER` | integer | `5` | Maximum concurrent sessions per user | No |
| `ENABLE_MFA` | boolean | `false` | Enable multi-factor authentication | No |
| `MFA_ISSUER` | string | `ResearchPlatform` | TOTP issuer label | No |

> There is **no** `JWT_SECRET_KEY` and no `PASSWORD_REQUIRE_*` composition flags. The real password
> policy is length (`PASSWORD_MIN_LENGTH=12`) + bcrypt cost + history + breach check. `API_KEY_LENGTH`
> and `API_KEY_PREFIX` are also not defined on `Settings`.

> **Enforcement caveat.** The application-level `AuthMiddleware` is effectively a no-op — it does not
> validate tokens. Authentication is enforced **per endpoint** via `Depends(...)`. Endpoints that do
> not declare an auth dependency (notably `/api/v1/query/*`, `/api/v1/agents/*`, `/api/v1/masr/*`) are
> effectively unauthenticated.

### Rate Limiting Configuration

A single global limiter is applied via middleware (backed by Redis).

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `ENABLE_RATE_LIMITING` | boolean | `true` | Enable the global rate limiter | No |
| `MAX_REQUESTS_PER_MINUTE` | integer | `100` | Global request budget per minute | No |

> The active limiter reads `ENABLE_RATE_LIMITING` and `MAX_REQUESTS_PER_MINUTE`. There are no tiers,
> no burst allowance, and no per-endpoint configuration. Variables like `RATE_LIMIT_ENABLED`,
> `RATE_LIMIT_WINDOW`, `RATE_LIMIT_STORAGE`, and `RATE_LIMIT_KEY_FUNC` are not part of the effective
> configuration.

### Monitoring and Observability

The real LLM observability surface is **Prometheus** (exposed at `GET /metrics`) plus **structlog**
structured logging. Prometheus series include `llm_call_duration_seconds`, `llm_tokens_total`,
`llm_cost_usd_total`, `llm_request_cost_drift_ratio`, and `llm_cost_drift_events_total`.

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `ENABLE_METRICS` | boolean | `true` | Enable Prometheus metrics collection (`/metrics` endpoint) | No |
| `ENABLE_TRACING` | boolean | `true` | Enable in-app tracing hooks. **Distinct from Langfuse** — this is not the Langfuse switch. | No |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | string | `http://localhost:4317` | OTLP exporter endpoint (consumed only where OTel export is wired) | No |

> The field names are `ENABLE_METRICS` / `ENABLE_TRACING` — not `METRICS_ENABLED` / `TRACING_ENABLED`.
> There are no `METRICS_PATH`, `TRACING_ENDPOINT`, `TRACING_SERVICE_NAME`, or `HEALTH_CHECK_INTERVAL`
> settings. There is no Grafana/Loki/Jaeger/CloudWatch/Sentry backbone; distributed tracing beyond
> Prometheus + structlog is the opt-in Langfuse integration documented above.

### Context Compaction Telemetry

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `ENABLE_CONTEXT_COMPACTION_TELEMETRY` | boolean | `false` | Enable INFO-level token telemetry at 4 context hotspots (working memory truncation, multi-tier memory recall, supervisor worker results, domain output synthesis). When `false`, no per-request tiktoken encoding occurs (zero runtime overhead); the tiktoken dependency is imported at process startup regardless. Set to `true` to observe token counts at each measurement point. | No |

### Multi-Domain Merge Configuration

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `MULTI_DOMAIN_MERGE_STRATEGY` | string | `concat` | Multi-domain result merge strategy (`concat` or `llm`) | No |
| `MULTI_DOMAIN_MERGE_PER_DOMAIN_CHAR_LIMIT` | integer | `4000` | Character limit per domain when using LLM synthesis | No |

**Merge Strategies:**

- **`concat`** (default): Labeled concatenation by domain. Each domain's output is stored under its domain key. Fast, deterministic, preserves all per-domain detail.
- **`llm`**: LLM synthesis composes per-domain outputs into one coherent answer. Uses the synthesis agent to integrate findings across domains. Falls back to `concat` on synthesis failure.

**Example:**
```bash
# Use default concatenation (fast, preserves all detail)
MULTI_DOMAIN_MERGE_STRATEGY=concat

# Use LLM synthesis for composed answers
MULTI_DOMAIN_MERGE_STRATEGY=llm
MULTI_DOMAIN_MERGE_PER_DOMAIN_CHAR_LIMIT=4000
```

### Security Configuration

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `CORS_ORIGINS` | list | `["http://localhost:3000","http://localhost:8000"]` | Allowed CORS origins. Env formats: JSON array or comma-separated. | No |

> `CORS_ORIGINS` is the only security knob under this heading that actually changes CORS behavior.
> `Settings` also declares `RATE_LIMIT_REQUESTS` (100) and `RATE_LIMIT_PERIOD` (60) under its
> `# Security Settings` comment, but they are **unconsumed** — the live limiter reads
> `ENABLE_RATE_LIMITING` / `MAX_REQUESTS_PER_MINUTE` (see [Rate Limiting Configuration](#rate-limiting-configuration)).
> There are no `CORS_ALLOW_METHODS` / `CORS_ALLOW_HEADERS`, no `CSRF_*`, no `SECURITY_HEADERS_ENABLED`,
> and no `HTTPS_ONLY` settings — those variables are ignored if set.

### Legacy / vestigial settings

| Variable | Type | Default | Status |
|----------|------|---------|--------|
| `TEMPORAL_HOST` | string | `localhost:7233` | **Dead.** Temporal has been removed; the in-process `DirectExecutionService` replaced it. This field still exists on `Settings` (`config.py:47`) but nothing on the query path reads it. |
| `TEMPORAL_NAMESPACE` | string | `default` | **Dead.** Same as above (`config.py:48`). |

> Temporal was removed from the live code (no `temporalio` dependency; execution now runs in-process
> via `DirectExecutionService`). Only `TEMPORAL_HOST` and `TEMPORAL_NAMESPACE` survive as vestigial
> settings; the former richer set of `TEMPORAL_*` variables (task queue, timeouts, retry policy) does
> **not** exist on `Settings` and has no effect.

## Configuration Files

### Application Configuration

All settings live on the single `Settings(BaseSettings)` class in `src/core/config.py`. There is no
`config/` package of layered setting classes — environment selection is driven by the `ENVIRONMENT`
variable, which gates production validators and debug behavior at runtime.

### Docker Environment File

**Location:** `.env`

```bash
# Core Application
ENVIRONMENT=development
DEBUG=false
LOG_LEVEL=INFO
SECRET_KEY=change-me-to-a-32+-char-random-string

# Database
DATABASE_URL=postgresql+asyncpg://research:research123@localhost:5432/research_db

# Redis
REDIS_URL=redis://localhost:6379/0

# Gemini AI (default provider)
GEMINI_API_KEY=your-gemini-api-key-here
GEMINI_DEFAULT_MODEL=gemini-pro

# JWT (RS256 — keys are PEM files, not a shared secret)
JWT_PRIVATE_KEY_PATH=/secrets/jwt_private.pem
JWT_PUBLIC_KEY_PATH=/secrets/jwt_public.pem

# Monitoring
ENABLE_METRICS=true
ENABLE_TRACING=true

# Rate limiting
ENABLE_RATE_LIMITING=true
MAX_REQUESTS_PER_MINUTE=100

# CORS
CORS_ORIGINS=["http://localhost:3000","http://localhost:8000"]
```

### CLI Configuration File

**Location:** `~/.research-cli.env`

```bash
# CLI Configuration (written by `research-cli config save`)
RESEARCH_API_URL=http://localhost:8000
RESEARCH_API_TIMEOUT=30
RESEARCH_OUTPUT_FORMAT=table
RESEARCH_VERBOSE=false
RESEARCH_COLOR=true
RESEARCH_MAX_RETRIES=3
# RESEARCH_API_KEY and RESEARCH_AUTH_TOKEN are written only when set
RESEARCH_AUTH_TOKEN=your-auth-token-here
```

> `CLIConfig.from_env` (`src/cli/config.py`) reads eight variables from this dotenv file:
> `RESEARCH_API_URL`, `RESEARCH_API_TIMEOUT`, `RESEARCH_OUTPUT_FORMAT`, `RESEARCH_VERBOSE`,
> `RESEARCH_COLOR`, `RESEARCH_MAX_RETRIES`, `RESEARCH_API_KEY`, and `RESEARCH_AUTH_TOKEN`.
> `config save` always writes the first six and adds `RESEARCH_API_KEY` / `RESEARCH_AUTH_TOKEN`
> only when they are set.

## CLI Configuration

The packaged entrypoints are `research-platform` and `research-cli` (defined in
`pyproject.toml [project.scripts]`). There is no `cerebro-cli` command and no PyPI package.

### Configuration Commands

```bash
# Show all configuration (or a single key)
research-cli config show
research-cli config show api_url

# Set a configuration value
research-cli config set api_url http://localhost:8000
research-cli config set output_format json
research-cli config set verbose true

# Save current configuration to ~/.research-cli.env
research-cli config save
```

> `config` supports `show`, `set`, and `save`. There is no `config reset` or `config load` subcommand.

### Global CLI Options

| Option | Environment Variable | Default | Description |
|--------|---------------------|---------|-------------|
| `--api-url` | `RESEARCH_API_URL` | `http://localhost:8000` | API base URL |
| `--format` | - | `table` | Output format (`table` / `json` / `yaml` / `csv`) |
| `--verbose` | - | `false` | Verbose output |
| `--no-color` | - | `false` | Disable colored output |

> These four are the only global options on the `research-cli` group. There is no `--api-key` or
> `--timeout` group option.

### Command Tree

- `research-cli config <show|set|save>`
- `research-cli health` — checks `/health` and `/ready`
- `research-cli completion <bash|zsh|fish>`
- `research-cli agents <query|route|estimate|execute|chain|status>`
- `research-cli projects <create|get|list|progress|cancel|results|refine>`

## Docker Configuration

### Docker Compose Environment

The development `docker-compose.yml` runs the API against Postgres 16 and Redis 7. There is **no
worker service** (`docker/Dockerfile.worker` does not exist) and **no Temporal services** —
execution is in-process. A standalone `masr-router` container exists in compose but is **legacy /
standalone**: it is not on the verified query path, which uses the in-process `MASRouter` object.

```yaml
# docker-compose.yml (trimmed to the query-path essentials)
services:
  api:
    build:
      context: .
      target: development
    environment:
      - ENVIRONMENT=${ENVIRONMENT:-development}
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
      - GEMINI_API_KEY=${GEMINI_API_KEY}
    env_file:
      - .env
    ports:
      - "8000:8000"
    depends_on:
      - postgres
      - redis

  postgres:
    image: postgres:16-alpine
    ports:
      - "5432:5432"

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
```

### Container Configuration

The real `Dockerfile` is **multi-stage** (`base` → `development` → `builder` → `production`), built
with `uv`, with base and runtime images **pinned by digest**. Production runs as a non-root `app`
user with `--workers 4`, exposes a `HEALTHCHECK` against `/health`, and uses an entrypoint that runs
database migrations before starting the server.

```dockerfile
# Dockerfile (abridged — real file is multi-stage)

# Stage 1: base — digest-pinned python:3.11-slim + uv
FROM python:3.11-slim@sha256:<digest> as base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_SYSTEM_PYTHON=1
COPY --from=ghcr.io/astral-sh/uv:latest@sha256:<digest> /uv /usr/local/bin/uv
WORKDIR /app

# Stage 2: development — installs .[dev], runs migrations then uvicorn --reload
FROM base as development
RUN uv pip install -e ".[dev]"
COPY src/ ./src/
COPY docker/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]   # runs `alembic` migrations, then exec's CMD
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# Stage 3: builder — installs production deps only
FROM base as builder
RUN uv pip install -e .
COPY src/ ./src/

# Stage 4: production — non-root user, health check, 4 workers
FROM python:3.11-slim@sha256:<digest> as production
RUN groupadd -r app && useradd -r -g app app
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /app/src ./src
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

## Kubernetes Configuration

The Kubernetes manifests live under `k8s/` in the `research-platform` namespace. Remember that
unknown environment keys are silently ignored (`extra="ignore"`), so a ConfigMap should set only
fields that actually exist on `Settings`.

### ConfigMap

```yaml
# k8s/configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: research-platform-config
  namespace: research-platform
data:
  ENVIRONMENT: "production"
  LOG_LEVEL: "INFO"
  ENABLE_METRICS: "true"
  ENABLE_RATE_LIMITING: "true"
  MAX_REQUESTS_PER_MINUTE: "100"
  # NOTE: The shipped configmap still sets TEMPORAL_NAMESPACE — this is a
  # vestige of the removed Temporal era and has no effect. Do not rely on it.
  # (Full key set in k8s/configmap.yaml also includes API_HOST, API_PORT,
  # API_WORKERS, WORKER_CONCURRENCY, TASK_TIMEOUT_SECONDS, MCP_PORT,
  # MCP_TOOLS_ENABLED, ENABLE_TRACING, ENABLE_CACHE, and OTEL_EXPORTER_OTLP_ENDPOINT.)
```

### Secrets

There is **no committed `k8s/secrets.yaml`**. Secrets are declared in `k8s/external-secrets.yaml` as
an `ExternalSecret` (External Secrets Operator), which materializes the `research-platform-secrets`
Secret from a cloud secret store via the `research-platform-secret-store` `ClusterSecretStore`. It
replaces the previously committed `k8s/secrets.yaml`.

```yaml
# k8s/external-secrets.yaml (abridged)
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: research-platform-secrets
  namespace: research-platform
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: research-platform-secret-store
    kind: ClusterSecretStore
  target:
    name: research-platform-secrets
    creationPolicy: Owner
    deletionPolicy: Retain
  data:
    - secretKey: GEMINI_API_KEY
      remoteRef: { key: research-platform/gemini-api-key }
    - secretKey: SECRET_KEY
      remoteRef: { key: research-platform/secret-key }
    - secretKey: JWT_SECRET_KEY
      remoteRef: { key: research-platform/jwt-secret-key }
    - secretKey: DATABASE_URL
      remoteRef: { key: research-platform/database-url }
    - secretKey: REDIS_URL
      remoteRef: { key: research-platform/redis-url }
    - secretKey: TEMPORAL_HOST
      remoteRef: { key: research-platform/temporal-host }
```

> The materialized `research-platform-secrets` key set is `GEMINI_API_KEY`, `SECRET_KEY`,
> `JWT_SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`, and `TEMPORAL_HOST`. Note that `JWT_SECRET_KEY` is
> **vestigial**: `Settings` has no such field, so the value is silently ignored (`extra="ignore"`).
> Actual JWT signing uses PEM files mounted at `/secrets/jwt_private.pem` and
> `/secrets/jwt_public.pem` (see the Authentication section). `TEMPORAL_HOST` is likewise a vestige
> of the removed Temporal era.

### Deployment Configuration

```yaml
# k8s/deployment-api.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: research-api
  namespace: research-platform
spec:
  replicas: 3
  selector:
    matchLabels:
      app: research-api
  template:
    metadata:
      labels:
        app: research-api
    spec:
      containers:
      - name: api
        image: gcr.io/PROJECT_ID/research-platform-api:latest
        envFrom:
        - configMapRef:
            name: research-platform-config
        - secretRef:
            name: research-platform-secrets
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "2Gi"
            cpu: "1000m"
```

> The manifests also include a `research-worker` deployment. It is a **vestige of the removed
> Temporal era** — there is no worker entrypoint module — and should not be treated as an active
> component of the query path.

## Configuration Examples

### Development Environment

```bash
# .env.development
ENVIRONMENT=development
DEBUG=true
LOG_LEVEL=DEBUG
SECRET_KEY=dev-only-secret-at-least-32-characters-long

DATABASE_URL=postgresql+asyncpg://research:research123@localhost:5432/research_db
REDIS_URL=redis://localhost:6379/0

GEMINI_API_KEY=your-dev-api-key
GEMINI_DEFAULT_MODEL=gemini-pro

CORS_ORIGINS=["http://localhost:3000","http://localhost:8000"]

ENABLE_METRICS=true
ENABLE_TRACING=true
```

### Staging Environment

```bash
# .env.staging
ENVIRONMENT=staging
DEBUG=false
LOG_LEVEL=INFO
SECRET_KEY=staging-secret-at-least-32-characters-long

DATABASE_URL=postgresql+asyncpg://user:pass@staging-db:5432/research_db
REDIS_URL=redis://staging-redis:6379/0

GEMINI_API_KEY=your-staging-api-key
GEMINI_DEFAULT_MODEL=gemini-pro

CORS_ORIGINS=["https://staging.research-platform.ai"]

ENABLE_METRICS=true
ENABLE_TRACING=true
```

### Production Environment

```bash
# .env.production
ENVIRONMENT=production
DEBUG=false
LOG_LEVEL=WARNING
# In production the SECRET_KEY validator requires a real (non-placeholder),
# 32+ character value, and DATABASE_URL must not use default credentials.
SECRET_KEY=<32+ char secret from a secrets manager>

DATABASE_URL=postgresql+asyncpg://user:pass@prod-db:5432/research_db
REDIS_URL=redis://prod-redis:6379/0

GEMINI_API_KEY=your-production-api-key
GEMINI_DEFAULT_MODEL=gemini-pro

CORS_ORIGINS=["https://research-platform.ai"]

ENABLE_RATE_LIMITING=true
MAX_REQUESTS_PER_MINUTE=100

ENABLE_METRICS=true
ENABLE_TRACING=true
```

## Security Considerations

### Sensitive Information

**Never commit these to version control:**
- `GEMINI_API_KEY`
- `SECRET_KEY`
- `OPENROUTER_API_KEY` (only relevant if multi-provider routing is enabled)
- `DATABASE_URL` (with credentials)
- `REDIS_URL` (with password)
- JWT private key (`/secrets/jwt_private.pem`)

### Startup Validation

`Settings` enforces two production guardrails at startup (`src/core/config.py`):

- **`SECRET_KEY`**: must not be the `MUST_SET_IN_ENV` placeholder in production, and must be at least
  32 characters in every environment.
- **`DATABASE_URL`**: in production, rejects known default credential patterns
  (`research:research123`, `postgres:postgres`, `:password@`).
- **`CONSTRAINT_TYPES`**: every element must be one of `routing`, `qa`, `format`, `security`;
  an unrecognised value fails startup.

### Best Practices

1. **Use environment-specific files:**
   ```bash
   .env.development  # Development settings
   .env.staging      # Staging settings
   .env.production   # Production settings (use secrets management)
   ```

2. **Secrets Management** — Kubernetes Secrets, AWS Secrets Manager, HashiCorp Vault, or an external
   secrets operator. In `k8s/`, secrets are sourced into `research-platform-secrets`.

3. **Generate a strong `SECRET_KEY`:**
   ```bash
   python -c 'import secrets; print(secrets.token_urlsafe(32))'
   ```

4. **Access Control:**
   ```bash
   # Restrict permissions on env files and PEM keys
   chmod 600 .env* /secrets/jwt_*.pem
   ```

> Remember: because `Settings` uses `extra="ignore"`, a misspelled or obsolete security variable will
> be **silently discarded** rather than rejected. Verify every variable name against the fields in
> `src/core/config.py`.
