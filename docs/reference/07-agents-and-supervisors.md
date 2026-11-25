# Agents and supervisors

Cerebro has 17 worker agents, four domain supervisors, a direct agent API and a supervisor API. Worker agents are single-purpose classes that turn a task into a result. A supervisor owns a LangGraph workflow that sends tasks to its workers, runs a verification gate over their output and scores the outcome. The default research execution path (`POST /api/v1/query/...`) runs workers from a compiled execution plan and does not use supervisors; supervisors run behind `/api/v1/supervisors`, the TalkHier sessions and the direct agent routes described here. See the [architecture overview](01-architecture-overview.md) and [research execution](05-research-execution.md).

## Summary

| Feature | Status | Entry point | Source |
|---|---|---|---|
| 17 worker agents | Active | `AgentFactory.resolve_agent` | `src/core/kernel/component_keys.py` (`AGENT_KEYS`) |
| Direct agent API | Active | `/api/v1/agents/*` | `src/api/routes/agent_api.py` (`router`) |
| Chain-of-agents and mixture-of-agents | Active | `POST /api/v1/agents/chain`, `POST /api/v1/agents/mixture` | `src/api/services/agent_execution_service.py` (`AgentExecutionService`) |
| Four domain supervisors | Active | `/api/v1/supervisors/*`, TalkHier sessions | `src/agents/supervisors/` (`BaseSupervisor`) |
| Worker verification loop | Active | `BaseSupervisor._send_worker_verified` | `src/agents/supervisors/base_supervisor.py` |
| MAST failure labels | Active | `BaseSupervisor._run_verification` | `src/qa/mast.py` (`MASTLabeler`) |
| Supervisor API | Active | `/api/v1/supervisors/*` | `src/api/routes/supervisor_api.py` (`router`) |
| TalkHier API | Active | `/api/v1/talkhier/*` | `src/api/routes/talkhier_api.py` (`router`) |
| Worker tool-call loop | Active | `LLMWorkerAgentBase.execute` | `src/agents/llm_worker_base.py` (`_execute_model_tool_call`) |
| OpenRouter route for workers | Off by default | `MULTI_PROVIDER_ROUTING_ENABLED=true` | `src/agents/llm_worker_base.py` (`_build_legacy_openrouter_route`) |
| Constraint pinning | Off by default | `ENABLE_CONSTRAINT_PINNING=true` | `src/agents/supervisors/base_supervisor.py` (`BaseSupervisor.execute`) |
| Bounded parallel worker execution | Not wired | none | `src/agents/supervisors/base_supervisor.py` (`execute_workers_parallel`) |
| Supervisor refinement rounds | Not wired | none | `src/agents/supervisors/base_supervisor.py` (`coordinate_refinement_round`) |
| Procedural memory context in worker prompts | Not wired | `MEMORY_INFORMED_ROUTING_ENABLED=true` | `src/agents/llm_worker_base.py` (`_get_procedural_context`) |

## Domains, supervisors, and workers

`AGENT_KEYS` in `src/core/kernel/component_keys.py` lists the 17 agent keys. `build_application_component_registry` (`src/api/services/component_catalog.py`) binds each key to its class. `AgentFactory.resolve_agent(key, config)` resolves the class from the registry and instantiates it with `gemini_service`, `cache_client` and a `config` dict.

| Key | Class | Module | Domain | Supervisor |
|---|---|---|---|---|
| `literature_review` | `LiteratureReviewAgent` | `src/agents/literature_review_agent.py` | research | research |
| `comparative_analysis` | `ComparativeAnalysisAgent` | `src/agents/comparative_analysis_agent.py` | research | research |
| `methodology` | `MethodologyAgent` | `src/agents/methodology_agent.py` | research | research |
| `synthesis` | `SynthesisAgent` | `src/agents/synthesis_agent.py` | research | research |
| `citation` | `CitationAgent` | `src/agents/citation_agent.py` | research | research |
| `content_planning` | `ContentPlanningAgent` | `src/agents/content_agents.py` | content | content (registered, never called) |
| `drafting` | `DraftingAgent` | `src/agents/content_agents.py` | content | content |
| `editing` | `EditingAgent` | `src/agents/content_agents.py` | content | content |
| `optimization` | `OptimizationAgent` | `src/agents/content_agents.py` | content | content |
| `data_analysis` | `DataAnalysisAgent` | `src/agents/analytics_agents.py` | analytics | analytics |
| `statistical_modeling` | `StatisticalModelingAgent` | `src/agents/analytics_agents.py` | analytics | analytics |
| `insight_synthesis` | `InsightSynthesisAgent` | `src/agents/analytics_agents.py` | analytics | analytics |
| `financial_analysis` | `FinancialAnalysisAgent` | `src/agents/finance_agents.py` | finance | finance |
| `valuation` | `ValuationAgent` | `src/agents/finance_agents.py` | finance | finance |
| `risk_assessment` | `RiskAssessmentAgent` | `src/agents/finance_agents.py` | finance | finance |
| `financial_calculator` | `FinancialCalculatorAgent` | `src/agents/financial_calculator_agent.py` | finance | none (deterministic wrapper around `finance_math`) |
| `verification` | `VerificationAgent` | `src/agents/verification_agent.py` | cross-cutting | every supervisor (QA gate) |

The `content` supervisor registers `content_planning` and its `plan_content` node builds a task payload for it, but no node sends that worker a message, so `content_planning` never produces a result inside the supervisor workflow.

`SUPERVISOR_KEYS` has four supervisors: `analytics`, `content`, `finance` and `research`. `DOMAIN_KEYS` has seven domain keys: `analytics`, `content`, `finance`, `general`, `multimodal`, `research` and `service`. The domain registry maps each to a supervisor name: `analytics`, `content` and `research` to themselves, `finance` to `research`, `general` to `research`, `multimodal` to `content` and `service` to `service`. The registry has no supervisor registered under `service`, and `MASRSupervisorBridge` resolves a domain through this map only when MASR proposes a generic supervisor type (`general`, `parallel_coordinator` or `hierarchical_supervisor`). The `QueryDomain` values that MASR produces are in [MASR routing](06-masr-routing.md).

### Agent types exposed by the direct API

`AgentType` (`src/models/agent_api_models.py`) has ten values: `literature-review`, `citation`, `methodology`, `comparative-analysis`, `synthesis`, `financial-analysis`, `valuation`, `risk-assessment`, `financial-calculator` and `verification`. `API_AGENT_KEYS` maps each to the underscore key above. Seven workers have no `AgentType` and are reachable only through supervisors: `content_planning`, `drafting`, `editing`, `optimization`, `data_analysis`, `statistical_modeling` and `insight_synthesis`.

## Worker base

`BaseAgent` (`src/agents/base.py`) is the abstract base for workers and supervisors. `BaseAgent.__init__(gemini_service=None, cache_client=None, config=None)` stores the three arguments, an optional `mcp_integration` taken from `config["mcp_integration"]`, and metrics and state objects. Subclasses implement `execute(task) -> AgentResult` and `validate_result(result) -> bool`. Other members:

- `handle_error(task, error)` returns a `failed` result with `output` `{"error", "error_type"}` and confidence 0.
- `execute_with_retry(task)` applies `task.timeout` and validates the result; the retry decorator makes up to three attempts with exponential back-off between 1 and 10 seconds for `ConnectionError` and `TimeoutError`.
- `cache_result` and `get_cached_result` use `cache_client`.
- `build_execution_metadata` adds `agent_type`, `prompt_template` and `prompt_version`.

The data classes are in `src/agents/models.py` and are frozen dataclasses:

- `AgentTask`: `id`, `agent_type`, `input_data`, `context` (default empty), `timeout` (default 300) and `priority` (default 0).
- `AgentResult`: `task_id`, `status`, `output`, `confidence`, `execution_time` and `metadata`. `is_successful()` is true only for the status `success`.
- `AgentMessage`: `from_agent`, `to_agent`, `message_type`, `content` and `timestamp`.

`LLMWorkerAgentBase` (`src/agents/llm_worker_base.py`) is the base of the prompt-driven workers. A subclass sets `agent_type` and implements `_build_prompt(query, task)`.

- **Execution.** `execute` requires a non-empty `input_data["query"]`, runs the optional `_precompute` hook, builds the prompt, appends the tool block when the worker has registered tools, appends precomputed values as a `Precomputed exact values` block, generates, and returns `output` with `content`, `analysis` and `agent_type` (plus `computed` when present).
- **Generation.** `_generate_with_routing` picks a route (`_select_generation_route`). A task that carries `input_data["provider_model_policy"]` uses only that policy's primary provider and model; a failure raises `PlanProviderUnavailableError` and no other provider is tried. Otherwise, when `MULTI_PROVIDER_ROUTING_ENABLED` is true and `OPENROUTER_API_KEY` is set, a `ModelRouter` with OpenRouter generates (`max_tokens` 2000, temperature 0.7, tier `simple` below complexity 0.3, `balanced` below 0.7, `complex` otherwise) and a failed call falls back to Gemini. With neither, `GeminiService.generate_content` runs; confidence is 0.85 for non-empty text and 0.3 for empty text. With no model service, the result is a fixed message at confidence 0.3. See [LLM providers](10-llm-providers.md).
- **Budget.** When `task.context` holds a budget ledger, every provider call reserves and settles against it, and `BudgetExceededError` propagates.
- **Structured output.** `_generate_structured_with_routing(prompt, schema, task)` routes the same way and validates against a Pydantic schema. `MethodologyAgent` and `SynthesisAgent` use it and return a built-in canned analysis, still with the status `success`, when structured generation raises.
- **Tools.** A worker overrides `_register_tools(registry)`; only `DataAnalysisAgent` registers a tool (`ArithmeticTool`). The model requests a tool with exactly `{"tool_call": {"name": ..., "arguments": {...}}}`; anything else is ordinary text. One tool call per execution is allowed (`MAX_TOOL_MODEL_TURNS` is 2 model turns), a second request ends in a failed result with `error_type` `tool_loop_limit`. If `config["tool_allowlist"]` is present, tools outside it return `tool_not_allowed`. The call goes through `ToolRegistry.execute` with the task's identity, capability grants and budget. Workers built from an execution plan receive the live MCP tool path only when `MCP_TOOL_PATH_ENABLED` is true (default `false`). Other failure codes are `malformed_tool_call`, `tool_execution_error`, `tool_call_failed` and `final_generation_failed`. See [tool boundary and evidence](09-tool-boundary-and-evidence.md).

> **Not wired.** `_get_procedural_context` would add a `Past Successful Approaches` block to the prompt when `MEMORY_INFORMED_ROUTING_ENABLED` is true and `LLMWorkerAgentBase.procedural_memory` is set. The class attribute is `None` and nothing in `src` assigns it, so no worker prompt carries procedural context.

## Worker behaviour notes

- **`LiteratureReviewAgent`.** With a Gemini service present, it makes one structured model call (`_search_and_analyze_structured`) and reports `databases_searched` as `["gemini_knowledge"]`; the sources come from the model, not from a search index. Without one it calls the MCP integration's academic search when one is configured and `_fallback_academic_search` otherwise; the last-resort `_static_fallback_sources` returns an empty source list. Results are cached for 24 hours.
- **`CitationAgent`.** It formats citations, then checks each source's `title`, `authors` and `year` and its DOI. `_resolve_doi` checks the DOI format and its docstring states it "Does not resolve against a registry"; `resolved` is always `False`, so `verified_sources` is 0 and every `verification_status` entry has the status `unverified`.
- **`ResearchQualityValidator.validate_sources`.** The research supervisor's source check asks one structured model call whether each of up to 10 sources exists, and keeps sources with `exists` true and confidence at least 0.7. No registry is consulted.
- **Finance workers.** `FinancialAnalysisAgent` calls `finance_math.financial_ratios` when `parameters.values` is a non-empty dict, and `ValuationAgent` calls `finance_math.dcf_gordon_growth` when `parameters` holds `fcf_next`, `growth_rate` and `discount_rate`; the exact values are appended to the prompt and returned in `output["computed"]`. `RiskAssessmentAgent` is prompt-only. None retrieves market data.
- **`FinancialCalculatorAgent`.** It does not call a model. It reads `parameters.operation` and runs `finance_math.calculate`; confidence is 1.0. Operations are listed in `finance_math.AVAILABLE_OPERATIONS`.
- **`VerificationAgent`.** It reviews `input_data["content"]` (or `previous_result`, `background` or the query) and answers with `VERDICT: PASS` or `REVISE` and an `ISSUES` list.
- **Content and analytics workers** build one prompt each (`ContentPlanningAgent`, `DraftingAgent`, `EditingAgent`, `OptimizationAgent`, `DataAnalysisAgent`, `StatisticalModelingAgent`, `InsightSynthesisAgent`) and read prior-stage text from `input_data["previous_result"]` or `input_data["background"]`.

### Input keys read by each worker

| Worker | Keys read from `input_data` |
|---|---|
| `literature_review` | `query` (required) |
| `citation` | `sources` (required), `style`, `export_formats` |
| `methodology` | `research_question` (required), `research_type`, `scope` |
| `synthesis` | `agent_outputs` (required) |
| `comparative_analysis` | `items` (at least 2), `criteria` (required with items), `comparison_data` |
| `financial_calculator` | `parameters.operation` (required) |
| all other `LLMWorkerAgentBase` workers | `query` (required), `previous_result`, `background`, `parameters` (finance), `content` (verification) |

Supervisors send each worker a TalkHier message; `CommunicationProtocol._send_message_to_agent` builds the task input from it: `query`, `research_question` and `text` all carry the message text, and the message's `intermediate_outputs` and `context` are merged on top. The direct agent API builds `input_data` itself (see [Direct agent API](#direct-agent-api)).

## Supervisors

`BaseSupervisor` (`src/agents/supervisors/base_supervisor.py`) extends `BaseAgent`. A subclass implements `_register_worker_types`, `_build_workflow_graph` and `_coordinate_workers`. `_build_workflow_graph` returns nothing; it compiles a LangGraph `StateGraph` onto `self.workflow_graph`. `execute` instantiates the registered workers from the component registry, builds a `SupervisionState`, runs the graph with `ainvoke` and returns an `AgentResult` whose `status` is `completed` when `consensus_score` reaches the supervisor's `quality_threshold` (default 0.85) and `partial` otherwise. Its `output` holds `worker_results`, `supervision_quality` and `coordination_metadata`.

| Supervisor | Graph nodes in order |
|---|---|
| `analytics` | `plan_analysis`, `coordinate_data_analysis`, `coordinate_modeling`, `coordinate_insights`, `evaluate_confidence` |
| `content` | `plan_content`, `coordinate_drafting`, `coordinate_editing`, `coordinate_optimization`, `evaluate_quality` |
| `finance` | `analyze_financials`, `coordinate_valuation`, `coordinate_risk`, `evaluate_quality` |
| `research` | `plan_research`, `coordinate_literature`, `validate_sources`, `coordinate_methodology`, `coordinate_analysis`, `coordinate_synthesis`, `coordinate_citation`, `draft_paper`, `graduate_review`, then `revise_paper` (looping back to `graduate_review`) or `evaluate_consensus` |

The research graph accepts the paper when the graduate review `overall_score` is at least 9.0 or the revision count reaches `max_paper_revisions` (default 2). After `evaluate_consensus` the graph has a `continue` edge back to `coordinate_literature`, taken only when consensus is below the threshold and `refinement_round` is below 1. The state starts at 1 and `evaluate_consensus` increments it, so the edge is not taken and the graph ends.

`SupervisorFactory` (`supervisor_factory.py`) registers the four built-in `SupervisorSpecification` entries, resolving each class from the component registry. `register_supervisor` rejects a replacement for a built-in type. It also provides capability matching and a health monitor.

### Verification gate

Every worker call in a supervisor phase goes through `_send_worker_verified`, which calls `_run_worker_with_verification_loop`. The loop runs the worker, runs `_run_verification` on the response text, and when the verdict is `revise` and rounds remain, re-sends the task with the verifier's report appended inside an escaped `<REVISION_FEEDBACK>` block. `MAX_VERIFICATION_REVISION_ROUNDS` is 2 (the first attempt plus one revision). When the worker returns no response, the loop stops with a neutral `pass`.

`_run_verification` resolves a `VerificationAgent` from the component registry and parses the verdict with `parse_verdict`, which returns `pass` only for an explicit `PASS` verdict. A missing or unparseable verdict, or any verifier error, returns `revise`, so the gate fails closed. Empty content skips the verifier and returns `pass`. The final node of each supervisor runs the verifier once over the aggregated worker output: `_run_verification` in the analytics, content and finance supervisors, and an inline loop in the research supervisor's `evaluate_consensus` that exits after the first verdict without re-running workers. On `revise` the supervisor multiplies its quality score by 0.85 (the research supervisor applies the factor to both the consensus and quality scores). The base scores are `min(0.92, 0.6 + 0.11 * completed)` for analytics and `min(0.9, 0.5 + 0.1 * completed)` for content and finance, where `completed` is the number of worker results excluding the verification entry; research uses the consensus builder's scores.

`src/qa/mast.py` (`MASTLabeler`) labels each verification result with failure modes and returns them in `AgentResult.metadata` (`mast_failures`, `mast_confidence`, `verification_rounds`, `revision_history`). The labels are `FM-1.1` (disobey task specification), `FM-1.3` (step repetition), `FM-1.5` (unaware of termination conditions), `FM-2.6` (reasoning-action mismatch) and `FM-3.2` (no or incomplete verification).

> **Not wired.** `BaseSupervisor.execute_workers_parallel` runs worker messages through `BoundedTaskRunner` with `max_parallel_workers` (default 5) and isolates worker failures. Every supervisor phase calls workers one at a time through `_send_worker_verified`, and nothing in `src` calls `execute_workers_parallel`.

> **Not wired.** `BaseSupervisor.coordinate_refinement_round` broadcasts a refinement prompt to the workers and scores consensus. Nothing in `src` calls it.

### Where supervisors run

Supervisors run when a request reaches `/api/v1/supervisors/{supervisor_type}/execute`, a TalkHier session, or `SupervisorCoordinationService`. The `/query` execution path compiles an `ExecutionPlan` and runs it with `MASRSupervisorBridge.execute_execution_plan`, which executes the plan "without translating it to a supervisor graph". See [architecture overview](01-architecture-overview.md).

## Direct agent API

The router in `src/api/routes/agent_api.py` is mounted at `/api/v1/agents` and requires a bearer token. Tenancy and authority are described in [authentication and tenancy](04-authentication-and-tenancy.md); the route inventory is in [HTTP API](03-http-api.md).

| Group | Routes |
|---|---|
| Catalogue | `GET /`, `GET /{agent_type}` |
| Execution | `POST /{agent_type}/execute`, `POST /chain`, `POST /mixture` |
| Per-agent utility | `POST /{agent_type}/validate`, `GET /{agent_type}/metrics`, `GET /{agent_type}/health` |
| Convenience | `POST /literature-review/search`, `POST /citation/format`, `POST /synthesis/combine`, `POST /workflows/literature-analysis`, `POST /workflows/comprehensive-research` |
| Monitoring | `GET /system/stats`, `GET /executions/active`, `GET /health/summary`, `GET /performance/comparison` |

**Authority.** `execute`, `chain`, `mixture` and the convenience routes require `authority_reference` (`authority_id` and `authority_version`). Without it the route returns 422 with `detail` `{"code": "EXECUTION_AUTHORITY_REQUIRED"}`. The reference must resolve for the caller's organization, otherwise 422 with the resolver's code (`EXECUTION_AUTHORITY_UNAVAILABLE` when no resolver is configured). The convenience routes take `authority_id` and `authority_version` as query or body parameters and return `AUTHORITY_REFERENCE_INCOMPLETE` when only one is given. The organization comes from the token and overrides any value in the request body.

**Single agent.** `AgentExecutionRequest` has `query` (1 to 2000 characters), `context`, `parameters`, `timeout_seconds` (30 to 1800, default 300), `quality_threshold`, `enable_refinement`, `max_refinement_rounds`, `user_id`, `session_id` and `authority_reference`. `AgentExecutionService.execute_single_agent` builds an `AgentTask` with `input_data` `{query, context, parameters, quality_threshold, enable_refinement, max_refinement_rounds}`, runs the worker under `asyncio.wait_for`, and returns `AgentExecutionResponse` with `quality_score` equal to the worker's `confidence`. A timeout or exception becomes a response with status `failed`. The service allows 100 concurrent executions; one more raises `RuntimeError`, which the route returns as a 500. Because the task carries no `context`, direct executions have no capability grants or budget ledger. The worker input keys are in the table above; for `citation`, `methodology` and `synthesis` the direct API supplies none of the required keys, so those workers return their own input error with status `failed`, and `comparative_analysis` returns a structured success that reports no items.

**Chain.** `ChainOfAgentsRequest` has `agent_chain` (2 to 5 agents), `pass_intermediate_results` (default true), `early_stopping` (default false), `quality_threshold` (default 0.85), `timeout_per_agent_seconds` (30 to 900, default 180) and `enable_validation`, which the service does not read. Agents run in order with `parameters` empty. A successful step's output is stored in the shared `context` under `previous_<agent>_result` for the next step; no worker reads that key. Early stopping ends the chain when a step's `quality_score` is below the threshold and sets `early_stopped` and `stopped_at_agent`. The final status is `completed`, `partial` or `failed` by how many steps failed, and `final_result` is the last step's output.

**Mixture.** `MixtureOfAgentsRequest` has `agent_types` (2 to 5), `aggregation_strategy` (`consensus`, `weighted_average` or `best_quality`; any other value uses `consensus`), `weight_by_confidence` (default true), `consensus_threshold` (default 0.8), `timeout_seconds` (60 to 1800, default 300) and `max_parallel` (1 to 5, default 3). The service runs the agents through `BoundedTaskRunner(max_parallel)` inside `asyncio.timeout_at(deadline)`, so one deadline covers the whole mixture. Weights are confidence shares (or equal shares). `consensus_score` is `1 - stdev(confidences)`, and `consensus_achieved` compares it with the threshold.

**Validation.** `POST /{agent_type}/validate` is a length-and-type heuristic: it returns `valid` when a score from the query length and parameter types reaches 0.7, with a constant `estimated_cost` of 0.01. It does not call the worker.

**Monitoring.** Counters live in `AgentExecutionService` process memory and reset on restart. `GET /`, `GET /{agent_type}/metrics`, `GET /system/stats`, `GET /executions/active` and `GET /performance/comparison` read per-organization counters through `get_tenant_identity` (`/`) or `get_tenant_context` (the others), so a caller sees only its organization's executions. `GET /{agent_type}/health` and `GET /health/summary` use only the router's token dependency and read process-wide counters. Several `AgentMetricsResponse` fields are constants (`average_cost_per_execution` 0.01, `cost_efficiency_score` 0.85, `peak_usage_hour` 14). `time_period_hours` on `/performance/comparison` is echoed and not used in the computation. The catalogue entries from `GET /` carry fixed reliability and quality scores.

## Supervisor API

The router in `src/api/routes/supervisor_api.py` is mounted at `/api/v1/supervisors`. Every REST route requires a bearer token and the service is resolved from `app.state.supervisor_coordination_service` (503 when missing). `SupervisorType` accepts `research`, `content`, `analytics`, `service` and `general`; `finance` is not a value, so the finance supervisor cannot be addressed here.

| Route | Behaviour |
|---|---|
| `POST /{supervisor_type}/execute` | `SupervisorCoordinationService.execute_supervisor_task` assigns workers from a static catalogue, runs the supervisor through `RealSupervisorExecutor` and the MASR bridge, and returns `SupervisorExecuteResponse`. `service` and `general` fall back to the `research` supervisor class. Sends a `task_completed` event to the caller's organization's WebSocket clients |
| `GET /`, `GET /{supervisor_type}`, `GET /{supervisor_type}/workers` | Static catalogue entries from `SupervisorRegistry` |
| `POST /{supervisor_type}/coordinate` | Builds a coordination plan and executes it |
| `POST /multi/orchestrate` | Runs each listed supervisor in sequence and synthesizes the results when `synthesis_required` is set |
| `GET /{supervisor_type}/stats`, `GET /{supervisor_type}/health` | In-memory counters |
| `POST /optimize-allocation` | Heuristic allocation by goal (`quality`, `speed`, `cost` or balanced) |
| `POST /resolve-conflicts` | `majority_vote`, `quality_based` and `supervisor_override` with guidance are computed directly; other strategies ask a Gemini service and fall back to a concatenated consensus |
| `GET /performance/compare` | Ranks supervisors by the in-memory counters |
| `POST /experiment` | Returns 501 |
| `WS /coordination/ws` | Sends a fixed series of progress events (10, 30, 50, 70, 90 and 100 percent, one second apart) for a `coordination_id`; the events are not derived from execution state |
| `WS /{supervisor_type}/ws` | Sends the supervisor info, answers `ping` and `get_status`, and receives `task_completed` events for the caller's organization |

The supervisor and worker catalogue in `SupervisorRegistry` (worker names such as `writer` and `data_analyst`, worker counts, initial success rate 0.95) is static metadata and is separate from the 17 agent classes. WebSocket routes authenticate with `require_authenticated_websocket`; events reach only connections whose principal belongs to the same organization.

## TalkHier API

The router in `src/api/routes/talkhier_api.py` is mounted at `/api/v1/talkhier` and requires a bearer token and a tenant context. Sessions live in `TalkHierStateManager.sessions`, a dictionary in process memory that is cleared on shutdown; a session is visible only to the user and organization that created it.

| Route | Purpose |
|---|---|
| `POST /sessions` | Creates a session: routes the query through MASR, creates a supervisor and returns the session ID and a WebSocket URL |
| `GET /sessions/{session_id}` | Session status |
| `POST /sessions/{session_id}/round` | Runs one refinement round |
| `POST /sessions/{session_id}/consensus` | Checks consensus among participants |
| `POST /sessions/{session_id}/close` | Closes the session |
| `GET /protocols` | Five protocol types: `standard` (3 rounds), `fast_track` (2), `deep_analysis` (5), `collaborative` (3), `supervised` (3) |
| `POST /validate` | Checks a message sequence against the protocol |
| `GET /analytics` | Protocol analytics |
| `POST /coordinate` | Coordinates two or more sessions (`sequential`, `parallel` or `hierarchical`) |
| `GET /health` | Active-session count for the caller |
| `WS /sessions/{session_id}/live`, `WS /interactive`, `WS /coordination` | Live updates |

`TalkHierSessionRequest` carries its own thresholds: `quality_threshold` (default 0.85), `consensus_threshold` (default 0.8, range 0.5 to 1.0), `max_rounds` (default 3, range 1 to 10) and `timeout_seconds` (default 300). The `TALKHIER_*` settings in `src/core/config.py` are exposed through `get_ai_brain_config` and are not read by the session service or the supervisors.

## Constraint pinning

> **Off by default.** When enabled, `BaseSupervisor.execute` extracts routing, QA, format and security constraints from the original query into a request-local `ConstraintRegistry` and logs the count and extraction time. Enable with `ENABLE_CONSTRAINT_PINNING=true`. `CONSTRAINT_TYPES` (a JSON array or comma-separated list of `routing`, `qa`, `format` and `security`; default all four) selects the types, and an unknown value stops startup.

The registry is created per call and discarded after the log line. `ConstraintRegistry.inject` has no call site in `src`, so extracted constraints are never added back to a prompt: enabling the flag changes only the log output.

## Adding a domain

A new domain touches these places:

1. Write the worker classes under `src/agents/`, subclassing `LLMWorkerAgentBase` (set `agent_type` and `_build_prompt`) or `BaseAgent`.
2. Add each worker name to the tuple in `AGENT_KEYS` (`src/core/kernel/component_keys.py`) and the class to `_AGENT_COMPONENTS` in `src/api/services/component_catalog.py`. `build_application_component_registry` indexes `AGENT_KEYS[name]`, so a class without a key raises `KeyError` when the registry is built.
3. Write a `BaseSupervisor` subclass in `src/agents/supervisors/` that implements `_register_worker_types`, `_build_workflow_graph` and `_coordinate_workers`, with worker phases that call `_send_worker_verified` and a final phase that calls `_run_verification`.
4. Add the supervisor name to `SUPERVISOR_KEYS` and the class to `_SUPERVISOR_COMPONENTS`, and add a `SupervisorSpecification` in `SupervisorFactory._register_builtin_supervisors`.
5. Add the domain name to `DOMAIN_KEYS` and `_DOMAIN_SUPERVISOR_NAMES`.
6. For routing, extend `QueryDomain` and its `domain_patterns` in `src/ai_brain/router/query_analyzer.py` and the domain maps in `MASRouter._get_domain_supervisor_types` and `_get_domain_worker_types`.
7. For direct API access, add an `AgentType` value; `API_AGENT_KEYS` then requires a matching `AGENT_KEYS` entry. For supervisor API access, add a `SupervisorType` value and `SupervisorRegistry` entries.

## Source map

- `src/agents/base.py` — `BaseAgent`
- `src/agents/models.py` — `AgentTask`, `AgentResult`, `AgentMessage` and related frozen dataclasses
- `src/agents/factory.py` — `AgentFactory`
- `src/agents/llm_worker_base.py` — `LLMWorkerAgentBase`, tool-call envelope, generation routing
- `src/agents/literature_review_agent.py`, `citation_agent.py`, `comparative_analysis_agent.py`, `methodology_agent.py`, `synthesis_agent.py` — research workers
- `src/agents/content_agents.py`, `analytics_agents.py`, `finance_agents.py`, `financial_calculator_agent.py`, `verification_agent.py` — other workers
- `src/agents/supervisors/base_supervisor.py` — `BaseSupervisor`, verification gate, `parse_verdict`
- `src/agents/supervisors/research_supervisor.py`, `content_supervisor.py`, `analytics_supervisor.py`, `finance_supervisor.py` — domain supervisors
- `src/agents/supervisors/supervisor_factory.py` — `SupervisorFactory`
- `src/agents/communication/communication_protocol.py` — `CommunicationProtocol`
- `src/qa/mast.py` — `MASTLabeler`
- `src/core/kernel/component_keys.py`, `src/api/services/component_catalog.py` — agent, supervisor and domain registries
- `src/api/routes/agent_api.py`, `src/models/agent_api_models.py`, `src/api/services/agent_execution_service.py` — direct agent API
- `src/api/routes/supervisor_api.py`, `src/api/services/supervisor_coordination_service.py`, `supervisor_registry.py`, `real_supervisor_executor.py`, `supervisor_progress_tracker.py` — supervisor API
- `src/api/routes/talkhier_api.py`, `src/api/services/talkhier_session_service.py`, `src/models/talkhier_api_models.py` — TalkHier API
- `src/ai_brain/compaction/constraint_registry.py` — `ConstraintRegistry`
