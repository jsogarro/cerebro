# Durable runs and events

Every research execution admitted through `DirectExecutionService` is recorded in the database as a run with one task and one attempt, a frozen configuration snapshot and an ordered, append-only event log. The log feeds an outbox relay and two replayable stream endpoints. Process memory (`active_executions`) holds the live view; the database rows are the record that survives a restart.

## Summary

| Feature | Status | Entry point | Source |
|---|---|---|---|
| Run, task and attempt rows with legal-transition checks | Active | `start_research_execution` | `src/api/services/direct_execution_service.py` (`_admit_run`, `_persist_transition`), `src/core/contracts/states.py` |
| Configuration snapshot per run | Active | admission transaction | `src/repositories/run_config_snapshot_repository.py` (`RunConfigSnapshotRepository.create_snapshot`) |
| Parked terminal outcomes and reconciliation | Active | `_persist_transition` | `src/api/services/direct_execution_service.py` (`reconcile_pending_terminal_transitions`) |
| Rehydration of non-terminal runs at startup | Active | API lifespan | `src/api/services/direct_execution_service.py` (`restore_active_executions`) |
| Cancellation request recorded as `cancelling` | Active | `cancel_execution` | `src/api/services/direct_execution_service.py` (`_persist_cancellation`) |
| Workflow checkpoints | Active | `_checkpoint`, `resume_execution` | `src/repositories/checkpoint_repository.py` |
| Append-only event log | Active | `RunEventRepository.append_event` | `src/models/db/run_event.py` (`AgentRunEvent`) |
| Outbox relay to Redis | Active | API lifespan | `src/api/services/outbox_relay.py` (`OutboxRelay`) |
| SSE run event stream | Active | `GET /api/v1/runs/{run_id}/events` | `src/api/routes/run_stream.py` |
| WebSocket run event stream | Active | `/ws/runs/{run_id}` | `src/api/routes/websocket.py` (`run_event_stream_endpoint`) |
| Project progress WebSockets | Active | `/ws`, `/ws/projects/{project_id}`, `/ws/cli/{project_id}` | `src/api/routes/websocket.py` |
| WebSocket health endpoint | Active | `GET /ws/health` | `src/api/routes/websocket.py` (`websocket_health`) |
| `publish_project_event` and `publish_event` logging shims | Not wired | none | `src/api/services/event_publisher.py` |

## Lifecycle model

The contracts in `src/core/contracts/` define three immutable records, `Run` (`lifecycle.py`), `Task`, and `Attempt` (`task_lifecycle.py`). Their statuses and transitions are in `states.py`; the tables below are generated from `RUN_TRANSITIONS`, `TASK_TRANSITIONS` and `ATTEMPT_TRANSITIONS`. A transition outside the table raises `InvalidTransitionError`.

**Run**

| From | Legal targets |
|---|---|
| `created` | `cancelled`, `failed`, `queued` |
| `queued` | `cancelled`, `failed`, `running` |
| `running` | `cancelling`, `failed`, `succeeded` |
| `cancelling` | `cancelled`, `failed` |
| `succeeded`, `failed`, `cancelled` | none (terminal) |

**Task**

| From | Legal targets |
|---|---|
| `pending` | `cancelled`, `failed`, `ready`, `skipped` |
| `ready` | `cancelled`, `failed`, `running`, `skipped` |
| `running` | `cancelling`, `failed`, `succeeded` |
| `cancelling` | `cancelled`, `failed` |
| `succeeded`, `failed`, `cancelled`, `skipped` | none (terminal) |

**Attempt**

| From | Legal targets |
|---|---|
| `created` | `cancelled`, `failed`, `running` |
| `running` | `cancelling`, `failed`, `succeeded`, `timed_out` |
| `cancelling` | `cancelled`, `failed` |
| `succeeded`, `failed`, `cancelled`, `timed_out` | none (terminal) |

Terminal statuses are `TERMINAL_RUN_STATUSES` (`succeeded`, `failed`, `cancelled`), `TERMINAL_TASK_STATUSES` (those plus `skipped`), and `TERMINAL_ATTEMPT_STATUSES` (`succeeded`, `failed`, `cancelled`, `timed_out`). The model validators also require: `completed_at` on terminal records and never on non-terminal ones; `started_at` for `running`, `cancelling` and `succeeded` records (and for `timed_out` attempts), and no `started_at` for `created` or `queued` runs, `created` attempts or `pending`, `ready` and `skipped` tasks; a non-empty `status_reason` for `failed`, `cancelled` and `cancelling` records (and for `skipped` tasks and `timed_out` attempts); and cancellation timestamp and reason provided together. `transition_to(target, at=..., reason=...)` returns a new validated record; it never mutates the old one.

Each table's `status` column carries a CHECK constraint generated from the same enums (`status_check` in `src/models/db/run_lifecycle.py`).

## Persisting transitions

`DirectExecutionService` persists one run, one task and one attempt per execution. The task's `task_key` is the execution id, and its `input` JSON holds `project_id`, `execution_id` and `domains`; the run row has no project column, so project-scoped reads recover the project from the task input. Persistence is active only when the service has a session factory; without one the execution runs in memory and every durable write reports `RECORDED` without writing.

**Admission** (`_admit_run`) runs in one transaction and one commit:

1. `create_run` for the run in `created`, then `record_run_transition` to `queued`.
2. `create_task`, then `record_task_transition` to `ready`.
3. `create_attempt` (ordinal 1, `created`).
4. `PlanCapabilityIssuer.issue` mints the capability grants and `CapabilityRepository.create_grant` stores each; see [tool boundary and evidence](09-tool-boundary-and-evidence.md).
5. The configuration snapshot (below).
6. The `run.admitted` event.

A failure raises `RunAdmissionError` and nothing is started. `agent_runs` is unique on `(tenant_id, idempotency_key)` (`uq_agent_run_tenant_idempotency`); a concurrent admission that loses the insert raises `RunAlreadyAdmittedError`, and the caller coalesces onto the existing execution. Within one process, `_admissions_in_flight` and `_execution_ids_by_run_id` coalesce replays the same way.

**Configuration snapshot.** `_persist_config_snapshot` writes one `agent_run_config_snapshots` row per run in the admission transaction. `PinnedVersions` carries the workflow definition id and version, the routing policy id and version and the event envelope version; it carries no per-component pins. `configuration` is the serialized execution authority binding, and `content_sha256` is the SHA-256 of its canonical JSON (`hash_configuration`: sorted keys, compact separators). When the execution authority resolver has a `register` method, the snapshot is written through it so the resolver's cache and the row agree. The row is append-only.

**Transitions** (`_persist_transition`) plan the new `Run`, `Task` and `Attempt` with the contract's `transition_to`, then write all rows and the event in one transaction through `_commit_transition`. Writes performed by the workflow:

| Event type | Run target | When |
|---|---|---|
| `run.started` | `running` | the workflow begins |
| `run.succeeded` | `succeeded` | the plan finishes, or a fixture-mode run finishes |
| `run.failed` | `failed` | an attempt fails after `_WORKFLOW_MAX_ATTEMPTS` (3) attempts |
| `run.budget_exceeded` | `failed` | a `BudgetExceededError` ends the run immediately |

A failed attempt that is followed by another attempt records a `run.attempt_failed` event and no status change; the run stays `running`. A terminal attempt gets a `journaled_result` of `final_output`, `agent_results` and `errors`. On `succeeded`, the same transaction writes the final-report artifact and its claim-support rows (see [tool boundary and evidence](09-tool-boundary-and-evidence.md)).

Each lifecycle write returns a `DurableWriteOutcome`:

- `RECORDED`: the write landed, or there is nothing to persist (memory-only execution).
- `REJECTED`: the state machine refuses the transition because the durable record already moved past it. Retrying cannot help, so `_realign_with_durable_record` sets the in-memory status from the durable run status instead of publishing the refused one.
- `UNAVAILABLE`: the write did not land.

`_commit_transition` tries up to `durable_write_max_attempts` (3) times, sleeping `durable_write_retry_seconds` (0.1) doubled after each failure. Before writing, it checks `_terminal_transition_already_committed`, which recognizes a complete earlier commit (run, task, attempt, event, artifact and claim rows all matching) so an ambiguous commit is not written twice. Event ids are derived deterministically from the transition, so rebuilding the same transition names the same event.

**Parked terminal outcomes.** When a terminal write is `UNAVAILABLE`, the transition is held in `_pending_terminal_outcomes`. `_settle_terminal_state` withholds the terminal status: the execution keeps a non-terminal status, `current_phase` becomes `awaiting_durable_confirmation` and `unconfirmed_terminal_status` records what is waiting. A background task (`_reconcile_until_drained`) calls `reconcile_pending_terminal_transitions` every `durable_reconcile_interval_seconds` (1.0) for up to `durable_reconcile_max_seconds` (300.0); each write that lands publishes the real terminal status. `close()` makes one last reconciliation attempt. The parked outcome lives in process memory; if the process exits first, the durable record still shows a non-terminal run.

**Cancellation.** `cancel_execution` calls `_persist_cancellation`, which records the request through `Run.request_cancellation`, `Task.request_cancellation` and `Attempt.request_cancellation` and appends `run.cancellation_requested`. A `running` record moves to `cancelling`; a record in any other non-terminal status, such as a run still `queued` before `run.started` commits, moves directly to `cancelled`. `cancel_execution` does not cancel the background workflow task. If the write is not recorded, `cancel_execution` returns `False`. After a recorded request the in-memory status is `cancelled`; a later terminal write the state machine refuses (for example `succeeded` from `cancelling`) is `REJECTED` and realigns the in-memory status to the durable one, which maps `cancelling` to `running`.

**Startup rehydration.** `restore_active_executions` runs once in the API lifespan after the session factory exists. It reads every non-terminal run across tenants (`list_non_terminal_runs(organization_id=None)`), rebuilds each run's `Run`, `Task`, `Attempt`, capability grants and journaled result, and registers an `ExecutionStatus` under the task's `task_key`. Runs with no task or attempt row, or with rows that fail contract validation, are skipped. Durable run statuses map to the in-memory status strings `pending` (`created`, `queued`), `running` (`running`, `cancelling`), `completed`, `failed` and `cancelled`.

**Checkpoints.** `_checkpoint` writes a `workflow_checkpoints` row (status, progress, phase, routing decision, agent results, final output, errors, retry count) at the phases `masr_routing`, `fixture_completed`, `plan_topology_execution` and `completed`, and logs and continues if the write fails. `resume_execution(project_id, organization_id=...)` loads the project in the caller's organization, takes the latest recoverable checkpoint and registers a rebuilt execution status. See [research execution](05-research-execution.md) for the workflow these phases belong to.

## Event log

`agent_run_events` is the ordered record of everything observable about a run. One row is an event envelope:

| Column | Meaning |
|---|---|
| `event_id` | unique id |
| `run_id`, `organization_id` | owning run and tenant |
| `aggregate_type`, `aggregate_id` | `run` and the run id for lifecycle events; `tool_invocation` and the invocation id for tool events |
| `sequence` | per-run position, at least 1 |
| `event_type`, `event_type_version` | event name and payload-shape version |
| `envelope_schema_version` | `1.0` (`EVENT_ENVELOPE_VERSION`) |
| `occurred_at`, `recorded_at` | when it happened and when the database accepted it |
| `producer` | `direct_execution_service` for lifecycle events, `tool_boundary` for the tool boundary's own events, `session-tool-audit-store` for events written by lease-expiry recovery |
| `deduplication_key` | unique per run (`uq_agent_run_event_dedup`) |
| `correlation_id`, `causation_event_id` | optional links |
| `payload` | JSON object |

**Ordering.** `RunEventRepository.append_event` allocates the sequence with `UPDATE agent_runs SET last_event_sequence = last_event_sequence + 1 ... RETURNING`, which takes a row lock on the run for the rest of the transaction, so concurrent appends to one run get distinct, consecutive numbers. `(run_id, sequence)` is unique (`uq_agent_run_event_sequence`). The method validates the envelope through the `RunEvent` contract, requires the caller's organization to equal the run's (`TenantMismatchError` otherwise) and flushes without committing, so the event lands or fails with the state change that produced it.

**Event types written by the application:**

| `event_type` | Payload |
|---|---|
| `run.admitted` | `execution_id`, `task_id`, `attempt_id` |
| `run.started` | `phase` |
| `run.attempt_failed` | `error`, `attempt` |
| `run.succeeded` | `workers_used` and `usage`, or `fixture_mode` |
| `run.failed` | `error` and `usage` |
| `run.budget_exceeded` | `error`, `code`, `dimension`, `limit`, `used`, `requested`, `usage` |
| `run.cancellation_requested` | `reason` |
| `tool.invocation.requested`, `tool.invocation.completed` | tool name and version, capability scope, input digest and trust (requested) or outcome and error code (completed), plus `task_id` and `attempt_id`; written only when tool calls use the durable audit store (see [tool boundary and evidence](09-tool-boundary-and-evidence.md)) |

`usage` is the run's budget ledger at the terminal event: `tokens_used`, `cost_usd` (a decimal string), `tool_invocations` and `unknown_usage_calls`. It is present on `run.succeeded` and `run.failed` only when the run has a ledger, and always on `run.budget_exceeded`.

Lifecycle events use `event_type_version` `1`; tool events use `1.0`.

**Immutability.** `AgentRunEvent` is registered with `register_append_only`, so the ORM refuses updates and deletes. On PostgreSQL, migration `c3f5a1d7b209` also installs a `BEFORE UPDATE OR DELETE` trigger that raises `Table agent_run_events is append-only`. SQLite databases get the ORM guard only.

## Outbox relay

Each `append_event` call takes a `destinations` argument and inserts one `agent_run_event_outbox` row per destination in the same transaction as the event. Lifecycle events request the destination `redis`. Tool audit events carry the destinations configured on the boundary, which are none in the application's boundaries, so they have no outbox rows.

`OutboxRelay` (`src/api/services/outbox_relay.py`) drains the table. `lifespan` creates and starts one relay whenever a session factory exists, with these defaults: `poll_interval_seconds=1.0`, `batch_size=50`, `max_attempts=10`, `claim_lease_seconds=60.0`.

- **Claiming.** A `SELECT` picks up to `batch_size` claimable ids, then an `UPDATE ... RETURNING` that re-checks claimability moves those rows to `in_flight`, stamps `claimed_at` and `claimed_by` and increments `attempts`. A row is claimable when it is `pending` or `failed` with `available_at` in the past, or `in_flight` with a claim older than the lease (or with no `claimed_at`), and in each case has `attempts < max_attempts`.
- **Delivery.** For `redis`, `EventPublisher.publish_run_event` publishes the stored envelope to the Redis channel `research_platform:run_events` with `run_id`, `event_type`, `payload`, `occurred_at`, `event_id`, `sequence` and `idempotency_key`. `publish_run_event` returns `False` when no Redis client exists, so with Redis unset every `redis` outbox row fails, backs off up to 300 seconds and is dead-lettered after `max_attempts`. This channel is the feed for consumers outside this repository, who must dedupe on `idempotency_key`; nothing in this repository subscribes to it. The in-process subscriber in `event_publisher.py` listens on `research_platform:events`, which carries a different message shape, and is unrelated. For `websocket`, the relay wakes local stream subscribers and carries no payload. Any other destination logs `outbox_relay_unknown_destination` and counts as a failed delivery.
- **Retry.** A failed delivery sets `status=failed`, `available_at` to the current time plus `min(2 ** attempts, 300)` seconds and `last_error`.
- **Dead letter.** A row whose `attempts` reaches `max_attempts` becomes `status=failed` with a `last_error` beginning `dead-lettered after N attempts`, and is never claimed again. A claim abandoned at exactly `max_attempts` is dead-lettered by the same rule on the next poll.
- **Delivery guarantee.** Delivery is at least once. The outbox `idempotency_key` is `<destination>:<deduplication_key>` (`delivery_idempotency_key`), stable across redeliveries; consumers dedupe on it.

After each batch the relay notifies `run_stream_hub` for every run whose rows it claimed, whether or not delivery succeeded, so waiting stream subscribers re-read the log.

The `status` column holds `pending`, `in_flight`, `delivered` or `failed` (`EventDeliveryStatus`), enforced by `ck_agent_run_event_outbox_status`; the table also checks `attempts >= 0` and that `delivered` rows carry `delivered_at`.

## Run event streams

Both streams read `agent_run_events` through `RunEventStreamReader` (`src/api/websocket/run_stream.py`) and never receive payloads from the process that produced them. Each batch (default `DEFAULT_BATCH_LIMIT` of 100 events) is a separate short-lived session and a separate organization-filtered query.

**Entitlement.** The caller's token must carry an `organization_id` claim that parses as a UUID (`RunStreamEntitlement.from_organization_claim`). Anonymous access, including the development opt-in, is never accepted for run streams. A run outside the caller's organization raises the same `RunNotVisibleError` as a run that does not exist, so existence is not observable across tenants.

**Cursor.** `RunEventCursor(run_id, last_sequence)` is a position, not a capability. `encode()` returns a URL-safe base64 token of its canonical JSON and `decode()` rejects malformed tokens with `ValueError`. Zero means nothing processed, so replay starts at sequence 1. A cursor naming a different run than the one requested is refused with `RunNotVisibleError`.

**Frames.** Each frame carries `run_id`, `sequence`, `event_id`, `event_type`, `occurred_at`, `payload`, `cursor` (the token to resume from exactly there) and `idempotency_key` (`<destination>:<deduplication_key>`, with destination `sse` or `websocket`).

**Tail.** After replaying from the cursor, the reader waits for either a hub notification or `poll_interval_seconds` (0.5) and re-reads. It ends when a read returns nothing and the run is terminal, after one final read that catches events committed with the terminal status. A hub notification only wakes a subscriber in the same process; subscribers in other API processes advance on the poll interval.

### SSE

`GET /api/v1/runs/{run_id}/events` (bearer token, `Authorization` header). Resume with the `cursor` query parameter or the `Last-Event-ID` header; the query parameter wins when both are present. Each message is:

```
id: <cursor>
event: <event_type>
data: <frame JSON>
```

The response has `Content-Type: text/event-stream`, `Cache-Control: no-cache` and `X-Accel-Buffering: no`. Errors are returned before the stream opens:

| Status | Cause |
|---|---|
| 400 | `cursor` or `Last-Event-ID` is not a valid cursor token |
| 403 | the token has no usable `organization_id` claim |
| 404 | the run does not exist or belongs to another organization |
| 503 | no database session factory is available |

A missing or invalid bearer token fails in `get_current_token` before these checks; see [authentication and tenancy](04-authentication-and-tenancy.md). A cursor for another run, supplied after the response has started, ends the stream without frames.

### WebSocket

`/ws/runs/{run_id}?token=<jwt>&cursor=<token>`. The router-level dependency `require_authenticated_websocket` reads only the `token` query parameter; an `Authorization` header alone does not authenticate a WebSocket. Frames are sent as JSON text messages with the same fields as SSE. Closing behavior:

| Close code | Reason | Cause |
|---|---|---|
| 1000 | `Run stream complete` | the run reached a terminal status and all events were sent |
| 1008 | `Invalid resume cursor` | the `cursor` parameter does not decode |
| 1008 | `Run not found` | the run is not visible to the caller's organization |
| 1008 | authentication reasons | missing or invalid token, or no organization claim |
| 1011 | `Run event stream unavailable` | no session factory |
| 1011 | `Run stream error` | an unexpected error while streaming |
| 1011 | `Authentication service unavailable` | the JWT service cannot be reached |

## Project progress WebSockets

These endpoints deliver progress updates for a research project. They are separate from the run event streams and carry in-process broadcasts rather than log rows.

| Endpoint | Behavior |
|---|---|
| `/ws` | general endpoint; the client sends subscription messages |
| `/ws/projects/{project_id}` | subscribes to the project after an access check |
| `/ws/cli/{project_id}` | same as the project endpoint with client type forced to `cli`; query parameter `format` (default `text`); sends an `info` welcome message |
| `GET /ws/health` | no authentication; returns `status`, `websocket_stats` and `timestamp` |

**Authentication.** Every WebSocket endpoint has `require_authenticated_websocket` as a dependency. It validates the `token` query parameter through the shared JWT service with `allow_anonymous=False`, so a missing token closes the connection (code 1008) and an unavailable authentication service closes it with 1011. Anonymous connections are therefore not possible on any mounted `/ws*` route, whatever `DEV_ALLOW_ANONYMOUS_WEBSOCKETS` is set to. Token claims and validation are described in [authentication and tenancy](04-authentication-and-tenancy.md).

**Authorization on every subscribe.** `_handle_subscription` calls `_principal_may_subscribe_to_project` before every `subscribe` action, and the two project endpoints call it before connecting. `verify_project_access` returns true only when the project exists, is owned by the caller (`user_id`) and lies in the caller's organization, resolved through `ResearchRepository.get_for_user`. It fails closed: a missing user id, organization claim or database session denies, as does an unparseable identifier. The one exception is the development bypass: when `ENVIRONMENT` is `development` and `DEV_ALLOW_ANONYMOUS_WEBSOCKETS` is true (default false), the function returns true for any project, for authenticated connections too. A denied subscribe returns a `SubscriptionResponse` with `success=false` and the message `Access denied to project`; a denied connection on a project endpoint closes with code 1008.

**Client messages** on `/ws` and `/ws/projects/{project_id}` are JSON text:

- `{"type": "subscription", "data": {"action": "subscribe" | "unsubscribe", "project_id": "<uuid>", "message_types": [...], "client_type": "web"}}`. The reply is an `info` message whose `data` is a `SubscriptionResponse` (`success`, `message`, `active_subscriptions`).
- `{"type": "heartbeat_response"}` refreshes the connection's heartbeat.
- Any other `type` is logged as unknown. After five consecutive processing errors the server closes the loop (`MAX_CONSECUTIVE_WS_ERRORS`).

**Server messages** are `WSMessage` objects (`type`, `project_id`, `timestamp`, `data`). `WSMessageType` values:

| Group | Values |
|---|---|
| Progress | `progress` |
| Agent activity | `agent.started`, `agent.progress`, `agent.completed`, `agent.failed` |
| Project lifecycle | `project.started`, `project.completed`, `project.failed`, `project.cancelled` |
| Workflow | `workflow.phase.started`, `workflow.phase.completed` |
| System | `error`, `warning`, `info` |
| Connection | `connected`, `disconnected`, `heartbeat` |

**Connection management** (`ConnectionManager` in `src/api/websocket/connection_manager.py`). `connect` sends a `connected` message with `client_id`, a greeting and `server_time`. A monitor task checks connections every 30 seconds: it sends a `heartbeat` message to each healthy connection and disconnects connections that are inactive or have no heartbeat response within 60 seconds. `broadcast_to_project`, `broadcast_to_user` and `broadcast_to_all` fan messages out, disconnecting clients whose send fails. `get_stats()` returns `total_connections`, `connections_by_type`, `total_project_subscriptions` and `total_user_subscriptions`. Client type derives from the `User-Agent` header: `cli` for `research-cli`, `api` for `python`, `websocket` for `websocket`, `web` otherwise, `unknown` when absent.

**Progress publication.** `DirectExecutionService._publish_progress_update` builds a `ProgressUpdate` (`progress_percentage`, `current_phase`, `current_agent`) and calls `EventPublisher.publish_progress_update`, which broadcasts a `progress` message to the project's local subscribers and publishes it to the Redis channel `research_platform:events` with an `_origin` instance id. Each instance subscribes to that channel and rebroadcasts messages from other instances to its local connections. Without Redis the publisher runs in local-only mode. Progress updates are the only message type the application publishes; the typed `publish_agent_*`, `publish_project_*`, `publish_workflow_phase_*`, `publish_error`, `publish_warning` and `publish_info` methods have no caller outside `event_publisher.py`.

> **Not wired.** `EventPublisher.publish_project_event` and `EventPublisher.publish_event` log a debug line and deliver nothing. The only caller of `publish_event` is `RealTimeDashboard` in the experimentation package, which no other module imports.

**Health.** `GET /ws/health` responds `{"status": "healthy", "websocket_stats": <get_stats()>, "timestamp": "2024-01-01T00:00:00Z"}`. The `timestamp` value is a fixed string, not the current time.

**CLI streaming.** `research-cli projects progress <project-id> --stream` connects to `/ws/cli/{project_id}` through `CLIWebSocketClient` and falls back to polling when streaming fails; `--watch` polls. See [CLI and web clients](13-cli-and-web-clients.md).

## SQLite and PostgreSQL differences

On SQLite the lifespan creates only the research and durable run tables (`research_projects`, `research_results`, `workflow_checkpoints`, `agent_runs`, `agent_run_tasks`, `agent_task_attempts`, `agent_run_config_snapshots`, `agent_run_events`, `agent_run_event_outbox`), so the tool, capability, artifact, evidence and claim tables do not exist there. There are no row-level security policies and no append-only triggers; the ORM guard still blocks updates and deletes of `agent_run_events` and `agent_run_config_snapshots`. The sequence allocation, outbox claiming and streams use the same SQL. See [data model and persistence](14-data-model-and-persistence.md).

## Source map

- `src/core/contracts/states.py`: statuses, transition tables, terminal sets
- `src/core/contracts/lifecycle.py`, `task_lifecycle.py`: `Run`, `Task`, `Attempt` records and validators
- `src/core/contracts/outcomes.py`: `RunEvent` envelope contract
- `src/core/contracts/streaming.py`: `RunEventCursor`, `delivery_idempotency_key`
- `src/api/services/direct_execution_service.py`: admission, transitions, parking, reconciliation, cancellation, rehydration, checkpoints
- `src/repositories/run_lifecycle_repository.py`, `run_config_snapshot_repository.py`, `run_event_repository.py`: persistence of runs, snapshots, events and outbox rows
- `src/models/db/run_lifecycle.py`, `run_config_snapshot.py`, `run_event.py`: the tables
- `src/api/services/outbox_relay.py`: outbox relay
- `src/api/services/event_publisher.py`: Redis publishers and subscriber
- `src/api/routes/run_stream.py`: SSE endpoint
- `src/api/routes/websocket.py`: WebSocket endpoints and health
- `src/api/websocket/run_stream.py`: stream reader, hub, entitlement
- `src/api/websocket/auth.py`: WebSocket authentication and project access check
- `src/api/websocket/connection_manager.py`: connections, heartbeats, broadcasts
- `src/models/websocket_messages.py`: message and subscription schemas
- `src/api/main.py`: `lifespan` wiring for rehydration and the relay
