# Model invocation and journal contract

Every governed external model call passes through `invoke_model` in `src/services/model_invocation.py`. This page is the contract an owner (a research run, an evaluation job or a document job) satisfies to make governed calls. Only the research owner exists in production, and it supplies a ledger and a deadline but no journal (see [not wired](15-not-wired.md#model-call-journal-owner)).

## The context an owner supplies

`ModelInvocationContext` is a frozen dataclass. An owner puts it in `AgentTask.context` under `MODEL_INVOCATION_CONTEXT_KEY`; `ExecutionPlanTopologyExecutor` copies it to every worker task after folding in the plan deadline. A task with only a `BudgetLedger` under `BUDGET_LEDGER_CONTEXT_KEY` gets `ModelInvocationContext.for_ledger`. A task with neither makes ungoverned calls: typed result, no reservation, no journal row.

| Field | Meaning |
|---|---|
| `ledger` | The owner's `BudgetLedger`; in-process admission and the source of the run's `usage`. |
| `journal` | A `CallJournal` (`ModelCallReservationRepository` satisfies it); durable reserve, marker and settle. |
| `remaining_seconds` | Seconds left before the owner's deadline, or `None`. At zero or below, no call starts. Otherwise it bounds the provider request timeout. |
| `guard` | Raises when the owner is cancelled or lost its lease. Checked before the reservation and again before the dispatch marker. |
| `fencing_token` | Opaque; forwarded to every journal write so a stale owner cannot settle. |
| `attempt_ref` | Opaque owning attempt, trigger or worker reference recorded on the row. |
| `max_attempts` | Attempts per call; default 1. A retry follows only an `UnbilledTransportError`, and each attempt has its own reservation and row. |
| `trace_parent` | A Langfuse trace id, or `None`. |

A job-scoped owner (evaluation, document) builds its own context with its own ledger, deadline and `JournalOwner`; it never reuses a research run's deadline or ledger.

## Order of one attempt

1. `price` is required whenever a ledger or journal is present; an unpriced route raises `BudgetExceededError` before anything is reserved. A zero-price fixture route is a `CallPrice(fixture=True)`.
2. `guard()` and the deadline are checked. A cancelled or expired owner reserves and sends nothing.
3. The ledger reserves tokens and cost; `journal.reserve` commits the row (`dispatch_state=reserved`).
4. `guard()` and the deadline are checked again; `journal.mark_may_have_sent` commits the dispatch marker. A failure at 3 or 4 releases the hold (`release_unsent` after a committed reservation) and sends nothing.
5. The provider call runs, and a provider span opens when `trace_parent` is set.
6. The response is normalised. Only a complete, non-empty response is `CallStatus.COMPLETE`.
7. Settlement runs under `asyncio.shield`, so a cancellation cannot interrupt it:
   - reported usage with a cost: `settle_known` and the ledger records the reported amounts;
   - an error, a cancellation, or missing usage: `settle_unknown` and the ledger keeps the full reservation and fails closed on cost for every later call of the run;
   - `UnbilledTransportError`: zero charge, settled as known zero.

Journal write failures after the call (`SQLAlchemyError`, `OSError`) are logged and swallowed, because the provider has been paid; the open row is later settled unknown by reconciliation. Any other exception from the journal propagates.

## Journal states

`model_call_reservations` rows move `dispatch_state` `reserved` → `may_have_sent` and `settlement_state` `open` → exactly one of `settled_known`, `settled_unknown`, `released_unsent`. A second settlement of a closed row changes nothing and returns `False`.

`ModelCallReservationRepository` is constructed with a session factory, an organization id and a `JournalOwner` whose `kind` is `research_run`, `evaluation_job` or `document_job`. `JournalOwner.lock` must select the owner row `FOR UPDATE` within the organization and return its `OwnerBudget` (ceilings, fencing token, liveness), raising `OwnerLostError` when the owner is missing. The repository serializes reservations on that lock and rejects any write whose fencing token differs from the owner's.

`reconcile_owner` closes every open row of an interrupted owner: unmarked rows release as not sent, marked rows settle unknown and keep their reservation as an uncertain bound. It is idempotent. Uncertain spend is reported separately from known spend (`JournalTotals`) and is never recorded as provider-billed cost.

## Telemetry

With a `trace_parent` and a Langfuse client, each real attempt opens one `model_call` generation observation under that trace and ends it on every exit, including cancellation. The observation carries the call id, attempt number, estimated tokens, outcome, provider, model, finish reason, usage certainty, request id and, when reported, token usage and cost. It never carries prompts, response text or provider error text. A missing parent, a disabled client or a failing backend (an HTTP, OS, value, attribute or runtime error from the span call) yields no span and changes no result, usage or journal write; any other exception from a span call propagates. See [security, audit and observability](12-security-audit-observability.md#tracing).

## Source map

- `src/services/model_invocation.py`: the wrapper, `ModelInvocationContext`, `ModelCallResult`
- `src/repositories/model_call_reservation_repository.py`, `src/models/db/model_call_reservation.py`: the journal
- `src/ai_brain/integration/execution_plan_topology.py`: folds the plan deadline into each worker's context
- `src/core/tracing.py`: `start_model_attempt`, `end_model_attempt`
- Tests: `tests/test_provider_invocation_contract.py`, `tests/test_model_invocation_telemetry.py`, `tests/integration/test_model_call_crash_accounting.py`
