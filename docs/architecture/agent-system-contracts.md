# Agent-system contracts

The `src.core.contracts` package contains Cerebro's typed agent-system records.
The package is currently independent of the FastAPI execution path, database
models, providers, supervisors, workers, and WebSocket messages.

## Contract catalogue

| Contract | Current purpose |
| --- | --- |
| `WorkflowDefinition` | Identifies a workflow, its control mode, requirements, and supported execution modes |
| `RoutingPolicy` | Records routing strategy, collaboration mode, concurrency, retry, timeout, and metadata |
| `Run` | Represents one workflow execution and its lifecycle |
| `Task` | Represents one unit of work within a run |
| `Attempt` | Represents one execution attempt for a task |
| `ToolInvocation` | Records a typed tool request, outcome, trust labels, and timing |
| `Artifact` | Identifies produced content and its provenance |
| `Evidence` | Identifies source material, locators, hashes, and trust |
| `ClaimSupport` | Relates a claim to evidence and a support status |
| `EvaluationResult` | Records one evaluator's dimension-specific result |
| `RunEvent` | Represents an ordered, deduplicated event for a run |

## Validation and serialization

Every top-level contract currently uses `schema_version="1.0"`. Contract models:

- reject unknown fields and other schema values;
- use frozen Pydantic records;
- require timezone-aware timestamps and non-empty opaque identifiers;
- serialize enums by value;
- preserve ordered relationships as tuples;
- round-trip deterministically through Pydantic JSON serialization.

Nested JSON objects are exposed as read-only mappings and nested arrays as
tuples. Source dictionaries and lists are copied, so later source mutation does
not alter a contract. Pydantic serialization emits ordinary JSON objects and
arrays. Nested `NaN`, positive infinity, and negative infinity are rejected
because they cannot round-trip as standard JSON numbers.

Domain identifiers include their semantic versions:

- workflows use `(workflow_definition_id, workflow_version)`;
- policies use `(routing_policy_id, routing_policy_version)`;
- tools use `(tool_name, tool_version)`;
- evaluators use `(evaluator_id, evaluator_version)`;
- event meanings use `(event_type, event_type_version)`.

## Lifecycle behavior

Lifecycle methods return a validated replacement record and never mutate the
previous snapshot. Raw strings and status enums belonging to another lifecycle
are rejected even when their serialized values match.

Timestamps are monotonic:

- `updated_at` cannot precede `created_at`, `started_at`, or `completed_at`;
- `completed_at` appears only on terminal records;
- equality is valid at a transition boundary.

### Run

| From | Legal next states |
| --- | --- |
| `created` | `queued`, `failed`, `cancelled` |
| `queued` | `running`, `failed`, `cancelled` |
| `running` | `succeeded`, `failed`, `cancelling` |
| `cancelling` | `cancelled`, `failed` |
| `succeeded`, `failed`, `cancelled` | none |

### Task

| From | Legal next states |
| --- | --- |
| `pending` | `ready`, `failed`, `cancelled`, `skipped` |
| `ready` | `running`, `failed`, `cancelled`, `skipped` |
| `running` | `succeeded`, `failed`, `cancelling` |
| `cancelling` | `cancelled`, `failed` |
| `succeeded`, `failed`, `cancelled`, `skipped` | none |

### Attempt

| From | Legal next states |
| --- | --- |
| `created` | `running`, `failed`, `cancelled` |
| `running` | `succeeded`, `failed`, `cancelling`, `timed_out` |
| `cancelling` | `cancelled`, `failed` |
| `succeeded`, `failed`, `cancelled`, `timed_out` | none |

Retries are represented by a new `Attempt` with the next ordinal. Terminal
attempts do not reopen.

## Cancellation behavior

`Run`, `Task`, and `Attempt` preserve the first cancellation request in paired
`cancellation_requested_at` and `cancellation_reason` fields.

- Cancellation before active execution transitions directly to `cancelled`.
- Active execution transitions to `cancelling`.
- Acknowledgement transitions active work from `cancelling` to `cancelled`.
- Cleanup failure may transition active work from `cancelling` to `failed`.
- Duplicate requests preserve the first request timestamp and reason.
- Late requests do not rewrite terminal outcomes.

A cancellation request cannot precede creation or active execution, and cannot
follow the record's update or completion timestamp.

## Idempotency and ordering fields

Contracts expose stable idempotency, deduplication, correlation, causation, and
sequence fields. `RunEvent.sequence` defines per-run ordering.
`ToolInvocation`, task attempts, artifacts, evidence, claim support, and
evaluation results each carry stable identities suitable for duplicate
detection by their consumers.

## Trust and provenance

`ToolInvocation`, `Artifact`, and `Evidence` carry trust labels. Evidence
contains a source snapshot reference, content hash, and locator.
`ClaimSupport` records `supported`, `contradicted`, or `insufficient` and
requires evidence references for supported and contradicted results.
`EvaluationResult.score` is dimension-specific and is not a calibrated
probability.

## Runtime usage

The current HTTP, CLI, MASR, supervisor, worker, persistence, and WebSocket
implementations do not import `src.core.contracts`. Their current behavior is
documented in [Current agent runtime behavior](agent-system-runtime.md), and
their data relationships are listed in
[Agent-system compatibility](agent-system-compatibility.md).
