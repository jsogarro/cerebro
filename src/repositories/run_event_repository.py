"""Repository for the append-only run event stream and its delivery outbox.

``append_event`` is the only write path onto ``agent_run_events``: it
allocates the next per-run sequence transactionally (an ``UPDATE ...
RETURNING`` against ``agent_runs.last_event_sequence``, which Postgres
serializes per row so concurrent appends to the same run never collide or
leave a gap), validates the result through the ``RunEvent`` contract, and
inserts one outbox row per requested destination in the same flush. Nothing
here commits — the event row and its outbox rows land in the caller's
transaction, so a failure on either one rolls both back together and an
outbox row can never exist for an event that didn't.
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import RunEvent, RunEventCursor, delivery_idempotency_key
from src.models.db.run_event import AgentRunEvent, AgentRunEventOutbox
from src.models.db.run_lifecycle import AgentRun
from src.repositories.tenant_scope import (
    TenantMismatchError,
    get_run_organization_id,
    normalize_organization_id,
)

OrganizationId = uuid.UUID | str | None


class RunEventRepository:
    """Durable persistence for ``agent_run_events`` and
    ``agent_run_event_outbox``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append_event(
        self,
        *,
        run_id: str,
        organization_id: OrganizationId,
        event_id: str,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        event_type_version: str,
        occurred_at: datetime,
        producer: str,
        deduplication_key: str,
        payload: Mapping[str, Any],
        correlation_id: str | None = None,
        causation_event_id: str | None = None,
        destinations: Sequence[str] = (),
    ) -> AgentRunEvent:
        """Append one immutable event and its outbox deliveries atomically.

        Args:
            run_id: The run this event belongs to.
            organization_id: The authenticated tenant boundary; must match
                the run's stored organization.
            destinations: Delivery targets (e.g. ``"websocket"``, ``"sse"``).
                One outbox row is inserted per destination, in the same
                transaction as the event.
            (remaining args mirror ``RunEvent`` contract fields, minus
            ``sequence`` — the sequence is allocated here, not supplied.)

        Returns:
            The persisted event row, with its allocated sequence.

        Raises:
            MissingOrganizationContextError: No org context was supplied.
            TenantMismatchError: The org context disagrees with the run's
                stored organization.
            ValueError: The run does not exist.
        """
        normalized_organization_id = normalize_organization_id(organization_id)
        run_organization_id = await get_run_organization_id(self.session, run_id)
        if run_organization_id is None:
            raise ValueError(f"run {run_id!r} does not exist")
        if run_organization_id != normalized_organization_id:
            raise TenantMismatchError(
                f"run {run_id!r} does not belong to organization "
                f"{normalized_organization_id}"
            )

        sequence = await self._allocate_sequence(run_id)

        event = RunEvent(
            event_id=event_id,
            run_id=run_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            sequence=sequence,
            event_type=event_type,
            event_type_version=event_type_version,
            occurred_at=occurred_at,
            producer=producer,
            deduplication_key=deduplication_key,
            correlation_id=correlation_id,
            causation_event_id=causation_event_id,
            payload=dict(payload),
        )

        row = AgentRunEvent(
            event_id=event.event_id,
            run_id=event.run_id,
            organization_id=normalized_organization_id,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            sequence=event.sequence,
            event_type=event.event_type,
            event_type_version=event.event_type_version,
            occurred_at=event.occurred_at,
            producer=event.producer,
            deduplication_key=event.deduplication_key,
            correlation_id=event.correlation_id,
            causation_event_id=event.causation_event_id,
            payload=dict(event.payload),
        )
        self.session.add(row)

        envelope = event.model_dump(mode="json")
        for destination in destinations:
            outbox_row = AgentRunEventOutbox(
                event_id=event.event_id,
                run_id=event.run_id,
                organization_id=normalized_organization_id,
                destination=destination,
                idempotency_key=delivery_idempotency_key(
                    deduplication_key=event.deduplication_key,
                    destination=destination,
                ),
                payload=envelope,
            )
            self.session.add(outbox_row)

        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def read_events_after(
        self,
        run_id: str,
        *,
        after_sequence: int,
        limit: int = 100,
        organization_id: OrganizationId = None,
    ) -> list[AgentRunEvent]:
        """Return events after ``after_sequence``, in sequence order.

        This is the replay primitive a stream client resumes from: pass the
        last sequence it processed (0 for "from the start") and get back
        everything after it, oldest first.
        """
        query = (
            select(AgentRunEvent)
            .where(
                AgentRunEvent.run_id == run_id,
                AgentRunEvent.sequence > after_sequence,
            )
            .order_by(AgentRunEvent.sequence)
            .limit(limit)
        )
        if organization_id is not None:
            query = query.where(
                AgentRunEvent.organization_id
                == normalize_organization_id(organization_id)
            )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def read_from_cursor(
        self,
        cursor: RunEventCursor,
        *,
        limit: int = 100,
        organization_id: OrganizationId = None,
    ) -> list[AgentRunEvent]:
        """Replay events after a client's resumable cursor position."""
        return await self.read_events_after(
            cursor.run_id,
            after_sequence=cursor.last_sequence,
            limit=limit,
            organization_id=organization_id,
        )

    async def _allocate_sequence(self, run_id: str) -> int:
        """Atomically claim the next per-run event sequence.

        The ``UPDATE ... RETURNING`` takes a row lock on the run for the
        duration of the caller's transaction, so concurrent appends to the
        same run serialize on this statement instead of racing: each caller
        gets a distinct, gap-free, monotonically increasing sequence.
        """
        statement = (
            update(AgentRun)
            .where(AgentRun.run_id == run_id)
            .values(last_event_sequence=AgentRun.last_event_sequence + 1)
            .returning(AgentRun.last_event_sequence)
        )
        result = await self.session.execute(statement)
        row = result.first()
        if row is None:
            raise ValueError(f"run {run_id!r} does not exist")
        return int(row[0])


__all__ = ["RunEventRepository"]
