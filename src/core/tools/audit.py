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
        self, *, run_id: str, organization_id: str | None, idempotency_key: str
    ) -> ToolInvocation | None:
        """Return a previously recorded terminal invocation, if one exists.

        This is what makes a retried call idempotent: a second request bearing
        an idempotency key that already reached a terminal state returns that
        recorded outcome rather than executing the tool again.
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

        **``None`` is a real case, not a defensive default.** Input validation
        runs before authorization, and must: the capability request's
        fingerprint covers the digest of the *validated* input, so there is
        nothing to decide about until the arguments parse. The boundary still
        records that rejection, deliberately. Exactly one persisted status
        therefore carries no decision, and an adapter has to answer for it
        rather than assume it away — see the packet handoff for why the DDL
        cannot currently hold such a row.
        """
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
    "NullEventPublisher",
    "ToolAuditEvent",
    "ToolAuditStore",
    "ToolEventPublisher",
]
