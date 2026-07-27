# Current agent runtime behavior

The credential-free tests in `tests/characterization/` record the behavior of
Cerebro's current public query, agent, CLI, routing, and execution-trace
surfaces. They call route functions with deterministic service doubles because
full FastAPI startup can initialize optional database, Redis, and provider
resources.

## Entry points and response shapes

| Surface | Current behavior |
| --- | --- |
| `/api/v1/query/research` | Starts process-local execution and reports legacy `pending`, `running`, `completed`, or `failed` strings. The response includes fixed estimate fields and an empty `selected_agents` list. |
| `/api/v1/query/analyze`, `/synthesize` | Translate input into research context. The research route sets its own `api_endpoint`; validated real-time and timeout request fields are not forwarded into execution. |
| `/api/v1/query/methodology`, `/comparison` | Build specialized research requests and delegate to the research route. |
| Query status/results/resume | Reads process-local status. Missing results return 404. Resume accepts UUID project IDs and returns literal `status: resumed`. |
| Query routing metadata | `/routing/strategies` and `/routing/recommend` use local descriptive and length-based heuristics. Invalid JSON context becomes an HTTP exception. |
| `/api/v1/agents` | Direct, chain, mixture, and synthesis-combine execution expose distinct legacy response models. Validation uses local length/type heuristics and fixed estimated cost. |
| CLI `agents` | `query`, `route`, `estimate`, `execute`, `chain`, and `status` map to fixed HTTP requests. |

At the HTTP application boundary, exceptions use
`{"error": {"code", "message", "details"}}`. Direct route-function tests
observe `HTTPException.detail` before middleware conversion.

## Routed execution path

```text
FastAPI query route
  -> DirectExecutionService (in-memory ExecutionStatus)
  -> MASR routing
  -> MASRSupervisorBridge
  -> selected domain supervisor and workers
  -> supervisor quality and verification output
```

`DirectExecutionService` creates an immutable in-process
`src.agents.models.AgentTask`. The bridge and supervisor result control the
legacy execution status and copy output and quality dictionaries into an
in-memory status record.

## Collaboration-mode translation

| MASR mode | Bridge execution mode | Current behavior |
| --- | --- | --- |
| `fast_path` | `parallel` | Defaults to `parallel` if it reaches the bridge; the direct service also has a separate provider-backed fast path |
| `direct` | `sequential` | Explicit translation |
| `parallel` | `parallel` | Explicit translation |
| `hierarchical` | `hybrid` | Explicit translation |
| `debate` | `adaptive` | The bridge does not expose debate topology |
| `ensemble` | `parallel` | Voting and ensemble semantics are not represented in the bridge configuration |

The bridge derives a `balanced` strategy and `0.85` quality threshold even when
the routing result records `quality_focused`. The supervisor API advertises
`sequential`, `parallel`, `hierarchical`, `adaptive`, and `debate`; its executor
maps the first four to `sequential`, `parallel`, `hybrid`, and `adaptive`, while
`debate` falls back to `parallel`.

## State and failure behavior

| Area | Current behavior |
| --- | --- |
| Execution state | `DirectExecutionService` keeps query execution status in process memory |
| Routing | Collaboration and coordination modes use the translations above |
| Verification | Quality scores and completion are supervisor outputs stored in process |
| Tools | Public query and agent responses do not expose typed durable tool-invocation records |
| Providers | Fast-path execution requires a configured provider and otherwise fails or uses the existing fallback path |
| Persistence | Checkpoint failures are logged without creating a durable event history |
| Cancellation | Service cancellation updates process-local status |
| Live updates | `WSMessage` carries project-scoped message data without persisted event replay |

The current runtime does not import `src.core.contracts`. See
[Agent-system contracts](agent-system-contracts.md) for the standalone contract
package and [Agent-system compatibility](agent-system-compatibility.md) for the
relationships between current representations.
