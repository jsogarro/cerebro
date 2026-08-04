"""Crash matrix: duplicate delivery and a relay crash mid-claim.

At-least-once delivery means a consumer may see the same event more than
once; ``src.core.contracts.delivery_idempotency_key`` exists so a consumer
can dedupe on a value that is stable across redeliveries. The first test
here proves that stability holds for a redelivery an operator (or a future
multi-instance/`SKIP LOCKED` relay) forces. The second documents a real gap
found while probing the other direction: the relay's claim step
(``status -> in_flight``) has no matching reclaim path, so a crash between
claiming a row and finalizing it does not produce a duplicate delivery — it
produces no further delivery at all, silently, forever.
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
@pytest.mark.xfail(
    strict=True,
    reason=(
        "FINDING: OutboxRelay._claim_batch only selects rows with status "
        "'pending' or 'failed'; once a row is claimed it moves to "
        "'in_flight' and stays there unless _finalize runs. If the relay "
        "process crashes (or is killed) after committing the claim but "
        "before _deliver/_finalize completes, the row is not "
        "'duplicate-delivered' on the next poll — it is never delivered "
        "again by anything: no timeout-based reclaim exists for 'in_flight' "
        "rows, by this relay instance or a freshly restarted one. This is a "
        "silent hole (no error, no warning, no visible signal), the exact "
        "failure mode the wave's validation bar rules out for state "
        "transitions in general: 'a failure between persisting state and "
        "delivering an event leaves a recoverable, observable state.'"
    ),
)
async def test_relay_crash_between_claim_and_finalize_is_eventually_recovered(
    test_engine: AsyncEngine,
) -> None:
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "outbox-stuck-in-flight"
    async with session_factory() as session:
        await seed_run_with_event(session, run_id=run_id)

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

    # "Restart": a fresh relay instance, sharing only the database, polls
    # again — the correct behavior is that an abandoned claim is eventually
    # retried.
    fresh_relay = OutboxRelay(
        session_factory=session_factory, event_publisher=publisher
    )
    delivered = await fresh_relay.run_once()
    assert delivered == 1
