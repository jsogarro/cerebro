"""Crash matrix: Redis unavailable at delivery time.

``EventPublisher.initialize()`` already degrades to "local-only mode" when
Redis cannot be reached (catches the connection failure, leaves
``redis_client`` as ``None``); ``publish_run_event`` returns ``False`` when
``redis_client`` is ``None``. This file proves that degradation, exercised
against a real (deliberately unreachable) address rather than a mock, does
not lose the durable event it was asked to deliver — the outbox row stays
``failed``-with-backoff, never ``delivered`` and never dropped — and that
once Redis becomes reachable again, the same row is redelivered without any
special recovery step.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.api.services.event_publisher import EventPublisher
from src.api.services.outbox_relay import OutboxRelay
from src.models.db.run_event import AgentRunEventOutbox, EventDeliveryStatus
from tests.integration.conftest import IntegrationTestConfig
from tests.integration.crash_fixtures import seed_run_with_event

pytestmark = [pytest.mark.integration]

# Port 1 is privileged and unassigned in the test environment: a connection
# attempt gets an immediate refusal (no listener), never a hang — the same
# fail-fast shape a genuinely down Redis instance produces on a reachable
# host, without needing a firewall drop to simulate a black-holed one.
_UNREACHABLE_REDIS_URL = "redis://127.0.0.1:1/0"


@pytest.mark.asyncio
async def test_redis_down_degrades_delivery_without_losing_the_event(
    test_engine: AsyncEngine,
    redis_container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.api.services.event_publisher as event_publisher_module

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "redis-down-run"
    async with session_factory() as session:
        event_id = await seed_run_with_event(session, run_id=run_id)

    publisher = EventPublisher()
    monkeypatch.setattr(
        event_publisher_module.settings, "REDIS_URL", _UNREACHABLE_REDIS_URL
    )
    await publisher.initialize()
    assert publisher.redis_client is None  # degraded to local-only, as designed

    relay = OutboxRelay(session_factory=session_factory, event_publisher=publisher)
    delivered = await relay.run_once()

    assert delivered == 0
    async with session_factory() as session:
        row = (
            await session.execute(
                select(AgentRunEventOutbox).where(
                    AgentRunEventOutbox.event_id == event_id
                )
            )
        ).scalar_one()
        assert row.status == EventDeliveryStatus.FAILED.value
        assert row.attempts == 1
        assert row.last_error is not None
        # The event itself is untouched — this is a delivery-layer failure,
        # not a data-layer one.

    # Redis "comes back": point the same publisher at the real testcontainer
    # and re-initialize, exactly like a production process would after an
    # outage window (nothing about this path is test-only).
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    real_url = f"redis://{host}:{port}/{IntegrationTestConfig.TEST_REDIS_DB}"
    monkeypatch.setattr(event_publisher_module.settings, "REDIS_URL", real_url)
    await publisher.initialize()
    assert publisher.redis_client is not None

    # Force the backoff window open without a real sleep — this test proves
    # the row is redeliverable, not that the specific backoff duration is
    # correct (that is `test_outbox_relay.py`'s job).
    async with session_factory() as session:
        await session.execute(
            update(AgentRunEventOutbox)
            .where(AgentRunEventOutbox.event_id == event_id)
            .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
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
        assert row.status == EventDeliveryStatus.DELIVERED.value
        assert row.attempts == 2

    await publisher.shutdown()


@pytest.mark.asyncio
async def test_redis_down_across_multiple_events_loses_none_of_them(
    test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sustained Redis outage across several distinct runs/events must
    accumulate every one of them as retryable, not drop any silently."""

    import src.api.services.event_publisher as event_publisher_module

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    event_ids = []
    async with session_factory() as session:
        for i in range(5):
            event_ids.append(
                await seed_run_with_event(session, run_id=f"redis-down-multi-{i}")
            )

    publisher = EventPublisher()
    monkeypatch.setattr(
        event_publisher_module.settings, "REDIS_URL", _UNREACHABLE_REDIS_URL
    )
    await publisher.initialize()
    assert publisher.redis_client is None

    relay = OutboxRelay(session_factory=session_factory, event_publisher=publisher)
    delivered = await relay.run_once()
    assert delivered == 0

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(AgentRunEventOutbox).where(
                        AgentRunEventOutbox.event_id.in_(event_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 5
        assert all(row.status == EventDeliveryStatus.FAILED.value for row in rows)
        assert all(row.attempts == 1 for row in rows)
        # Every failed row is durably present and none is marked delivered —
        # the outage produced retries, not loss.

    await publisher.shutdown()
