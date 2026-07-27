# Agent-system compatibility

Cerebro currently contains several task, routing, execution, persistence, and
event representations. The `src.core.contracts` package exists alongside these
types and is not yet imported by the runtime.

## Task representations

| Current representation | Current role | Canonical counterpart and observed difference |
| --- | --- | --- |
| `src.models.research_project.AgentTask` | Mutable research/domain task | Closest to `Task`; it embeds a result dictionary and lacks run identity, stable task key, idempotency, pinned versions, dependencies, and attempt history |
| `src.agents.models.AgentTask` | Frozen in-process worker invocation | Contains dispatch input for work represented by `Task` and `Attempt`; it lacks lifecycle, run, version, idempotency, and persistence identity |
| `src.models.db.agent_task.AgentTask` | SQLAlchemy project task row | Contains task and retry state; `project_id` is not a canonical `run_id`, output is embedded, and `retrying` does not create a separate attempt record |

### Status relationships

| Existing value | Canonical value or representation |
| --- | --- |
| `pending` | `pending` |
| `queued` | `ready` |
| `in_progress` | `running` |
| `completed` | `succeeded` |
| `failed` | `failed` |
| `cancelled` | `cancelled` |
| `retrying` | A new `Attempt` with the next ordinal |

Existing APIs continue to return their existing status strings.

## Routing representations

| Current representation | Canonical counterpart and observed difference |
| --- | --- |
| `src.ai_brain.router.masr.RoutingDecision` | Contains strategy, collaboration mode, worker allocation, limits, estimates, and adaptive attribution represented by parts of `RoutingPolicy` |
| `src.ai_brain.router.routing_types.AgentAllocation` | Contains worker and concurrency values represented by routing-policy fields |
| `src.ai_brain.router.routing_types.RoutingExecutionPolicy` | Contains fixture, provider, and memory controls not represented as ambient defaults in `RoutingPolicy` |
| `src.ai_brain.integration.masr_supervisor_bridge.SupervisorConfiguration` | Contains the reduced execution configuration currently passed from MASR to a supervisor |

MASR currently selects routing behavior. Run, task, attempt, event, and
canonical contract state remain outside MASR.

## Run, output, and event representations

| Current representation | Canonical counterpart and observed difference |
| --- | --- |
| `src.models.research_project.ResearchProject` | Contains project lifecycle and workflow-specific input/output corresponding to parts of `Run` |
| `src.agents.models.AgentResult` | Contains an in-process attempt outcome and output; `confidence` remains legacy metadata rather than calibrated correctness |
| `src.models.websocket_messages.WSMessage` | Contains project-scoped live message data rather than an ordered `RunEvent` record |
| Current report and research-result models | Contain output represented separately by `Artifact`, `Evidence`, `ClaimSupport`, and `EvaluationResult` |

## Entry points

`/query`, `/agents`, and the corresponding CLI commands are the active public
entry points. They use the existing request and response models and do not
create `src.core.contracts.Run` records.

The `/workflows` and `/runs` frontend resource clients are separate from this
Python contract package. The current backend does not expose the full neutral
workflow/run resource surface consumed by the opt-in workbench integration
test.
