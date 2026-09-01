"""The durable record of a mediated call, and the order it is written in.

**Persist before publish, always.** Wave 3 established this and proved why with
a crash matrix: a subscriber that has seen an event the database never stored
is a subscriber holding a fact the system cannot reproduce, and no later replay
will ever contain it. So the boundary writes the invocation row and its audit
events in one transaction, commits, and only then publishes. A publish failure
after a successful commit costs a redelivery, which is recoverable. A commit
failure after a successful publish costs a phantom event, which is not.

**Correlation, which 4-Char found missing.** No public method on the existing
integration threads a run, task, or attempt identifier, so nothing could
correlate a tool call back to a durable record. Every field here carries all
three, and they are required rather than optional — an event that cannot be
traced to its run is an event nobody can act on.

**The interface, not the implementation.** Packet 4B is building the tables and
repositories concurrently. These protocols are what this boundary codes
against; the assumptions they encode are stated in the handoff so integration
can reconcile them rather than discover them.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, final, runtime_checkable

from pydantic import JsonValue

from src.core.contracts.capabilities import CapabilityDecision
from src.core.contracts.provenance import ToolInvocation

TOOL_INVOCATION_AGGREGATE: str = "tool_invocation"
"""``aggregate_type`` for every event this boundary emits."""

EVENT_TYPE_VERSION: str = "1.0"

EVENT_REQUESTED: str = "tool.invocation.requested"
EVENT_COMPLETED: str = "tool.invocation.completed"


@final
@dataclass(frozen=True, slots=True)
class ToolAuditEvent:
    """One append-only event about a tool invocation.

    Field names mirror ``RunEventRepository.append_event`` so 4B's adapter is a
    transcription rather than a translation. ``sequence`` is deliberately
    absent: Wave 3 allocates it transactionally inside the repository, and a
    caller-supplied sequence is how gaps and collisions get introduced.
    """

    event_id: str
    run_id: str
    task_id: str
    attempt_id: str
    aggregate_id: str
    event_type: str
    occurred_at: datetime
    producer: str
    deduplication_key: str
    payload: Mapping[str, JsonValue]
    aggregate_type: str = TOOL_INVOCATION_AGGREGATE
    event_type_version: str = EVENT_TYPE_VERSION
    destinations: tuple[str, ...] = ()
    correlation_id: str | None = None
    causation_event_id: str | None = None


@runtime_checkable
class ToolAuditStore(Protocol):
    """Durable storage for invocations and their events, in one transaction."""

    async def find_invocation(
        self,
        *,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
    ) -> ToolInvocation | None:
        """Return a previously recorded terminal invocation, if one exists.

        This is what makes a retried call idempotent: a second request bearing
        an idempotency key that already reached a terminal state returns that
        recorded outcome rather than executing the tool again.

        **``attempt_id`` is required, and its absence was a defect.** This
        lookup must be scoped exactly as the storage is:
        ``agent_tool_invocations`` is unique on ``(attempt_id,
        idempotency_key)``, and this signature was ``(run_id,
        organization_id, idempotency_key)`` — coarser than what it reads. A
        caller-supplied key from one attempt therefore replayed into the
        next, so a deliberate retry at the attempt level was answered with
        the previous attempt's result and the tool never ran.

        ``_derive_idempotency_key`` includes ``attempt_id`` for exactly that
        reason, but a caller-supplied key skips the derivation, leaving this
        lookup as the only place the rule could hold. It is also unsound in
        the other direction against a real store: two attempts legitimately
        hold two rows under one key, and a run-scoped query has two answers
        to a question that admits one.
        """
        ...

    async def persist(
        self,
        *,
        invocation: ToolInvocation,
        events: Sequence[ToolAuditEvent],
        organization_id: str | None,
        capability_decision: CapabilityDecision | None,
    ) -> None:
        """Write the invocation and append its events atomically, and commit.

        Committing is the store's responsibility rather than the boundary's,
        because Wave 3's repositories flush and let their caller own the
        transaction. Whoever adapts those repositories owns the commit; what
        the boundary requires is that this call does not return until the write
        is durable.

        ``capability_decision`` is here because without it no adapter to Wave
        3's ``ToolInvocationRepository`` can be written at all.
        ``create_tool_invocation`` sources five columns from it —
        ``capability_decision_effect``, ``capability_grant_id``,
        ``capability_approval_id``, ``capability_denial_reason``, and
        ``request_fingerprint`` — none of which is recoverable from the
        ``ToolInvocation``, by design. The boundary holds the value; it had no
        way to hand it over.

        **``None`` is no longer reachable from the boundary.** This paragraph
        used to argue the opposite — that input validation "runs before
        authorization, and must", because the capability request's fingerprint
        covers the digest of the validated input — and concluded that exactly
        one persisted status carries no decision. That was a description of a
        defect, and the argument does not hold: the digest can be taken of the
        redacted *raw* arguments, so the decision runs first and a rejected
        input is now recorded under the decision that admitted the caller.
        :class:`~src.core.tools.boundary.ToolBoundary` passes a decision on
        every path that persists.

        The parameter stays optional so that adapters written against the older
        contract still typecheck, and so an implementation that receives ``None``
        from somewhere else is not silently free to invent one. An adapter that
        wants to refuse ``None`` outright may now do so.

        **One call, two writes — the adapter must dispatch.** An *allowed*
        invocation reaches this method twice: once with
        ``status=REQUESTED`` and once with its terminal status, and both
        describe the **same row**. ``agent_tool_invocations`` is unique on
        ``(attempt_id, idempotency_key)`` for every row that is not a
        refusal — the index is partial, ``WHERE status <> 'denied'``, because
        an idempotency key reserves one unit of work and a denial is a
        decision about a call that never ran — and Wave 3's
        ``ToolInvocationRepository`` splits the two across
        ``create_tool_invocation`` and ``record_transition`` accordingly. This
        signature does not distinguish them, so the obvious adapter inserts
        twice and fails on the unique constraint.

        Stated here because the obvious adapter is the one somebody will write,
        and because the denial and input-rejection paths **hide** the problem —
        they terminate before the ``REQUESTED`` write and persist exactly once,
        so an adapter tested only against refusals looks correct.
        ``tests/integration/test_boundary_records_survive_postgres.py`` builds a
        dispatching adapter and is the worked example; that is where this was
        found.
        """
        ...


@runtime_checkable
class ToolAdmissionStore(Protocol):
    """Optional atomic admission for a first requested invocation.

    ``None`` means this caller reserved the request.  An existing pending or
    terminal invocation means another caller already owns the key, so the
    boundary can validate its identity and return ``IN_PROGRESS`` or replay
    the terminal result without dispatching a handler.  Stores that do not
    implement this seam keep the older lookup-then-persist behavior.

    The reservation itself must be atomic in the adapter.  In-memory
    implementations can provide that guarantee by doing the lookup and claim
    without an ``await`` point; durable implementations should use their
    datastore's uniqueness/transaction primitive.
    """

    async def reserve_invocation(
        self,
        *,
        invocation: ToolInvocation,
        organization_id: str | None,
    ) -> ToolInvocation | None:
        """Claim the invocation key or return the existing owner."""
        ...


@runtime_checkable
class PendingToolAuditStore(Protocol):
    """Optional lookup for work that was admitted but has not terminated.

    A terminal replay is an answer.  A nonterminal row is a reservation for
    work that may still be running, so callers must not execute the same
    idempotency key again.  This capability is optional to preserve the
    boundary's compatibility with older in-memory and test adapters; durable
    stores that can recover after a process restart implement it.
    """

    async def find_pending_invocation(
        self,
        *,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
    ) -> ToolInvocation | None:
        """Return an authorized request that has not reached a terminal state."""
        ...


@runtime_checkable
class LeaseAwareToolAuditStore(PendingToolAuditStore, Protocol):
    """Optional durable recovery and renewal operations for leased tool work.

    ``reconcile_pending_invocation`` atomically retrieves the exact durable
    invocation identified by ``run_id``, ``attempt_id``, ``organization_id``,
    and ``idempotency_key`` at the caller-injected aware ``now``. It returns
    ``None`` when no non-denied reservation exists. If the reservation is
    pending and its lease is live, it returns the pending
    :class:`ToolInvocation` with lease fields reconstructed. If the lease is
    expired, missing, or from a legacy row with ``NULL`` lease fields, it must
    transition that row to a replayable terminal failure, clear lease fields,
    leave output empty, append the terminal tool event, and commit the state
    transition and event in one transaction. A concurrent retry that observes
    a row already reconciled by another process returns that existing terminal
    row and must not append a duplicate event.

    ``renew_invocation_lease`` updates only the lease expiry for the exact
    durable invocation/run/attempt/tenant/idempotency tuple while the row is
    still pending, belongs to ``lease_owner_id``, and its current lease is
    unexpired at the injected aware ``now``. It returns ``False`` for a missing
    row, wrong owner, wrong tenant, terminal row, or expired/null lease, and it
    must never resurrect expired work.
    """

    async def reconcile_pending_invocation(
        self,
        *,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
        now: datetime,
    ) -> ToolInvocation | None:
        """Return live pending work or terminalize stale pending work."""
        ...

    async def renew_invocation_lease(
        self,
        *,
        tool_invocation_id: str,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
        lease_owner_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        """Extend an unexpired pending lease held by ``lease_owner_id``."""
        ...


@runtime_checkable
class ToolEventPublisher(Protocol):
    """Delivery of already-durable events to live subscribers."""

    async def publish(self, events: Sequence[ToolAuditEvent]) -> None: ...


@final
@dataclass(slots=True)
class NullEventPublisher:
    """A publisher for a deployment with no live subscribers.

    Named, like :class:`~src.core.tools.secrets.NullSecretProvider`, so that
    "nothing is listening" is a visible choice at the construction site.
    """

    published: list[ToolAuditEvent] = field(default_factory=list)

    async def publish(self, events: Sequence[ToolAuditEvent]) -> None:
        self.published.extend(events)


__all__ = [
    "EVENT_COMPLETED",
    "EVENT_REQUESTED",
    "EVENT_TYPE_VERSION",
    "TOOL_INVOCATION_AGGREGATE",
    "LeaseAwareToolAuditStore",
    "NullEventPublisher",
    "PendingToolAuditStore",
    "ToolAdmissionStore",
    "ToolAuditEvent",
    "ToolAuditStore",
    "ToolEventPublisher",
]
