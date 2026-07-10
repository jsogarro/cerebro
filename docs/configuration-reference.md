# Configuration Reference

This document provides a comprehensive reference for all configuration options available in the Multi-Agent Research Platform.

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
| `ENVIRONMENT` | string | `development` | Deployment environment (development/staging/production) | No |
| `DEBUG` | boolean | `false` | Enable debug mode | No |
| `LOG_LEVEL` | string | `INFO` | Logging level (DEBUG/INFO/WARNING/ERROR/CRITICAL) | No |
| `HOST` | string | `0.0.0.0` | Server host binding | No |
| `PORT` | integer | `8000` | Server port | No |
| `WORKERS` | integer | `1` | Number of worker processes | No |

### Database Configuration

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `DATABASE_URL` | string | - | PostgreSQL connection string | Yes |
| `DATABASE_POOL_SIZE` | integer | `10` | Database connection pool size | No |
| `DATABASE_MAX_OVERFLOW` | integer | `10` | Maximum overflow connections | No |
| `DATABASE_POOL_TIMEOUT` | integer | `30` | Connection timeout (seconds) | No |
| `DATABASE_POOL_RECYCLE` | integer | `3600` | Connection recycle time (seconds) | No |
| `DATABASE_ECHO` | boolean | `false` | Log SQL queries | No |
| `DATABASE_ECHO_POOL` | boolean | `false` | Log connection pool events | No |

**Database URL Format:**
```bash
# Standard format
DATABASE_URL=postgresql+asyncpg://username:password@host:port/database

# With SSL
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db?ssl=require

# Connection parameters
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db?pool_size=20&max_overflow=30
```

### Redis Configuration

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `REDIS_URL` | string | `redis://localhost:6379/0` | Redis connection string | No |
| `REDIS_MAX_CONNECTIONS` | integer | `10` | Maximum Redis connections | No |
| `REDIS_RETRY_ON_TIMEOUT` | boolean | `true` | Retry on timeout | No |
| `REDIS_DECODE_RESPONSES` | boolean | `true` | Decode responses to strings | No |
| `REDIS_HEALTH_CHECK_INTERVAL` | integer | `30` | Health check interval (seconds) | No |

**Redis URL Format:**
```bash
# Basic Redis
REDIS_URL=redis://localhost:6379/0

# With authentication
REDIS_URL=redis://username:password@host:6379/0

# Redis Sentinel
REDIS_URL=redis+sentinel://host:26379/mymaster

# Redis Cluster
REDIS_URL=redis://host1:6379,host2:6379,host3:6379/0
```

### Temporal Configuration

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `TEMPORAL_HOST` | string | `localhost:7233` | Temporal server address | No |
| `TEMPORAL_NAMESPACE` | string | `default` | Temporal namespace | No |
| `TEMPORAL_TASK_QUEUE` | string | `research-queue` | Default task queue name | No |
| `TEMPORAL_CLIENT_TIMEOUT` | integer | `30` | Client timeout (seconds) | No |
| `TEMPORAL_WORKFLOW_TIMEOUT` | integer | `3600` | Default workflow timeout (seconds) | No |
| `TEMPORAL_ACTIVITY_TIMEOUT` | integer | `300` | Default activity timeout (seconds) | No |
| `TEMPORAL_RETRY_POLICY_MAX_ATTEMPTS` | integer | `3` | Maximum retry attempts | No |
| `TEMPORAL_RETRY_POLICY_BACKOFF` | float | `2.0` | Retry backoff coefficient | No |

### AI Service Configuration (Gemini)

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `GEMINI_API_KEY` | string | - | Google Gemini API key | Yes |
| `GEMINI_MODEL` | string | `gemini-1.5-flash` | Gemini model name | No |
| `GEMINI_TEMPERATURE` | float | `0.7` | Response temperature | No |
| `GEMINI_MAX_TOKENS` | integer | `8192` | Maximum tokens per request | No |
| `GEMINI_RATE_LIMIT` | integer | `60` | Requests per minute | No |
| `GEMINI_TIMEOUT` | integer | `30` | Request timeout (seconds) | No |
| `GEMINI_RETRY_MAX` | integer | `3` | Maximum retries | No |
| `GEMINI_RETRY_BACKOFF` | float | `2.0` | Retry backoff coefficient | No |
| `GEMINI_CACHE_ENABLED` | boolean | `true` | Enable response caching | No |
| `GEMINI_CACHE_TTL` | integer | `3600` | Cache TTL (seconds) | No |

### AI Brain Platform Configuration

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

**How to enable**: Set `OPENROUTER_API_KEY` in environment, then set `MULTI_PROVIDER_ROUTING_ENABLED=true`. When disabled or API key is absent, all requests fall back to `GeminiService` with byte-for-byte prior behavior preserved.

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

#### Multi-Domain Execution (PR #54)

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `MAX_DOMAIN_PARALLELISM` | integer | `4` | Maximum concurrent domain supervisors in multi-domain queries | No |

**Note**: This is a code constant in `src/api/services/direct_execution_service.py`, not an environment variable. Multi-domain execution is always enabled; this constant bounds concurrency to prevent resource exhaustion.

**Behavior**: When a query spans multiple domains, the system:
1. Decomposes the query into per-domain sub-queries
2. Dispatches up to `MAX_DOMAIN_PARALLELISM` domain supervisors concurrently (via `asyncio.Semaphore`)
3. Merges results with `_merge_domain_results()` (labeled concatenation; future: LLM synthesis)
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

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `JWT_SECRET_KEY` | string | - | JWT signing secret | Yes |
| `JWT_ALGORITHM` | string | `HS256` | JWT signing algorithm | No |
| `JWT_ACCESS_TOKEN_EXPIRE` | integer | `1440` | Access token expiry (minutes) | No |
| `JWT_REFRESH_TOKEN_EXPIRE` | integer | `10080` | Refresh token expiry (minutes) | No |
| `PASSWORD_MIN_LENGTH` | integer | `8` | Minimum password length | No |
| `PASSWORD_REQUIRE_UPPERCASE` | boolean | `true` | Require uppercase letters | No |
| `PASSWORD_REQUIRE_LOWERCASE` | boolean | `true` | Require lowercase letters | No |
| `PASSWORD_REQUIRE_DIGITS` | boolean | `true` | Require digits | No |
| `PASSWORD_REQUIRE_SPECIAL` | boolean | `true` | Require special characters | No |
| `API_KEY_LENGTH` | integer | `32` | API key length | No |
| `API_KEY_PREFIX` | string | `rp_` | API key prefix | No |

### Rate Limiting Configuration

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `RATE_LIMIT_ENABLED` | boolean | `true` | Enable rate limiting | No |
| `RATE_LIMIT_REQUESTS` | integer | `100` | Requests per window | No |
| `RATE_LIMIT_WINDOW` | integer | `60` | Time window (seconds) | No |
| `RATE_LIMIT_STORAGE` | string | `redis` | Storage backend (memory/redis) | No |
| `RATE_LIMIT_KEY_FUNC` | string | `ip` | Rate limit key function (ip/user/api_key) | No |

### Caching Configuration

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `CACHE_ENABLED` | boolean | `true` | Enable application caching | No |
| `CACHE_BACKEND` | string | `redis` | Cache backend (memory/redis) | No |
| `CACHE_DEFAULT_TTL` | integer | `300` | Default cache TTL (seconds) | No |
| `CACHE_KEY_PREFIX` | string | `rp:` | Cache key prefix | No |
| `CACHE_COMPRESSION` | boolean | `true` | Enable cache compression | No |
| `CACHE_MAX_CONNECTIONS` | integer | `50` | Maximum cache connections | No |

### File Storage Configuration

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `STORAGE_BACKEND` | string | `local` | Storage backend (local/s3/gcs) | No |
| `STORAGE_LOCAL_PATH` | string | `/tmp/uploads` | Local storage path | No |
| `STORAGE_S3_BUCKET` | string | - | S3 bucket name | No |
| `STORAGE_S3_REGION` | string | `us-east-1` | S3 region | No |
| `STORAGE_S3_ACCESS_KEY` | string | - | S3 access key | No |
| `STORAGE_S3_SECRET_KEY` | string | - | S3 secret key | No |
| `STORAGE_S3_ENDPOINT` | string | - | Custom S3 endpoint | No |
| `STORAGE_GCS_BUCKET` | string | - | GCS bucket name | No |
| `STORAGE_GCS_CREDENTIALS` | string | - | GCS credentials JSON | No |

### Monitoring and Observability

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `METRICS_ENABLED` | boolean | `true` | Enable Prometheus metrics | No |
| `METRICS_PATH` | string | `/metrics` | Metrics endpoint path | No |
| `TRACING_ENABLED` | boolean | `false` | Enable OpenTelemetry tracing | No |
| `TRACING_ENDPOINT` | string | - | Tracing collector endpoint | No |
| `TRACING_SERVICE_NAME` | string | `research-platform` | Service name for tracing | No |
| `HEALTH_CHECK_INTERVAL` | integer | `30` | Health check interval (seconds) | No |

### Email Configuration

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `EMAIL_ENABLED` | boolean | `false` | Enable email notifications | No |
| `EMAIL_SMTP_HOST` | string | - | SMTP server host | No |
| `EMAIL_SMTP_PORT` | integer | `587` | SMTP server port | No |
| `EMAIL_SMTP_USERNAME` | string | - | SMTP username | No |
| `EMAIL_SMTP_PASSWORD` | string | - | SMTP password | No |
| `EMAIL_SMTP_TLS` | boolean | `true` | Use TLS encryption | No |
| `EMAIL_FROM_ADDRESS` | string | - | Default sender address | No |
| `EMAIL_FROM_NAME` | string | `Research Platform` | Default sender name | No |

### WebSocket Configuration

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `WEBSOCKET_ENABLED` | boolean | `true` | Enable WebSocket support | No |
| `WEBSOCKET_PATH` | string | `/ws` | WebSocket endpoint path | No |
| `WEBSOCKET_MAX_CONNECTIONS` | integer | `100` | Maximum concurrent connections | No |
| `WEBSOCKET_PING_INTERVAL` | integer | `20` | Ping interval (seconds) | No |
| `WEBSOCKET_PING_TIMEOUT` | integer | `10` | Ping timeout (seconds) | No |
| `WEBSOCKET_MESSAGE_MAX_SIZE` | integer | `1048576` | Maximum message size (bytes) | No |

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
| `CORS_ENABLED` | boolean | `true` | Enable CORS | No |
| `CORS_ALLOW_ORIGINS` | list | `["*"]` | Allowed origins (comma-separated) | No |
| `CORS_ALLOW_METHODS` | list | `["*"]` | Allowed methods (comma-separated) | No |
| `CORS_ALLOW_HEADERS` | list | `["*"]` | Allowed headers (comma-separated) | No |
| `CSRF_ENABLED` | boolean | `false` | Enable CSRF protection | No |
| `CSRF_SECRET_KEY` | string | - | CSRF secret key | No |
| `SECURITY_HEADERS_ENABLED` | boolean | `true` | Enable security headers | No |
| `HTTPS_ONLY` | boolean | `false` | Force HTTPS only | No |

### CLI Configuration

| Variable | Type | Default | Description | Required |
|----------|------|---------|-------------|----------|
| `RESEARCH_API_URL` | string | `http://localhost:8000` | API base URL | No |
| `RESEARCH_API_KEY` | string | - | API key for authentication | No |
| `RESEARCH_API_TIMEOUT` | integer | `30` | Request timeout (seconds) | No |
| `RESEARCH_OUTPUT_FORMAT` | string | `table` | Default output format | No |
| `RESEARCH_VERBOSE` | boolean | `false` | Enable verbose output | No |
| `RESEARCH_COLOR` | boolean | `true` | Enable colored output | No |
| `RESEARCH_MAX_RETRIES` | integer | `3` | Maximum request retries | No |

## Configuration Files

### Application Configuration

**Location:** `config/`

```python
# config/base.py
class Settings:
    # Core settings
    environment: str = "development"
    debug: bool = False
    
    # Database settings
    database_url: str
    database_pool_size: int = 10
    
    # Redis settings
    redis_url: str = "redis://localhost:6379/0"
    
    # API settings
    api_host: str = "0.0.0.0"
    api_port: int = 8000
```

### Docker Environment File

**Location:** `.env`

```bash
# Core Application
ENVIRONMENT=development
DEBUG=false
LOG_LEVEL=INFO

# Database
DATABASE_URL=postgresql+asyncpg://research:research123@localhost:5432/research_db
DATABASE_POOL_SIZE=10
DATABASE_MAX_OVERFLOW=10

# Redis
REDIS_URL=redis://localhost:6379/0
REDIS_MAX_CONNECTIONS=10

# Temporal
TEMPORAL_HOST=localhost:7233
TEMPORAL_NAMESPACE=default
TEMPORAL_TASK_QUEUE=research-queue

# Gemini AI
GEMINI_API_KEY=your-gemini-api-key-here
GEMINI_MODEL=gemini-1.5-flash
GEMINI_TEMPERATURE=0.7
GEMINI_RATE_LIMIT=60

# Authentication
JWT_SECRET_KEY=your-super-secret-jwt-key-here
JWT_ACCESS_TOKEN_EXPIRE=1440

# Storage
STORAGE_BACKEND=local
STORAGE_LOCAL_PATH=/tmp/uploads

# Monitoring
METRICS_ENABLED=true
TRACING_ENABLED=false

# Security
CORS_ENABLED=true
CORS_ALLOW_ORIGINS=http://localhost:3000,http://localhost:8080
```

### CLI Configuration File

**Location:** `~/.research-cli.env` or `.env.cli`

```bash
# CLI Configuration
RESEARCH_API_URL=http://localhost:8000
RESEARCH_API_KEY=your-api-key-here
RESEARCH_API_TIMEOUT=30
RESEARCH_OUTPUT_FORMAT=table
RESEARCH_VERBOSE=false
RESEARCH_COLOR=true
RESEARCH_MAX_RETRIES=3

# Authentication
RESEARCH_USERNAME=your-username
RESEARCH_PASSWORD=your-password

# Output Settings
RESEARCH_PAGER=less
RESEARCH_EDITOR=vim
```

## CLI Configuration

### Configuration Commands

```bash
# Show current configuration
research-cli config show

# Set configuration values
research-cli config set api_url http://localhost:8000
research-cli config set output_format json
research-cli config set verbose true

# Reset configuration
research-cli config reset

# Save current configuration
research-cli config save

# Load configuration from file
research-cli config load ~/.research-cli-backup.env
```

### Global CLI Options

| Option | Environment Variable | Default | Description |
|--------|---------------------|---------|-------------|
| `--api-url` | `RESEARCH_API_URL` | `http://localhost:8000` | API base URL |
| `--api-key` | `RESEARCH_API_KEY` | - | API authentication key |
| `--format` | `RESEARCH_OUTPUT_FORMAT` | `table` | Output format |
| `--verbose` | `RESEARCH_VERBOSE` | `false` | Verbose output |
| `--no-color` | `RESEARCH_COLOR=false` | `false` | Disable colors |
| `--timeout` | `RESEARCH_API_TIMEOUT` | `30` | Request timeout |

## Docker Configuration

### Docker Compose Environment

```yaml
# docker-compose.yml
version: '3.8'

services:
  api:
    build: .
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
      - temporal

  worker:
    build:
      context: .
      dockerfile: docker/Dockerfile.worker
    environment:
      - ENVIRONMENT=${ENVIRONMENT:-development}
      - DATABASE_URL=${DATABASE_URL}
      - TEMPORAL_HOST=${TEMPORAL_HOST}
    env_file:
      - .env
    depends_on:
      - postgres
      - temporal
```

### Container Configuration

```dockerfile
# Dockerfile
FROM python:3.11-slim

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH="/app/.venv/bin:$PATH"

# Application configuration
ENV HOST=0.0.0.0
ENV PORT=8000
ENV WORKERS=1

WORKDIR /app
COPY . .
RUN pip install -e ".[dev]"

EXPOSE 8000
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## Kubernetes Configuration

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
  HOST: "0.0.0.0"
  PORT: "8000"
  DATABASE_POOL_SIZE: "20"
  REDIS_MAX_CONNECTIONS: "20"
  TEMPORAL_NAMESPACE: "production"
  GEMINI_MODEL: "gemini-1.5-flash"
  METRICS_ENABLED: "true"
  CACHE_ENABLED: "true"
  RATE_LIMIT_ENABLED: "true"
```

### Secrets

```yaml
# k8s/secrets.yaml
apiVersion: v1
kind: Secret
metadata:
  name: research-platform-secrets
  namespace: research-platform
type: Opaque
data:
  DATABASE_URL: <base64-encoded-database-url>
  GEMINI_API_KEY: <base64-encoded-gemini-key>
  JWT_SECRET_KEY: <base64-encoded-jwt-secret>
  REDIS_URL: <base64-encoded-redis-url>
```

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
        image: research-platform:latest
        envFrom:
        - configMapRef:
            name: research-platform-config
        - secretRef:
            name: research-platform-secrets
        resources:
          requests:
            memory: "512Mi"
            cpu: "500m"
          limits:
            memory: "1Gi"
            cpu: "1000m"
```

## Configuration Examples

### Development Environment

```bash
# .env.development
ENVIRONMENT=development
DEBUG=true
LOG_LEVEL=DEBUG

DATABASE_URL=postgresql+asyncpg://research:research123@localhost:5432/research_db
DATABASE_ECHO=true

REDIS_URL=redis://localhost:6379/0

GEMINI_API_KEY=your-dev-api-key
GEMINI_RATE_LIMIT=30

CORS_ALLOW_ORIGINS=http://localhost:3000,http://localhost:8080

METRICS_ENABLED=true
TRACING_ENABLED=true
```

### Staging Environment

```bash
# .env.staging
ENVIRONMENT=staging
DEBUG=false
LOG_LEVEL=INFO

DATABASE_URL=postgresql+asyncpg://user:pass@staging-db:5432/research_db
DATABASE_POOL_SIZE=15

REDIS_URL=redis://staging-redis:6379/0

GEMINI_API_KEY=your-staging-api-key
GEMINI_RATE_LIMIT=45

CORS_ALLOW_ORIGINS=https://staging.research-platform.ai

STORAGE_BACKEND=s3
STORAGE_S3_BUCKET=research-platform-staging

METRICS_ENABLED=true
TRACING_ENABLED=true
```

### Production Environment

```bash
# .env.production
ENVIRONMENT=production
DEBUG=false
LOG_LEVEL=WARNING

DATABASE_URL=postgresql+asyncpg://user:pass@prod-db:5432/research_db
DATABASE_POOL_SIZE=25
DATABASE_MAX_OVERFLOW=50

REDIS_URL=redis://prod-redis:6379/0
REDIS_MAX_CONNECTIONS=30

GEMINI_API_KEY=your-production-api-key
GEMINI_RATE_LIMIT=100

CORS_ALLOW_ORIGINS=https://research-platform.ai

STORAGE_BACKEND=s3
STORAGE_S3_BUCKET=research-platform-production

RATE_LIMIT_ENABLED=true
RATE_LIMIT_REQUESTS=1000
RATE_LIMIT_WINDOW=3600

METRICS_ENABLED=true
TRACING_ENABLED=true
HTTPS_ONLY=true
```

## Security Considerations

### Sensitive Information

**Never commit these to version control:**
- `GEMINI_API_KEY`
- `JWT_SECRET_KEY`
- `DATABASE_URL` (with credentials)
- `REDIS_URL` (with password)
- Email credentials
- Storage access keys

### Best Practices

1. **Use environment-specific files:**
   ```bash
   .env.development  # Development settings
   .env.staging      # Staging settings
   .env.production   # Production settings (use secrets management)
   ```

2. **Secrets Management:**
   ```bash
   # Use secret management tools
   - Kubernetes Secrets
   - AWS Secrets Manager
   - HashiCorp Vault
   - Azure Key Vault
   ```

3. **Environment Variable Validation:**
   ```python
   from pydantic import validator
   
   class Settings:
       gemini_api_key: str
       
       @validator('gemini_api_key')
       def validate_api_key(cls, v):
           if not v or v == 'your-api-key-here':
               raise ValueError('Invalid Gemini API key')
           return v
   ```

4. **Configuration Encryption:**
   ```bash
   # Encrypt sensitive configuration files
   gpg --cipher-algo AES256 --compress-algo 1 --symmetric .env.production
   ```

5. **Access Control:**
   ```bash
   # Set proper file permissions
   chmod 600 .env*
   chown app:app .env*
   ```

### Configuration Validation

Create a validation script:

```python
#!/usr/bin/env python3
# scripts/validate_config.py

import os
import sys
from urllib.parse import urlparse

def validate_database_url(url):
    parsed = urlparse(url)
    if not all([parsed.scheme, parsed.hostname, parsed.username]):
        raise ValueError(f"Invalid database URL: {url}")

def validate_redis_url(url):
    parsed = urlparse(url)
    if not parsed.scheme.startswith('redis'):
        raise ValueError(f"Invalid Redis URL: {url}")

def validate_config():
    errors = []
    
    # Required variables
    required_vars = [
        'DATABASE_URL',
        'GEMINI_API_KEY',
        'JWT_SECRET_KEY'
    ]
    
    for var in required_vars:
        if not os.getenv(var):
            errors.append(f"Missing required variable: {var}")
    
    # Validate URLs
    try:
        validate_database_url(os.getenv('DATABASE_URL', ''))
    except ValueError as e:
        errors.append(str(e))
    
    try:
        validate_redis_url(os.getenv('REDIS_URL', ''))
    except ValueError as e:
        errors.append(str(e))
    
    if errors:
        print("Configuration validation failed:")
        for error in errors:
            print(f"  - {error}")
        sys.exit(1)
    else:
        print("Configuration validation passed!")

if __name__ == "__main__":
    validate_config()
```