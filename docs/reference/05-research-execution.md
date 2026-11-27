# Research execution

Research execution is the plan path: a research request is bound to a stored execution authority, routed by MASR (the Multi-Agent System Router), compiled into an immutable `ExecutionPlan`, admitted as a durable run and executed by plan-local workers. `DirectExecutionService` in `src/api/services/direct_execution_service.py` owns the path from admission to the final status.

## Summary

| Feature | Status | Entry point | Source |
|---|---|---|---|
| Start a routed execution | Active | `POST /api/v1/query/research` | `src/api/routes/query_api.py` (`intelligent_research_query`), `DirectExecutionService.start_research_execution` |
| Execution authority binding | Active | `authority_reference` request field | `src/api/services/execution_authority_resolver.py` (`PersistedExecutionAuthorityResolver`) |
| Plan compilation | Active | `ExecutionPlanCompiler.compile` | `src/ai_brain/router/execution_plan_compiler.py` (`ExecutionPlanCompiler`) |
| Durable admission | Active | `DirectExecutionService._admit_run` | `src/api/services/direct_execution_service.py` |
| Plan topology execution | Active | `MASRSupervisorBridge.execute_execution_plan` | `src/ai_brain/integration/execution_plan_topology.py` (`ExecutionPlanTopologyExecutor`) |
| Budget ledger | Active | `BudgetLedger` created per execution | `src/core/budget.py` (`BudgetLedger`, `BudgetExceededError`) |
| Status, results, resume | Active | `GET /api/v1/query/execution/{execution_id}/status`, `GET /api/v1/query/execution/{execution_id}/results`, `POST /api/v1/query/execution/{project_id}/resume` | `src/api/routes/query_api.py` |
| Cancellation | Active | `POST /api/v1/research/projects/{project_id}/cancel` | `src/api/routes/research.py` (`cancel_research_project`), `DirectExecutionService.cancel_execution` |
| Startup rehydration | Active | `restore_active_executions` during `lifespan` | `DirectExecutionService.restore_active_executions` |
| Terminal-outcome reconciliation | Active | `reconcile_pending_terminal_transitions` | `DirectExecutionService.reconcile_pending_terminal_transitions` |
| Fixture mode | Not wired | `execution_policy.fixture_mode` argument | `DirectExecutionService.start_research_execution`, `src/ai_brain/router/routing_types.py` (`RoutingExecutionPolicy`) |
| Multi-domain fan-out and provider fast path | Not wired | none | `DirectExecutionService` (`_fast_path_provider`), `src/core/config.py` (`Settings.MULTI_DOMAIN_MERGE_STRATEGY`) |

## Entry points

| Route | Behaviour |
|---|---|
| `POST /api/v1/query/research` | Builds a `ResearchProject` from the request, calls the research kernel with `authority_reference` and the caller's `organization_id` and returns an `IntelligentQueryResponse`. The kernel resolves the registered routed-research workflow, which calls `DirectExecutionService.start_research_execution`. |
| `POST /api/v1/query/analyze`, `POST /api/v1/query/synthesize` | Build an `IntelligentQueryRequest` (analyze sets `routing_strategy` to `quality_focused` when `analysis_type` is `exhaustive`; synthesize sets `balanced`) and call `intelligent_research_query`. |
| `POST /api/v1/query/literature`, `POST /api/v1/query/methodology`, `POST /api/v1/query/comparison` | Build an `IntelligentQueryRequest` from query parameters and call `intelligent_research_query`. They build the request without an `authority_reference`. |
| `POST /api/v1/research/projects` | Creates a `research_projects` row, then calls `DirectExecutionService.start_research_execution(project, authority_reference=...)` when the request carries one, or `start_research_execution(project)` when it does not. |
| `POST /api/v1/research/projects/{project_id}/cancel` | Marks the project cancelled in the database and cancels the matching in-flight execution. |
| `GET /api/v1/research/projects/{project_id}`, `GET .../progress`, `GET .../results` | Read project state from the database and from `active_executions`. |
| `POST /api/v1/research/projects/{project_id}/refine` | Returns 501. |

Request and response schemas are in [HTTP API](03-http-api.md); this page covers behaviour.

`POST /api/v1/research/projects` calls `start_research_execution` without an `organization_id`. When the request carries an `authority_reference`, `enforce_authority_tenant` rejects a missing `organization_id` with `ExecutionAuthorityUnavailableError`; without one, `ExecutionAuthorityRequiredError` is raised first. Either way the start attempt raises. The route catches the exception, logs `Failed to start execution, project created but not started` and returns the created project with status `pending`. The query routes pass `organization_id=tenant_context.organization_id`.

## Execution authority

An execution starts only from a stored authority. `ExecutionAuthorityReference` (`src/models/execution_authority.py`) is the request field `authority_reference`: an `authority_id` of 1 to 255 characters and an `authority_version` of 1 to 100 characters, with extra fields forbidden. `ExecutionAuthorityBinding` is the trusted record it resolves to. It carries the fixed `Run` (including its `run_id`, `tenant_id` and `idempotency_key`), the `WorkflowDefinition` and `RoutingPolicy`, the allowed domains, the supervisor identity, the `WorkerAssignment` list, the routing edges, the `ProviderModelPolicy`, the `ExecutionBudget`, the stop conditions, the evaluator requirements, the deadline and the clock and plan-id factory used at compile time.

`DirectExecutionService._resolve_execution_authority` runs before MASR is called or any state is created:

- No `authority_reference` raises `ExecutionAuthorityRequiredError` (`EXECUTION_AUTHORITY_REQUIRED`).
- No configured resolver, a reference the resolver does not hold, a missing `organization_id` or a binding owned by another organization raises `ExecutionAuthorityUnavailableError` (`EXECUTION_AUTHORITY_UNAVAILABLE`). A cross-tenant reference raises the same error as an unknown one.

`PersistedExecutionAuthorityResolver.resolve` is synchronous and reads only an in-process cache keyed by `(authority_id, authority_version)`. It never queries the database. The cache is filled by `warm_from_snapshots` at startup, which loads every row of `agent_run_config_snapshots` and by `register`, which `DirectExecutionService._persist_config_snapshot` calls when a run is admitted. A snapshot row added to the database after startup is not visible to `resolve` until the process restarts. A resolver placed on `app.state.execution_authority_resolver` before startup takes priority over the persisted one; `MappingExecutionAuthorityResolver` is the in-process resolver built from a fixed mapping of bindings.

No route, CLI command or script in the repository creates an authority binding. The only writers of `agent_run_config_snapshots` are `RunConfigSnapshotRepository.create_snapshot`, called through `_persist_config_snapshot` for a binding that has already been resolved. A deployment must therefore supply the first snapshot rows (or a resolver on `app.state`) itself. Because the binding fixes the run, submitting the same `authority_reference` twice names the same run (see [Admission](#admission)).

The 422 responses carry `{"code": ...}` in the error detail. `intelligent_research_query` maps `ExecutionAuthorityError` to 422, and the routes differ in what they do with it:

- `/research`: 422 with the authority code.
- `/analyze` and `/synthesize`: the 422 `HTTPException` is caught by `except Exception` and becomes a 500 whose detail begins `Analysis query failed:` or `Synthesis query failed:`.
- `/literature`, `/methodology` and `/comparison`: 422 `EXECUTION_AUTHORITY_REQUIRED`, because they build the request without an `authority_reference`.
- Any other exception raised while starting an execution (compile drift, an unsupported topology, a budget that cannot hold the workers, a failed admission write or the concurrent-execution cap): 500 with detail `Query execution failed: ...`.

`AUTHORITY_REFERENCE_INCOMPLETE` is a separate code, emitted only by `_optional_authority_reference` in `src/api/routes/agent_api.py`, for an `authority_id` supplied without an `authority_version` or the reverse on the agent convenience routes.

## Routing proposal

`DirectExecutionService._propose_routing` calls `MASRouter.route(query=..., context=...)` with a context holding the query text, domains, project id and user id, merged with the caller's `context`. The returned MASR `RoutingDecision` is a proposal only: it cannot supply execution authority. See [MASR routing](06-masr-routing.md).

## Plan compilation

`ExecutionPlanCompiler.compile(proposal, binding)` returns an `ExecutionPlan` built entirely from the binding and raises `ValueError` when the proposal drifts from it:

- The proposal's collaboration mode must be a `CollaborationMode` whose entry in `COLLABORATION_MODE_SUPPORT` is `IMPLEMENTED`. `FAST_PATH`, `DIRECT`, `PARALLEL` and `HIERARCHICAL` are implemented; `DEBATE` and `ENSEMBLE` are `EXPLICITLY_REJECTED` (`src/core/contracts/execution_plan.py`).
- The proposal's routing strategy and collaboration mode must equal `binding.routing_policy`.
- The proposal's worker types, supervisor type, complexity-analysis domains and primary and fallback provider/model routes must equal the binding's.

The compiled plan has `plan_version` 1, no amendment and `compiled_at` from the binding's clock. `RoutingDecision` and `ExecutionPlan` validate their own shape: `FAST_PATH` and `DIRECT` require exactly one worker and no supervisor or edges, `PARALLEL` requires at least two workers, `HIERARCHICAL` requires a supervisor and `max_parallel_tasks` cannot exceed the worker count.

## Admission

`DirectExecutionService.start_research_execution` is idempotent per run. It resolves the binding, then returns the existing execution id when the run is already known: an admission still in flight (`_admissions_in_flight`), a live or restored execution (`_execution_ids_by_run_id`), or a run found in the database (`_durably_recorded_execution_id`, which includes terminal runs and ignores runs recorded under another organization). A lookup that cannot read the database raises `RunAdmissionError` rather than risk running the work twice.

For a new run, `_admit_new_execution` runs these steps in order:

1. `_propose_routing`, then `ExecutionPlanCompiler.compile`.
2. `MASRSupervisorBridge.admit_execution_plan`, which applies the topology admission rules below.
3. A capacity check: executions whose status is `pending` or `running` are counted, and `RuntimeError("Maximum concurrent executions (50) reached")` is raised at `max_concurrent_executions`. The value 50 is set in `DirectExecutionService.__init__` and is not read from settings. Finished executions remain in `active_executions` and do not count.
4. `_admit_run`, when a session factory is configured. In one transaction it writes the run (`created`, then `queued`), one task (`pending`, then `ready`), one attempt, one `CapabilityGrant` per tool in each worker's allowlist (from `PlanCapabilityIssuer.issue`), the config snapshot (through the resolver's `register`, or `RunConfigSnapshotRepository.create_snapshot`), and a `run.admitted` event with its outbox row. A failed write raises `RunAdmissionError` and nothing is started. A conflict on the unique `(tenant_id, idempotency_key)` of `agent_runs` caused by another process that admitted the same run is coalesced onto that run's execution. Without a session factory nothing is written.
5. The execution is added to `active_executions` and `_execute_research_workflow` is started as a background task.

See [durable runs and events](08-durable-runs-and-events.md) for the tables and the event stream.

## Plan topology execution

`ExecutionPlanTopologyExecutor.admit` accepts a plan only when all of these hold, and otherwise raises `ExecutionTopologyUnsupportedError` (`EXECUTION_TOPOLOGY_UNSUPPORTED`):

- The plan has exactly one domain.
- `evaluator_requirements` is empty and `stop_conditions` is exactly `("complete",)`.
- `budget.max_attempts_per_task` is at most 1.
- `fallback_mode` is `FAIL_CLOSED` with no fallback routes.
- The mode is `DIRECT`, `FAST_PATH`, `PARALLEL` or `HIERARCHICAL`. `DIRECT`, `FAST_PATH` and `PARALLEL` plans declare no supervisor and no edges. A `HIERARCHICAL` plan has edges whose relation is `delegates` or `depends_on`, a supervisor root with no incoming edge, every worker reachable from it and no cycle.
- Every worker type is registered as an agent in the component registry.
- `check_plan_fits`: `max_total_tokens` is at least the worker count times `MIN_WORKER_CALL_TOKENS` (2000), otherwise `BudgetExceededError` is raised.

`FAST_PATH` is executed exactly like `DIRECT`: one worker, no separate call. A `HIERARCHICAL` plan's supervisor node is the root of the worker graph only; no supervisor object runs.

`execute` runs workers in waves. In each wave every worker whose dependencies have completed runs under `asyncio.gather`, bounded by a semaphore of `budget.max_parallel_tasks`. Dependencies come from edges between workers. Each worker runs through `MASRSupervisorBridge._execute_plan_worker`, which builds the agent class from the registry with a config holding the worker's `permission_scopes` and `tool_allowlist` and gives the worker task a timeout of `budget.task_timeout_seconds`. When `MCP_TOOL_PATH_ENABLED` is true the worker also receives an `MCPIntegration` built from the run's capability grants (default `true`).

The result maps each stable `worker_id` to that worker's output, and `workers_used` is the number of workers that ran. A `PlanProviderUnavailableError` from a worker records an observability event and propagates. See [Worker outcomes and data handoff](#worker-outcomes-and-data-handoff) for what counts as success.

`BudgetLedger` is created once per execution when `_execute_research_workflow` first runs, and passed to every worker through the task context key `BUDGET_LEDGER_CONTEXT_KEY`, so a workflow retry draws on spend already recorded. Before each model call the ledger reserves the prompt estimate plus `max_tokens` and refuses a call that cannot fit; a response that reports no usage is charged its reservation and the ledger then refuses further calls. The executor checks `ledger.ensure_not_exceeded()` before each worker and after each wave. `BudgetExceededError` has code `BUDGET_EXCEEDED` and is never retried.

## Worker outcomes and data handoff

`classify_worker_result` normalizes every legacy `AgentResult` into a `WorkerOutcome` (`worker_id`, `status`, `code`, `reason`). Only `status == "success"` counts; the status is one of `success`, `failed`, `timed_out` or `invalid_output`, with these codes:

| Code | Status | Cause |
| --- | --- | --- |
| `WORKER_FAILED` | `failed` | The worker returned `failed`. |
| `WORKER_TIMEOUT` | `timed_out` | The worker returned `timeout`, `timed_out` or `timed-out`. |
| `WORKER_STATUS_UNKNOWN` | `failed` | Any other non-`success` status. |
| `ZERO_CONFIDENCE` | `invalid_output` | The worker reported `success` at confidence 0 or below, an abstention rather than a finding. |
| `OUTPUT_SCHEMA_VIOLATION` | `invalid_output` | The output does not validate against the worker's non-empty `output_schema` (JSON Schema draft 2020-12, local `$ref` only). `reason` names the path and keyword, never the content. |
| `UPSTREAM_PAYLOAD_TOO_LARGE` | `failed` | The rendered upstream data for the worker exceeds 64 KiB (`UPSTREAM_PAYLOAD_LIMIT_BYTES`); it is never truncated and the worker is not called. |
| `UPSTREAM_FAILED` | `blocked` | Reported in `failures` only: a predecessor did not succeed, so the worker never ran. |

`PlanExecutionResult.succeeded` is true only when no worker is blocked and every outcome succeeded. After a wave with any unsuccessful worker no further wave is scheduled. `failures` lists one content-free record per unsuccessful or blocked worker.

When the aggregate is not successful, `_fail_run_on_worker_outcomes` records run, task and attempt `failed` with event `run.failed` and failure code `WORKER_FAILED`, without retrying the workflow. The event payload carries `failures` and `partial_worker_ids`, the workers that succeeded. The journaled `agent_results` keeps every worker's output as diagnostics, `final_output` stays unset, no final report or claim support is written, and `successful_executions` is not incremented.

A worker with direct predecessors receives their validated outputs in the reserved input key `upstream_results`: a mapping from each direct predecessor's `worker_id` to `{"worker_id", "task_id", "execution_plan_id", "output"}`. Siblings, descendants and unrelated workers never appear in it. `adapt_upstream_input` turns the mapping into what the worker class reads, as escaped `<upstream_result worker="...">` blocks of untrusted evidence: `previous_result` for a class with `upstream_input = UpstreamInput.SINGLE` (one parent) or `agent_outputs`, keyed by parent, for `UpstreamInput.KEYED`. A worker class that cannot consume its parent count is rejected at admission. Plans whose `workflow_definition_version` has a major version below 2 are rejected when they contain a dependency edge.

`RESERVED_RUNTIME_INPUT_KEYS` (`upstream_results`, `previous_result`, `agent_outputs`, `background`) belong to the runtime: client-supplied values for them are dropped before a worker is called, and `objective`, `output_schema`, `permission_scopes`, `tool_allowlist` and `provider_model_policy` are always set from the plan.

## Fixture mode

> **Not wired.** `start_research_execution` accepts `execution_policy` and `fixture_result`; with `execution_policy.fixture_mode` set, it returns `fixture_result` as the execution output without running any worker. No caller passes them: `intelligent_research_query` reaches `start_research_execution` through the kernel adapter, whose call signature has neither argument, and `POST /api/v1/research/projects` passes only `authority_reference`.

When `execution_policy.fixture_mode` is set, `_propose_routing` calls `MASRouter.route` with the policy, and `_execute_research_workflow` requires `fixture_result` to be a dict (otherwise it raises `ValueError`), copies it into `final_output` and `agent_results`, sets `workers_used` to 0, records the succeeded transition with payload `{"fixture_mode": true}` and writes the checkpoint phase `fixture_completed`.

## Completion

On success `_execute_research_workflow` stores the topology result in `agent_results` and `final_output`, then calls `_persist_transition` for run, task and attempt `succeeded` with event `run.succeeded`. `_commit_transition` writes these in one transaction:

- The run, task and attempt transitions and the attempt's journaled result (`final_output`, `agent_results`, `errors`).
- A final-report `Artifact` of kind `research_report` and media type `application/json`, holding the canonical JSON of `final_output` and its SHA-256, with trust `DERIVED_UNTRUSTED` and storage URI prefix `memory://artifacts`.
- Claim-support records: `build_inventory` extracts the material claims from the persisted report text and `ClaimSupportResolver.resolve` is called with no verdicts, so each claim is recorded as `unsupported` with absent-evidence reason `NOT_ATTEMPTED`.
- The event and its outbox row.

A transaction that fails is retried up to `durable_write_max_attempts` (3) times, with a delay of `durable_write_retry_seconds` (0.1 s) doubled on each retry. A terminal write that still fails is parked in `_pending_terminal_outcomes`, the execution keeps a non-terminal status with the phase `awaiting_durable_confirmation`, and a background reconciler retries every `durable_reconcile_interval_seconds` (1 s) for up to `durable_reconcile_max_seconds` (300 s) before it logs `terminal_outcome_reconciliation_abandoned`. A write the durable record refuses as an illegal transition (`REJECTED`) realigns the in-memory status with the status the database holds. See [tool boundary and evidence](09-tool-boundary-and-evidence.md) for claim and evidence records.

## Status, results, resume, cancel

`ExecutionStatus.status` takes the values `pending`, `running`, `completed`, `failed` and `cancelled`. Status and results reads are scoped to the caller's organization: an execution of another organization, or one with no recorded owner, reads as not found (`_visible_execution`).

| Route | Behaviour |
|---|---|
| `GET /api/v1/query/execution/{execution_id}/status` | Returns `execution_id`, `status`, `progress_percentage`, `current_phase`, `supervisor_type`, `workers_used`, `routing_decision`, `execution_plan` (identifiers, collaboration mode, primary provider and model and the numeric budget; never scopes, tool allowlists or edges), `execution_time_seconds`, `errors` and `timestamp`. 404 when not visible. |
| `GET /api/v1/query/execution/{execution_id}/results` | `completed` returns `final_output`; `failed` returns `{"error": "Execution failed", "details": [...]}`; otherwise `{"status", "progress"}`. 404 when the execution is not visible or the returned value is empty. |
| `POST /api/v1/query/execution/{project_id}/resume` | 400 for a `project_id` that is not a UUID. `DirectExecutionService.resume_execution` returns `None` (404) without a session factory, without an `organization_id`, when the project is not in the caller's organization or when the project has no recoverable checkpoint (`CheckpointRepository.get_recovery_point`). Otherwise it rebuilds an `ExecutionStatus` from the latest recoverable checkpoint, registers it in `active_executions` and returns its id. It does not start any work. |
| `POST /api/v1/research/projects/{project_id}/cancel` | Sets the project to `cancelled` in the database, and for the project's execution calls `cancel_execution`. |

`cancel_execution` applies to executions whose status is `pending` or `running`. It records the request on the run, task and attempt (`Run.request_cancellation`), which moves a `running` durable run to `cancelling` and any other non-terminal run (for example one still `queued`, before `run.started` is written) directly to `cancelled`. It appends `run.cancellation_requested` and sets the in-memory status and phase to `cancelled`. It returns `False` when the database cannot record the request. It does not signal the background task. When the task finishes later after a move to `cancelling`, a success cannot be recorded because `cancelling` admits only `cancelled` and `failed`; `_realign_with_durable_record` then sets the in-memory status to the status mapped from `cancelling`, which is `running`, with phase `cancelling`. A failure is recorded as `failed`.

`restore_active_executions` runs once during startup. It reads every non-terminal run (all organizations), rebuilds an `ExecutionStatus` for each from the run, task, attempt, capability-grant and journaled-result rows and adds it to `active_executions`. It rebuilds status only; no code in `src` re-dispatches a restored run. `reconcile_pending_terminal_transitions` is the retry pass described under Completion.

Progress updates are published to subscribed WebSocket clients when the execution starts, after routing, before the topology runs and when the execution ends, through `EventPublisher.publish_progress_update` (skipped when `project_id` is not a UUID). The durable event stream is separate; see [durable runs and events](08-durable-runs-and-events.md).

## Retries and checkpoints

`_execute_research_workflow` carries a tenacity `@retry` of up to 3 attempts (`_WORKFLOW_MAX_ATTEMPTS`) with exponential wait between 4 and 10 seconds. `BudgetExceededError` is excluded from retry. A failure before the third attempt appends a `run.attempt_failed` event and re-raises, leaving the run `running`; the third failure, or a budget failure, records `failed` (event `run.failed`, or `run.budget_exceeded` with the ledger usage payload and `failure_code`).

`_checkpoint` writes a `WorkflowCheckpoint` row through `CheckpointRepository` and logs and continues when it fails or no session factory is configured. The phases written are `masr_routing` (after the routing decision and plan are stored on the status), `plan_topology_execution` (before the topology runs), `completed` and, in fixture mode, `fixture_completed`.

## Response of POST /api/v1/query/research

`IntelligentQueryResponse` is built from the `ExecutionStatus` immediately after admission. `routing_decision` is assigned by the background task `_execute_research_workflow`, so a response built before that task has run has `routing_decision` `{}`, `selected_agents` `[]`, `estimated_cost` and `estimated_quality` `null` and `supervisor_type` `research`. When the decision is present, `selected_agents` is `routing_decision["agent_allocation"]["worker_types"]` and `estimated_cost` and `estimated_quality` come from the same dictionary.

These fields are always the following on this path: `quality_scores` is `{}` (nothing on the plan path writes it), `confidence`, `routing_time_ms`, `routing_accuracy`, `cost_accuracy` and `completed_at` are `null`, `query_id` is the generated project id and `execution_time_seconds` and `total_time_seconds` both carry `ExecutionStatus.execution_time_seconds`. Use `GET .../status` for the recorded routing decision and plan summary.

## Multi-domain queries and the provider fast path

> **Not wired.** `DirectExecutionService` carries dead attributes and the multi-domain merge settings are read nowhere; the list is in [not wired](15-not-wired.md#dead-execution-attributes). The topology executor rejects plans with more than one domain.

`QueryAnalyzer` attaches a `QueryDecomposition` to the complexity analysis when `QueryAnalyzer` identifies more than one domain and `general` is not among them. No code in `src/api` reads that decomposition. A multi-domain query is rejected before any work starts, in one of two ways: when the binding lists one domain, `ExecutionPlanCompiler` raises `MASR proposal domains do not match authority`; when the binding lists several, `ExecutionPlanTopologyExecutor.admit` raises `v1 topology requires exactly one domain`.

## Source map

- `src/api/services/direct_execution_service.py`: `DirectExecutionService`, `ExecutionStatus`, admission, durable transitions, cancellation, rehydration
- `src/api/routes/query_api.py`: query routes and response model
- `src/api/routes/research.py`: research project routes
- `src/api/services/research_kernel.py`: adapter between the routes and the service
- `src/api/services/execution_authority_resolver.py`: authority errors and resolvers
- `src/models/execution_authority.py`: `ExecutionAuthorityReference`, `ExecutionAuthorityBinding`
- `src/ai_brain/router/execution_plan_compiler.py`: `ExecutionPlanCompiler`
- `src/core/contracts/execution_plan.py`: `ExecutionPlan`, `RoutingDecision`, `ExecutionBudget`, collaboration-mode support table
- `src/ai_brain/integration/execution_plan_topology.py`: `ExecutionPlanTopologyExecutor`
- `src/ai_brain/integration/masr_supervisor_bridge.py`: `MASRSupervisorBridge.execute_execution_plan` and `_execute_plan_worker`
- `src/core/budget.py`: `BudgetLedger`, `BudgetExceededError`, `check_plan_fits`
- `src/core/capabilities/issuer.py`: `PlanCapabilityIssuer`
- `src/core/claims/`: `build_inventory`, `ClaimSupportResolver`, `ClaimSupportRecorder`
- `src/repositories/checkpoint_repository.py`: `CheckpointRepository`
