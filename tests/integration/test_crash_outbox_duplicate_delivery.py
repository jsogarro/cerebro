"""Crash matrix: duplicate delivery and a relay crash mid-claim.

At-least-once delivery means a consumer may see the same event more than
once; ``src.core.contracts.delivery_idempotency_key`` exists so a consumer
can dedupe on a value that is stable across redeliveries. The first test
here proves that stability holds for a redelivery an operator (or a future
multi-instance/`SKIP LOCKED` relay) forces. The second covers the other
direction: a relay that dies between claiming a row and finalizing it leaves
that row ``in_flight``, and the claim is a lease — once it expires the row is
claimable again, by the same relay or a freshly restarted one. Redelivery is
the expected outcome there, not a defect; the defect would be the row never
being delivered again at all.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.api.services.outbox_relay import OutboxRelay
from src.models.db.run_event import AgentRunEventOutbox, EventDeliveryStatus
from tests.integration.crash_fixtures import seed_run_with_event

pytestmark = [pytest.mark.integration]


class _FakeEventPublisher:
    """Records ``publish_run_event`` calls; success is scripted per call."""

    def __init__(self, *, succeed: bool = True) -> None:
        self.succeed = succeed
        self.calls: list[dict[str, Any]] = []

    async def publish_run_event(
        self,
        *,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        occurred_at: str | None = None,
    ) -> bool:
        self.calls.append(
            {
                "run_id": run_id,
                "event_type": event_type,
                "payload": payload,
                "occurred_at": occurred_at,
            }
        )
        return self.succeed


@pytest.mark.asyncio
async def test_forced_redelivery_carries_a_stable_idempotency_key(
    test_engine: AsyncEngine,
) -> None:
    """A row redelivered after being reset to ``pending`` (an operator
    replaying a lost ack, or a future reclaim-by-timeout policy) keeps the
    exact same ``idempotency_key`` — the value a consumer must dedupe on —
    across both deliveries."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "outbox-forced-redelivery"
    async with session_factory() as session:
        event_id = await seed_run_with_event(session, run_id=run_id)

    publisher = _FakeEventPublisher(succeed=True)
    relay = OutboxRelay(session_factory=session_factory, event_publisher=publisher)

    delivered = await relay.run_once()
    assert delivered == 1

    async with session_factory() as session:
        row = (
            await session.execute(
                select(AgentRunEventOutbox).where(
                    AgentRunEventOutbox.event_id == event_id
                )
            )
        ).scalar_one()
        assert row.status == EventDeliveryStatus.DELIVERED.value
        first_idempotency_key = row.idempotency_key

    # Force a redelivery: an operator replaying a lost consumer ack, or a
    # future reclaim policy, resets the row back to claimable.
    async with session_factory() as session:
        await session.execute(
            update(AgentRunEventOutbox)
            .where(AgentRunEventOutbox.event_id == event_id)
            .values(
                status=EventDeliveryStatus.PENDING.value,
                available_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        await session.commit()

    redelivered = await relay.run_once()
    assert redelivered == 1

    async with session_factory() as session:
        row = (
            await session.execute(
                select(AgentRunEventOutbox).where(
                    AgentRunEventOutbox.event_id == event_id
                )
            )
        ).scalar_one()
        assert row.idempotency_key == first_idempotency_key

    assert len(publisher.calls) == 2
    # A consumer keyed on idempotency_key sees the identical key both times
    # and can safely treat the second call as a no-op replay of the first —
    # this is the entire mechanism "duplicate delivery is safe" rests on.


@pytest.mark.asyncio
async def test_relay_crash_between_claim_and_finalize_is_eventually_recovered(
    test_engine: AsyncEngine,
) -> None:
    """A claim abandoned by a dead relay is reclaimed once its lease expires,
    and not one moment before — the lease is a real bound, not a blanket
    'anything in_flight is up for grabs'."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "outbox-stuck-in-flight"
    async with session_factory() as session:
        event_id = await seed_run_with_event(session, run_id=run_id)

    publisher = _FakeEventPublisher(succeed=True)
    relay = OutboxRelay(session_factory=session_factory, event_publisher=publisher)

    # Simulate exactly the crash window: claim the batch (the only part of
    # run_once that commits before delivery is attempted), then the process
    # disappears before _deliver/_finalize ever runs.
    async with session_factory() as session:
        claimed = await relay._claim_batch(session)
        await session.commit()
    assert len(claimed) == 1
    assert claimed[0].status == EventDeliveryStatus.IN_FLIGHT.value
    assert publisher.calls == []

    # A relay whose lease has not yet expired must leave the row alone: a
    # live relay's in-flight rows are not other relays' to take, and the row
    # this one is holding is indistinguishable from that.
    unexpired_relay = OutboxRelay(
        session_factory=session_factory,
        event_publisher=publisher,
        claim_lease_seconds=3600,
    )
    assert await unexpired_relay.run_once() == 0
    assert publisher.calls == []

    # "Restart": a fresh relay instance, sharing only the database, polls
    # after the abandoned claim's lease has expired — the abandoned claim is
    # reclaimed and delivered rather than sitting in_flight forever.
    fresh_relay = OutboxRelay(
        session_factory=session_factory,
        event_publisher=publisher,
        claim_lease_seconds=0,
    )
    delivered = await fresh_relay.run_once()
    assert delivered == 1

    async with session_factory() as session:
        row = (
            await session.execute(
                select(AgentRunEventOutbox).where(
                    AgentRunEventOutbox.event_id == event_id
                )
            )
        ).scalar_one()
        assert row.status == EventDeliveryStatus.DELIVERED.value
        # Both the abandoned claim and the reclaim count as delivery attempts;
        # a reclaim continues the row's history rather than resetting it.
        assert row.attempts == 2
        assert row.claimed_by == fresh_relay.worker_id

    assert len(publisher.calls) == 1
    assert publisher.calls[0]["run_id"] == run_id
