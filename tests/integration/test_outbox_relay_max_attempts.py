"""``OutboxRelay.max_attempts`` is a real ceiling, not a dead constructor arg.

Before this test existed, ``max_attempts`` was accepted, stored, and never
read anywhere: a permanently undeliverable row (bad destination, a consumer
that will never come back, whatever) retried forever at the
``_MAX_BACKOFF_SECONDS`` backoff ceiling, silently, with nothing to observe
and nothing to page an operator. This proves the bound is enforced and that
the terminal state it produces is observable through the schema's existing
columns (no new status value, no new column — the table is frozen).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.api.services.outbox_relay import OutboxRelay
from src.core.contracts import Run, RunStatus
from src.models.db.run_event import AgentRunEventOutbox, EventDeliveryStatus
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository

pytestmark = [pytest.mark.integration]

ORG_ID = "00000000-0000-0000-0000-0000000000ff"
NOW = datetime(2026, 8, 4, tzinfo=UTC)


class _AlwaysFailsPublisher:
    """A destination that will never, ever come back."""

    def __init__(self) -> None:
        self.call_count = 0

    async def publish_run_event(self, **_kwargs: Any) -> bool:
        self.call_count += 1
        return False


async def _seed_run_with_event(session: AsyncSession, *, run_id: str) -> str:
    run = Run(
        run_id=run_id,
        tenant_id=ORG_ID,
        workflow_definition_id="workflow-1",
        workflow_definition_version="1",
        routing_policy_id="policy-1",
        routing_policy_version="1",
        idempotency_key=f"key-{run_id}",
        requested_by="user-1",
        status=RunStatus.CREATED,
        created_at=NOW,
        updated_at=NOW,
    )
    await RunLifecycleRepository(session).create_run(run, organization_id=ORG_ID)
    event_id = f"{run_id}-event"
    await RunEventRepository(session).append_event(
        run_id=run_id,
        organization_id=ORG_ID,
        event_id=event_id,
        aggregate_type="run",
        aggregate_id=run_id,
        event_type="run.admitted",
        event_type_version="1",
        occurred_at=NOW,
        producer="test",
        deduplication_key=event_id,
        payload={"note": "seeded"},
        destinations=("redis",),
    )
    await session.commit()
    return event_id


async def _force_available_now(session_factory: sessionmaker, event_id: str) -> None:
    """Skip the backoff wait without sleeping, exactly like an operator
    forcing an early retry or a test proving redelivery elsewhere in this
    suite (see ``test_crash_outbox_duplicate_delivery.py``)."""
    async with session_factory() as session:
        await session.execute(
            update(AgentRunEventOutbox)
            .where(AgentRunEventOutbox.event_id == event_id)
            .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()


async def _load_row(
    session_factory: sessionmaker, event_id: str
) -> AgentRunEventOutbox:
    async with session_factory() as session:
        return (
            await session.execute(
                select(AgentRunEventOutbox).where(
                    AgentRunEventOutbox.event_id == event_id
                )
            )
        ).scalar_one()


@pytest.mark.asyncio
async def test_a_permanently_failing_row_stops_retrying_after_max_attempts(
    test_engine: AsyncEngine,
) -> None:
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "outbox-dead-letter"
    async with session_factory() as session:
        event_id = await _seed_run_with_event(session, run_id=run_id)

    publisher = _AlwaysFailsPublisher()
    relay = OutboxRelay(
        session_factory=session_factory, event_publisher=publisher, max_attempts=2
    )

    # Attempt 1: fails, backed off, still under the ceiling.
    assert await relay.run_once() == 0
    row = await _load_row(session_factory, event_id)
    assert row.attempts == 1
    assert row.status == EventDeliveryStatus.FAILED.value

    # Attempt 2: fails, reaches max_attempts — this is the last attempt the
    # ceiling permits, so it must dead-letter rather than schedule another
    # backoff.
    await _force_available_now(session_factory, event_id)
    assert await relay.run_once() == 0
    row = await _load_row(session_factory, event_id)
    assert row.attempts == 2
    # Still an ordinary, already-permitted status value — no schema change.
    assert row.status == EventDeliveryStatus.FAILED.value
    assert row.last_error is not None
    assert "dead-letter" in row.last_error.lower()
    assert "2" in row.last_error  # names the attempt count it gave up at
    dead_lettered_error = row.last_error

    # A third round, even after forcing the backoff window open again, must
    # not reclaim the row at all: max_attempts is a hard ceiling, not a
    # bigger backoff. Before the fix, this call would have delivered a third
    # (failing) attempt and incremented attempts to 3.
    await _force_available_now(session_factory, event_id)
    assert await relay.run_once() == 0
    assert publisher.call_count == 2  # not 3 — the row was never reclaimed

    row = await _load_row(session_factory, event_id)
    assert row.attempts == 2  # unchanged
    assert row.last_error == dead_lettered_error  # unchanged: never re-finalized


async def _strand_row_in_flight_at_ceiling(
    session_factory: sessionmaker, event_id: str, *, attempts: int
) -> None:
    """Simulate a relay that claimed the attempt reaching ``max_attempts``
    and died before ``_finalize`` recorded any outcome.

    ``_claim_batch`` increments ``attempts`` as part of the same UPDATE that
    sets ``status=in_flight``; a relay that crashes between that claim and
    ``_finalize`` leaves exactly this row shape behind: ``in_flight``,
    ``attempts`` already at the ceiling, and a long-expired lease.
    """
    async with session_factory() as session:
        await session.execute(
            update(AgentRunEventOutbox)
            .where(AgentRunEventOutbox.event_id == event_id)
            .values(
                status=EventDeliveryStatus.IN_FLIGHT.value,
                attempts=attempts,
                claimed_at=datetime.now(UTC) - timedelta(hours=1),
                claimed_by="dead-relay",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_a_row_abandoned_in_flight_at_the_ceiling_is_dead_lettered(
    test_engine: AsyncEngine,
) -> None:
    """Reproduces N1: a relay claims the attempt that reaches
    ``max_attempts`` and dies before ``_finalize`` runs. ``_claim_batch``'s
    ``attempts < max_attempts`` bound then excludes the row from every future
    claim — including the lease-expiry reclaim path — so without a fix it is
    never delivered, never retried, and never dead-lettered: stranded
    ``in_flight`` forever with nothing to page an operator.
    """
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "outbox-stranded-at-ceiling"
    async with session_factory() as session:
        event_id = await _seed_run_with_event(session, run_id=run_id)

    await _strand_row_in_flight_at_ceiling(session_factory, event_id, attempts=2)

    publisher = _AlwaysFailsPublisher()
    relay = OutboxRelay(
        session_factory=session_factory, event_publisher=publisher, max_attempts=2
    )

    assert await relay.run_once() == 0
    # The row must never be handed to the publisher again: its attempt
    # budget is already spent, so redelivering it would exceed max_attempts.
    assert publisher.call_count == 0

    row = await _load_row(session_factory, event_id)
    assert row.status == EventDeliveryStatus.FAILED.value
    assert row.attempts == 2  # unchanged — no further attempt was made
    assert row.last_error is not None
    assert "dead-letter" in row.last_error.lower()
    assert "2" in row.last_error


@pytest.mark.asyncio
async def test_a_row_that_succeeds_before_the_ceiling_is_unaffected(
    test_engine: AsyncEngine,
) -> None:
    """The ceiling must not fire early: a row that eventually succeeds within
    its budget is delivered normally."""
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "outbox-recovers-in-time"
    async with session_factory() as session:
        event_id = await _seed_run_with_event(session, run_id=run_id)

    class _FailsOnceThenSucceeds:
        def __init__(self) -> None:
            self.calls = 0

        async def publish_run_event(self, **_kwargs: Any) -> bool:
            self.calls += 1
            return self.calls > 1

    publisher = _FailsOnceThenSucceeds()
    relay = OutboxRelay(
        session_factory=session_factory, event_publisher=publisher, max_attempts=2
    )

    assert await relay.run_once() == 0
    await _force_available_now(session_factory, event_id)
    assert await relay.run_once() == 1

    row = await _load_row(session_factory, event_id)
    assert row.status == EventDeliveryStatus.DELIVERED.value
    assert row.attempts == 2
