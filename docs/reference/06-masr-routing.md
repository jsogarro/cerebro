# MASR routing

MASR (Multi-Agent System Router) turns a query into a routing decision: a complexity analysis, a collaboration mode, a worker allocation, a model recommendation and cost, latency and quality estimates. One `MASRouter` instance is created at application startup by `MASRRuntime` and shared by the research execution path, the TalkHier session service and the `/api/v1/masr` endpoints.

## Summary

| Feature | Status | Entry point | Source |
|---|---|---|---|
| Query analysis and complexity scoring | Active | `MASRouter.route` | `src/ai_brain/router/query_analyzer.py` (`QueryComplexityAnalyzer`) |
| Multi-domain decomposition | Active (analysis only) | `QueryComplexityAnalyzer.analyze` | `src/ai_brain/router/query_decomposer.py` (`QueryDecomposer`) |
| `MASR_COMPLEXITY_WEIGHTS`, `MASR_COMPLEXITY_THRESHOLDS` | Active | `settings_to_router_config` | `src/ai_brain/router/factory.py` (`settings_to_router_config`) |
| Collaboration mode selection and fast path | Active | `MASRouter.route` | `src/ai_brain/router/masr.py` (`MASRouter._determine_collaboration_mode`) |
| Routing strategies | Active | `MASRouter.route`, `POST /api/v1/masr/route` | `src/ai_brain/router/routing_types.py` (`RoutingStrategy`) |
| Model selection and cost estimate | Active | `MASRouter.route` | `src/ai_brain/router/cost_optimizer.py` (`CostOptimizer`) |
| `COST_MAX_PER_REQUEST` | Active | `settings_to_router_config` | `src/ai_brain/router/cost_optimizer.py` (`CostOptimizer.max_cost_per_request`) |
| Hierarchical cost model | Not wired | none | `src/ai_brain/router/hierarchical_cost_model.py` (`HierarchicalCostOptimizer`) |
| Routing decision cache | Active | `MASRouter.route` | `src/ai_brain/router/routing_cache.py` (`RoutingCacheManager`) |
| Routing circuit breaker | Active | `MASRouter.route` | `src/core/tools/circuit.py` (`CircuitBreaker`) |
| MASR HTTP API (8 routes) | Active | `/api/v1/masr/*` | `src/api/routes/masr_api.py` (`router`) |
| `POST /api/v1/masr/feedback` learning | Not wired | `MASRRoutingService.submit_feedback` | `src/api/services/masr_routing_service.py` (`SupervisionFeedbackLearner`) |
| History-based strategy heuristic | Active | `MASRouter._select_routing_strategy` | `src/ai_brain/router/routing_metrics.py` (`RoutingMetricsCollector.get_adaptive_strategy`) |
| Adaptive worker allocation (Thompson sampling) | Off by default | `ADAPTIVE_ROUTING_ENABLED=true` | `src/ai_brain/router/masr.py` (`MASRouter._get_adaptive_allocation_adjustment`) |
| Memory-informed routing | Not wired | `MEMORY_INFORMED_ROUTING_ENABLED=true` | `src/ai_brain/router/masr.py` (`MASRouter._get_episodic_routing_prior`) |
| Routing outcome recorder | Not wired | none | `src/ai_brain/router/outcome_recorder.py` (`RoutingOutcomeRecorder`) |
| Adaptive routing metrics | Active | Prometheus registry | `src/ai_brain/router/routing_observability.py` |

## Role in execution

`MASRouter.route` returns a `RoutingDecision` (`src/ai_brain/router/masr.py`). The research execution path treats that decision as a proposal: `DirectExecutionService._propose_routing` asks MASR for it, and `ExecutionPlanCompiler.compile` (`src/ai_brain/router/execution_plan_compiler.py`) compares it with the trusted execution authority and rejects any mismatch in strategy, collaboration mode, worker types, supervisor type (not checked for `fast_path` and `direct`, which have no supervisor node), domains or provider and model routes. MASR cannot supply the worker list, provider policy or budget that the compiled `ExecutionPlan` carries. See [research execution](05-research-execution.md).

`route` catches every `Exception` raised while routing, including a rejection by an open circuit breaker, and `_create_fallback_decision` returns a `DIRECT` decision with one worker of type `general`, supervisor type `general`, `confidence_score` 0.3, `fallback_strategy` `error_recovery` and the `llama3.3:70b` model specification.

## Query analysis

`QueryComplexityAnalyzer.analyze` (`src/ai_brain/router/query_analyzer.py`) lower-cases and normalises the query, scores seven factors (linguistic, reasoning, domain, data, output, time, quality) and combines them as a weighted sum capped at 1.0. The analyzer's default weights are 0.15, 0.25, 0.20, 0.15, 0.15, 0.05 and 0.05. With the default thresholds a score below 0.12 is `simple`, below 0.22 is `moderate` and anything higher is `complex` (`ComplexityLevel`). The analysis also carries:

- `domains`: a list of `QueryDomain` values (`research`, `content`, `analytics`, `service`, `general`, `multimodal`, `finance`) matched by regular expressions.
- `subtask_count`, `uncertainty`, `reasoning_types`, `recommended_agents`, `estimated_tokens` and `priority_level` (`low`, `normal`, `high`, `critical`).

`settings_to_router_config` maps `MASR_COMPLEXITY_WEIGHTS` and `MASR_COMPLEXITY_THRESHOLDS` into the analyzer's `complexity_weights` and `complexity_thresholds` keys. The `simple` and `moderate` thresholds default to 0.12 and 0.22.

`QueryDecomposer.decompose_query` runs when the analysis finds more than one domain and `general` is not among them. It matches keyword lists for five domains (`research`, `content`, `analytics`, `service`, `finance`) and returns a `QueryDecomposition` with per-domain sub-queries, a `research` to `content` dependency when both are detected and the `is_multi_domain` property. The result is stored on `ComplexityAnalysis.decomposition`. No production code reads `decomposition` or `is_multi_domain`, so the decomposition does not alter routing.

## Collaboration modes

`CollaborationMode` (`src/core/contracts/execution_plan.py`) has six values. `COLLABORATION_MODE_SUPPORT` marks four as implemented and two as explicitly rejected:

| Mode | Plan compilation | Notes |
|---|---|---|
| `fast_path` | Implemented | One registered worker, executed exactly like `direct` |
| `direct` | Implemented | One worker |
| `parallel` | Implemented | Workers run side by side |
| `hierarchical` | Implemented | Supervisor-style layout |
| `debate` | Explicitly rejected | `ExecutionPlanCompiler.compile` raises `ValueError("unsupported collaboration mode")` |
| `ensemble` | Explicitly rejected | Same rejection |

`MASRouter._determine_collaboration_mode` chooses a mode in this order:

1. `fast_path` when `_should_use_fast_path` is true: the setting `MASR_FAST_PATH_ENABLED` (default `true`, read at call time) is on, complexity is `simple`, exactly one domain and one subtask were found, `uncertainty` is at most 0.3 and the priority is not `critical`.
2. `direct` for any other `simple` query.
3. `parallel` when more than two domains were found.
4. `debate` when `uncertainty` is above 0.7.
5. `hierarchical` for a `complex` query.
6. `parallel` otherwise.

Rule 4 can return `debate` for a `moderate` or `complex` query with at most two domains, and a `debate` decision does not compile into an execution plan. No rule returns `ensemble`.

## Strategies

`RoutingStrategy` (`src/ai_brain/router/routing_types.py`) has five values: `speed_first`, `cost_efficient`, `quality_focused`, `balanced` and `adaptive`. `MASR_DEFAULT_STRATEGY` (default `balanced`) sets `MASRouter.default_strategy`.

Without an explicit strategy, `_select_routing_strategy` applies these rules in order:

1. `context["routing_strategy"]`, when the caller supplies it.
2. The history-based heuristic (below), when `MASR_ENABLE_ADAPTIVE` is on and more than 100 decisions are in the process-local history.
3. `speed_first` for `critical` priority.
4. `cost_efficient` for a `simple` query.
5. `quality_focused` for a `complex` query.
6. The default strategy.

The strategy maps to a cost-optimizer strategy: `speed_first` to `latency_optimized`, `cost_efficient` to `cost_minimized`, `quality_focused` to `performance_optimized` and both `balanced` and `adaptive` to `balanced`.

`MASR_ENABLE_ADAPTIVE` (default `true`) controls only the history-based heuristic in `RoutingMetricsCollector.get_adaptive_strategy`. It never enables the Thompson-sampling allocation described under [Adaptive routing](#adaptive-routing); `settings_to_router_config` documents this and only `ADAPTIVE_ROUTING_ENABLED` can enable it. The heuristic reads up to 10,000 retained decisions from the last 24 hours and returns the strategy with the best average confidence. Because it labels every decision `balanced`, it returns `balanced` once the history holds at least 100 decisions and no context override is given. When none of the retained decisions is recent, it returns the default strategy.

## Routing under a catalogue entry

`MASRouter.route(..., shape=WorkflowShape)` (`src/ai_brain/router/routing_types.py`) routes a catalogue launch. The entry (`WorkflowCatalogEntry.shape()`) fixes strategy, domain, collaboration mode, workers and provider allowlist; the optimizer only considers models whose provider is in the allowlist, no fallbacks are proposed, and the decision cache is bypassed. The query analysis decides only whether the query fits: a domain other than the entry's (or `general`) raises `WorkflowShapeUnsupportedError`, which `route` re-raises instead of returning its fallback decision and which does not count against the routing circuit breaker. Routing without `shape` is unchanged.

## Worker allocation

`MASRouter._allocate_agents` builds an `AgentAllocation` (`supervisor_type`, `worker_count`, `worker_types`, `max_parallel`, `timeout_seconds`, `retry_attempts`) from the mode:

| Mode | Worker count | Worker types |
|---|---|---|
| `fast_path` | 1 | one registered worker for the domain (`research`: `literature_review`, `content`: `drafting`, `analytics`: `data_analysis`, `finance`: `financial_analysis`); `general`, `service` and `multimodal` get none rather than an unregistered label; the optimizer's fallback models are cleared |
| `direct` | 1 | one per detected domain |
| `parallel` | `min(domains + 1, max_parallel_workers)`, then the strategy budget | one per detected domain |
| `hierarchical` | `min(subtask_count, max_agents)`, then the strategy budget | reasoning specialists, domain specialists and `validation_specialist` |
| `debate` | `min(3, strategy budget)` | `analyst`, `critic`, `synthesizer` |
| `ensemble` | 5, clamped to the range 3 to 7 | one per detected domain |

The strategy budget caps `parallel` and `hierarchical` counts (`_get_strategy_budget`):

| Strategy | `parallel` | `hierarchical` | `debate` | `ensemble` |
|---|---|---|---|---|
| `cost_efficient` | 2 | 2 | 3 | 2 |
| `speed_first` | 3 | 4 | 3 | 3 |
| `quality_focused` | 4 | 10 | 3 | 5 |
| `balanced`, `adaptive` | 3 | 6 | 3 | 3 |

The supervisor type comes from the first detected domain: `research`, `content`, `analytics`, `finance` and `service` map to themselves, `multimodal` maps to `content` and an unknown or missing domain maps to `research`. The worker type names are the strings in `MASRouter._get_domain_worker_types` (for example `research_specialist` and `financial_analysis`); they are routing labels and are not keys of the agent catalogue described in [agents and supervisors](07-agents-and-supervisors.md).

## Cost model and limits

`CostOptimizer.optimize` (`src/ai_brain/router/cost_optimizer.py`) filters candidate models and then picks one by the mapped strategy:

- Filter: estimated request cost at most `max_cost_per_request` (0.10), `avg_latency_ms` at most 5000, `quality_score` at least 0.7, context window at least twice the estimated tokens and domain strengths compatible with the detected domains. An empty candidate list raises `ValueError`, which `route` turns into the fallback decision.
- Pick: `cost_minimized` takes the cheapest, `performance_optimized` the highest quality, `latency_optimized` the lowest latency and `balanced` the best weighted score (cost 0.4, quality 0.4, latency 0.2).
- Up to two fallback models are chosen by availability and then cost.

The application constructs `MASRouter` with `model_config_manager=None` (`create_masr_router`), so the optimizer uses `_initialize_models_legacy`, a fixed set of three model specifications (`llama3.3:70b`, `deepseek-v3` and `gemini-pro`) whose `provider` values are the dispatchable adapter names `llama`, `deepseek` and `gemini`. The optimizer's thresholds are constructor defaults; no setting feeds them.

`MAX_PARALLEL_WORKERS` (default 5) and `AGENTS_MAX_WORKERS_PER_SUPERVISOR` (default 10) are mapped by `settings_to_router_config` to `max_parallel` and `max_agents`, which bound `parallel` and `hierarchical` allocations.

`COST_MAX_PER_REQUEST` (default 0.10) is mapped to the router key `max_cost` (stored as `MASRouter.cost_threshold`, which nothing reads) and to `cost_optimizer.max_cost_per_request`, the per-request ceiling `CostOptimizer` applies when it filters models.

### Hierarchical cost model

> **Not wired.** `HierarchicalCostOptimizer` estimates supervisor, worker-coordination and TalkHier communication costs. `MASRRoutingService` constructs one as `cost_optimizer` and never calls it; `calculate_hierarchical_costs`, `record_actual_cost` and `get_cost_model_stats` have no callers in `src`.

## Caching and circuit breaking

`RoutingCacheManager` (`src/ai_brain/router/routing_cache.py`) holds routing decisions in process memory when `MASR_ENABLE_CACHING` is `true` (default). The key is an MD5 hash of the lower-cased, stripped query, `context["user_id"]`, `context["domain"]`, the requested strategy and the constraints. `MASR_CACHE_MAX_SIZE` (default 1000) bounds the cache; when it is exceeded, the `MASR_CACHE_EVICTION_BATCH_SIZE` (default 100) entries with the oldest decision timestamp are removed. A cache hit does not refresh an entry. The cache is bypassed and never written when adaptive allocation is enabled or the request carries the fixture execution policy.

`MASRouter` runs the whole routing body through the shared `CircuitBreaker` (`src/core/tools/circuit.py`, the same implementation `GeminiService` uses) with `circuit_failure_threshold` 5 and `circuit_timeout_seconds` 60 as its cooldown. While the breaker is open the pass is rejected, and `route` returns the fallback decision described under [Role in execution](#role-in-execution). After the cooldown the breaker admits one trial pass: its success closes the breaker, its failure reopens it, and a cancelled trial gives the slot back without counting as either. The breaker is exposed as `MASRouter.routing_circuit_breaker`.

## MASR HTTP API

The router in `src/api/routes/masr_api.py` is mounted at `/api/v1/masr`. Every route requires a bearer token through `get_current_token` (a missing token returns 401). Routes resolve `MASRRoutingService` from `app.state.masr_routing_service` and return 503 with the detail `MASR runtime is unavailable` when it is missing. The service is built at startup from the shared `MASRRuntime` router. There is no MASR WebSocket route: the only `websocket` handler in the file is commented out. Authentication and tenancy are described in [authentication and tenancy](04-authentication-and-tenancy.md); the full route inventory is in [HTTP API](03-http-api.md).

| Method and path | Request model | Response model |
|---|---|---|
| `POST /route` | `RoutingRequest` | `RoutingDecisionResponse` |
| `POST /estimate-cost` | `CostEstimationRequest` | `CostEstimationResponse` |
| `POST /evaluate-strategies` | `StrategyEvaluationRequest` | `StrategyEvaluationResponse` |
| `POST /analyze-complexity` | `ComplexityAnalysisRequest` | `ComplexityAnalysisResponse` |
| `GET /strategies` | none | `StrategiesListResponse` |
| `GET /models` | none | `ModelsListResponse` |
| `POST /feedback` | `RoutingFeedback` | JSON object (see below) |
| `GET /status` | none | `RouterStatus` |

The models are in `src/models/masr_api_models.py`. The handlers are in `src/api/services/masr_routing_service.py` (`MASRRoutingService`).

### `POST /route`

`RoutingRequest` fields: `query`, `context`, `strategy` (a `RoutingStrategy` value), `max_cost`, `min_quality` and `timeout_ms`. Only `query`, `context` and `strategy` are passed to `MASRouter.route`; the other three are accepted and not used.

`RoutingDecisionResponse` fields: `routing_id`, `domain`, `complexity`, `strategy`, `collaboration_mode`, `supervisor_allocations`, `selected_models`, `estimated_cost`, `estimated_latency_ms`, `confidence_score`, `reasoning` and `alternatives`.

- `routing_id` is a new UUID, stored with the decision in an in-memory dictionary used by `/feedback`. It is not the router's internal `query_id`.
- `domain` is the first detected domain, or `general`.
- `supervisor_allocations` is always an empty list: the code reads a `supervisors` attribute that `AgentAllocation` does not have.
- `selected_models` is the entries of a fixed three-model table (`deepseek-v3` premium, `llama3.3:70b` standard, `gemini-pro` standard) whose tier matches the chosen primary model's tier.
- `alternatives` lists up to three other strategies with the current decision's cost and latency multiplied by 0.9 and a rejection reason; they are not separate routing runs.

### `POST /estimate-cost`

`CostEstimationRequest` fields: `query`, `strategy`, `include_breakdown` (default `true`) and `include_confidence` (default `true`). `CostEstimationResponse` fields: `estimated_cost`, `breakdown`, `confidence_score`, `cost_factors` and `recommendations`. `breakdown` (`CostBreakdown`) splits the estimate into `model_costs` (70%), `coordination_overhead` (20%) and `memory_operations` (10%) with `total_cost`, and adds `confidence_interval` of 0.8 to 1.2 times the estimate when `include_confidence` is set. `cost_factors` has the keys `query_complexity`, `model_tier`, `supervisor_count`, `total_workers` and `refinement_rounds`.

### `POST /evaluate-strategies`

`StrategyEvaluationRequest` fields: `query`, `strategies` (all five when omitted) and `weights` (keys `cost`, `quality`, `latency`; defaults 0.3, 0.5 and 0.2). The service calls `MASRouter.route` once per strategy and scores each result as `weights.cost / (1 + cost) + weights.quality * quality + weights.latency / (1 + latency_ms / 10000)`. `StrategyEvaluationResponse` fields: `comparisons` (`StrategyComparison`: `strategy`, `estimated_cost`, `estimated_quality`, `estimated_latency_ms`, `pros`, `cons`, `recommendation_score`), `recommended_strategy`, `reasoning` and `trade_offs`.

### `POST /analyze-complexity`

`ComplexityAnalysisRequest` fields: `query`, `include_features` and `include_recommendations` (both default `true`). The service runs a full `route` call and reads its analysis. `ComplexityAnalysisResponse` fields: `complexity`, `complexity_score`, `features`, `recommended_approach` and `routing_recommendations`. `features` (`ComplexityFeatures`) has `query_length` (word count), `domain_count`, `reasoning_depth` (1, 2 or 3 from the level), `data_requirements`, `coordination_needs` and `uncertainty_level`; `uncertainty_level` is always 0.5 because the service reads an attribute the analysis does not have.

### `GET /strategies` and `GET /models`

`StrategiesListResponse` fields: `strategies` (`AvailableStrategy`: `strategy`, `name`, `description`, `optimization_focus`, `use_cases`, `trade_offs`), `default_strategy` and `total_count`. `default_strategy` is the constant `balanced`, not the `MASR_DEFAULT_STRATEGY` setting.

`ModelsListResponse` fields: `models`, `tiers`, `total_count` and `providers`. Each `ModelInfo` has `provider`, `model_id`, `tier`, `cost_per_token`, `max_tokens`, `capabilities`, `average_latency_ms` and `quality_score`. `tier` is a `ModelTier` value from `src/ai_brain/config/model_schemas.py`: `basic`, `standard`, `premium`, `specialized` or `testing`. The list is the fixed three-model table above, independent of the optimizer's model set.

### `POST /feedback`

> **Not wired.** `MASRRoutingService` constructs a `SupervisionFeedbackLearner` and never calls it, and `_update_performance_from_feedback` has no caller. The endpoint validates the request and acknowledges it without learning from it.

`RoutingFeedback` fields: `routing_id`, `actual_cost`, `actual_latency_ms`, `quality_score` (0 to 1), `user_satisfaction` (0 to 1, optional), `error_occurred` and `error_message`. An unknown `routing_id` returns 400 with the code `INVALID_FEEDBACK`. A known one returns a fixed acknowledgment: `status` `accepted`, `routing_id`, `feedback_processed` `true`, `learning_updated` `false`, `recorded` `false`, `eligible` `false`, `duplicate` `false`, `source` `manual` and `reason` `manual_feedback_has_no_executed_allocation_or_evaluator_proof`.

### `GET /status`

`RouterStatus` fields: `status`, `uptime_seconds`, `total_routes`, `average_latency_ms`, `success_rate`, `active_supervisors`, `performance_metrics`, `model_availability`, `learning_metrics`, `last_error` and `last_error_time`.

- `status` is `degraded` when the average success rate is below 0.8 or every entry of `model_availability` is false, otherwise `healthy`.
- `model_availability` comes from `_check_model_availability`, which maps `deepseek`, `gemini`, `llama` and `openrouter` to `false` when the provider is disabled or lacks a key, and to `null` (unknown) when it is configured: nothing probes providers, so a configured provider is never reported available.
- `learning_metrics` is `MASRouter.get_adaptive_status()` (see [Adaptive routing](#adaptive-routing)).
- `performance_metrics` has one entry per strategy with `requests`, `success_rate`, `avg_cost`, `avg_latency_ms` and `avg_quality`. Only `POST /route` updates `requests` and `success_rate` (a moving average with weight 0.1); the other three keep their initial values.
- `active_supervisors` is always 0 because the bridge has no `active_supervisor_count` attribute.

### Errors

Handlers raise `HTTPException` with `detail` set to a `MASRErrorResponse` (`error`, `error_code`, `details`, `suggestions`, `timestamp`), so the JSON body is `{"detail": {...}}`. `POST /route` and `POST /feedback` turn a `ValueError` into 400; every other exception, and every exception on the remaining routes, becomes 500 and `details.error` carries the exception text.

| Route | Error codes |
|---|---|
| `/route` | `INVALID_REQUEST` (400), `ROUTING_ERROR` (500) |
| `/estimate-cost` | `ESTIMATION_ERROR` |
| `/evaluate-strategies` | `EVALUATION_ERROR` |
| `/analyze-complexity` | `ANALYSIS_ERROR` |
| `/strategies` | `STRATEGY_LIST_ERROR` |
| `/models` | `MODEL_LIST_ERROR` |
| `/feedback` | `INVALID_FEEDBACK` (400), `FEEDBACK_ERROR` (500) |
| `/status` | `STATUS_ERROR` |

## Adaptive routing

> **Off by default.** With `ADAPTIVE_ROUTING_ENABLED=true`, adaptive routing adjusts the worker count of `parallel` and `hierarchical` allocations by -2 to +2 using a per-mode Thompson-sampling bandit (`AdaptiveAllocationEngine`). Learning also needs a non-empty `ADAPTIVE_ROUTING_ALLOWED_EVALUATORS` and a Redis instance at `REDIS_URL`.

With the flag off, `MASRRuntime.create` uses an in-memory state store and no Redis client; with it on, it creates a `RedisAdaptiveStateStore` and restores the stored snapshot at startup. State lives under the key prefix `cerebro:masr:adaptive`, namespaced by schema and policy version, and updates use an atomic compare-and-set with a per-outcome idempotency marker kept for 7 days and 5 minutes.

Behaviour when enabled:

- One experiment per mode (`adaptive_allocation_<mode>`) with five arms, -2 to +2. A proposal applies only when the experiment holds at least `ADAPTIVE_ROUTING_MIN_HISTORY` eligible samples in total (default 300) and at least one sample for every arm. Otherwise the router uses arm 0, the unchanged baseline.
- `fast_path`, `direct` and `debate` have fixed allocations and always use arm 0.
- The applied change is clamped to `ADAPTIVE_ROUTING_MAX_WORKER_ADJUST` (default 2). A proposal whose worker count exceeds the strategy budget or falls outside the system bounds is discarded in favor of the memory baseline (arm 0), with `budget_clamped` or `system_clamped` set, and the arm is reported as 0.
- A corrupt, incompatible or unreachable state store degrades to arm 0; routing does not depend on Redis.
- The routing cache is bypassed.
- Every decision carries `AdaptiveDecisionMetadata` with a status of `disabled`, `fixture_off`, `cold`, `control`, `degraded` or `active`. `MASRouter.get_adaptive_status()` reports the effective state, state revision, outcome counts and per-arm counts.

Only outcomes with `OutcomeSource.EVALUATOR`, measured quality, an evaluator name and version present in `ADAPTIVE_ROUTING_ALLOWED_EVALUATORS` (default empty), a successful execution status (`completed` or `success`), a timestamp no older than 7 days and no more than 5 minutes ahead of the clock, and matching schema and policy versions update the bandit. All other outcomes are recorded as ineligible.

Other settings read in `factory.py`: `ADAPTIVE_ROUTING_UPDATE_INTERVAL_SECONDS` (300), `ADAPTIVE_ROUTING_POSTERIOR_TEMP_ENABLED` (`true`), `ADAPTIVE_ROUTING_POSTERIOR_TEMP_THRESHOLD` (150), `ADAPTIVE_ROUTING_POSTERIOR_TEMP_FACTOR` (3.0), `ADAPTIVE_ROUTING_SCHEMA_VERSION` (`1`) and `ADAPTIVE_ROUTING_POLICY_VERSION` (`masr-adaptive-v1`). See [configuration](02-configuration.md).

Two offline scripts evaluate the allocation policy without enabling it; see the [testing guide](../testing-guide.md):

- `scripts/run_adaptive_routing_eval.py` runs a synthetic 200-query comparison of static and adaptive allocation, prints a report and writes `/tmp/adaptive_routing_eval_results.json`.
- `scripts/evaluate_adaptive_routing_promotion.py` replays a corpus against explicit criteria. Flags: `--criteria`, `--corpus`, `--output` (required) and `--seed` (default 42). It writes a private JSON report and exits 0 on `PASS`, 2 on `FAIL` and 3 otherwise. `config/adaptive_routing_promotion_criteria.example.json` is the criteria template; its `approved_for_promotion` is `false` and its evaluator fields are placeholders.

## Memory-informed routing

> **Not wired.** `MEMORY_INFORMED_ROUTING_ENABLED` (default `false`) is read into `MASRouter.memory_informed_routing_enabled`, but `MASRouter.episodic_memory` and `MASRouter.procedural_memory` are initialised to `None` and nothing assigns them, so `_get_episodic_routing_prior` would return `None`. `create_masr_router` therefore raises `ValueError` at startup when the flag is true (outside fixture mode) rather than letting it do nothing. See [not wired](15-not-wired.md).

## Routing outcomes and metrics

- `RoutingMetricsCollector` (`routing_metrics.py`) keeps a deque of up to 10,000 decisions and a counter of total decisions. `get_metrics` returns that counter; the other `RoutingMetrics` fields stay at zero, and `adapt_from_decision` does nothing.
- `RoutingOutcome` (`routing_outcome.py`) is the typed outcome record: opaque identifiers, source, collaboration mode, proposed and applied arm, final worker count, execution status, latency, cost and quality with their availability and an eligibility result. It has no field for query text or output.

> **Not wired.** `RoutingOutcomeRecorder` (`outcome_recorder.py`) turns an executed allocation into a `RoutingOutcome` with retry-stable identifiers and delivers it to `MASRouter.record_routing_outcome`. `DirectExecutionService` constructs one as `outcome_recorder` and nothing calls `record`, so production runs do not record outcomes. The offline evaluation scripts call `record_routing_outcome` directly.

- `routing_observability.py` exports the Prometheus series `cerebro_masr_adaptive_effective_state` (one-hot gauge over `disabled`, `fixture_off`, `cold`, `degraded`, `active`), `cerebro_masr_outcomes_total` and `cerebro_masr_outcome_metric_availability_total`. The gauge is updated by the router; the two counters are updated only by `RoutingOutcomeRecorder.record`. Labels are fixed enumerations. See [security, audit and observability](12-security-audit-observability.md).
- When `LANGFUSE_ENABLED` is `true`, `MASRouter.route` is wrapped in `trace_masr_routing` (`src/core/tracing.py`), which sends a redacted, length-capped query prefix and routing metadata.

## Source map

- `src/ai_brain/router/masr.py`: `MASRouter`, `RoutingDecision`, mode selection, allocation, adaptive state handling
- `src/ai_brain/router/factory.py`: `MASRRuntime`, `create_masr_router`, `settings_to_router_config`
- `src/ai_brain/router/query_analyzer.py`: `QueryComplexityAnalyzer`, `ComplexityAnalysis`, `QueryDomain`
- `src/ai_brain/router/query_decomposer.py`: `QueryDecomposer`, `QueryDecomposition`
- `src/ai_brain/router/cost_optimizer.py`: `CostOptimizer`, `OptimizationStrategy`, legacy model specifications
- `src/ai_brain/router/hierarchical_cost_model.py`: `HierarchicalCostOptimizer` (unused)
- `src/ai_brain/router/routing_cache.py`: `RoutingCacheManager`
- `src/ai_brain/router/routing_types.py`: `RoutingStrategy`, `AgentAllocation`, `RoutingExecutionPolicy`, adaptive metadata types
- `src/ai_brain/router/routing_metrics.py`: `RoutingMetricsCollector`
- `src/ai_brain/router/routing_outcome.py`: `RoutingOutcome`, `EvaluatorEligibilityPolicy`
- `src/ai_brain/router/outcome_recorder.py`: `RoutingOutcomeRecorder`
- `src/ai_brain/router/adaptive_state_store.py`: in-memory and Redis adaptive state stores
- `src/ai_brain/router/routing_observability.py`: Prometheus series
- `src/ai_brain/router/execution_plan_compiler.py`: `ExecutionPlanCompiler`
- `src/ai_brain/experimentation/core/adaptive_allocation_engine.py`: `AdaptiveAllocationEngine`
- `src/ai_brain/experimentation/eval/adaptive_routing_eval.py`, `adaptive_routing_promotion.py`: offline evaluation and promotion gate
- `src/core/tools/circuit.py`: `CircuitBreaker`
- `src/core/contracts/execution_plan.py`: `CollaborationMode`, `COLLABORATION_MODE_SUPPORT`
- `src/api/routes/masr_api.py`: HTTP routes
- `src/models/masr_api_models.py`: request and response models
- `src/api/services/masr_routing_service.py`: `MASRRoutingService`
- `src/ai_brain/config/model_schemas.py`: `ModelTier`
- `config/adaptive_routing_promotion_criteria.example.json`: promotion criteria template
