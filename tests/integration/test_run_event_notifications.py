"""Run events published to Redis wake only the owning tenant's stream readers.

Redis carries a wakeup, never authority: the subscriber trusts nothing in the
envelope beyond the run id, and takes the organization from the durable run row.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.api.services.event_publisher import EventPublisher
from src.api.services.run_event_wakeup import RunEventWakeupSubscriber
from src.api.websocket.run_stream import RunStreamHub
from tests.integration.wave4_helpers import seed_run_task_attempt

pytestmark = [pytest.mark.integration]

ORG_A = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
ORG_B = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
RUN = "run-a"


@pytest_asyncio.fixture(name="factory")
async def factory_fixture(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await seed_run_task_attempt(session, organization_id=ORG_A, run_id=RUN)
        await session.commit()
    return factory


def _envelope(**overrides: Any) -> dict[str, Any]:
    return {
        "run_id": RUN,
        "event_type": "run.admitted",
        "payload": {},
        "event_id": "e-1",
        "sequence": 1,
        "idempotency_key": "k-1",
        **overrides,
    }


async def _woken(event: asyncio.Event, timeout: float = 0.5) -> bool:
    try:
        await asyncio.wait_for(event.wait(), timeout)
    except TimeoutError:
        return False
    return True


class _ForbiddenFactory:
    def __call__(self) -> Any:
        raise AssertionError("a malformed envelope must not reach the database")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"\xff\xfe",
        b"[]",
        b'"run-a"',
        b"{}",
        b'{"run_id": ""}',
        b'{"run_id": 7}',
        b'{"run_id": "run-a", "sequence": "1"}',
        b'{"run_id": "run-a", "sequence": true}',
        b'{"run_id": "run-a", "sequence": -1}',
    ],
)
async def test_a_malformed_envelope_wakes_nobody(raw: bytes) -> None:
    hub = RunStreamHub()
    subscriber = RunEventWakeupSubscriber(
        redis_client=None,  # type: ignore[arg-type]
        session_factory=_ForbiddenFactory(),  # type: ignore[arg-type]
        stream_hub=hub,
    )
    async with hub.subscribe(organization_id=ORG_A, run_id=RUN) as woken:
        assert await subscriber.handle_message(raw) is False
        assert not woken.is_set()


@pytest.mark.asyncio
async def test_the_organization_comes_from_the_run_not_the_envelope(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    hub = RunStreamHub()
    subscriber = RunEventWakeupSubscriber(
        redis_client=None,  # type: ignore[arg-type]
        session_factory=factory,
        stream_hub=hub,
    )
    import json

    forged = json.dumps(_envelope(organization_id=str(ORG_B), tenant_id=str(ORG_B)))

    async with (
        hub.subscribe(organization_id=ORG_A, run_id=RUN) as owner,
        hub.subscribe(organization_id=ORG_B, run_id=RUN) as stranger,
    ):
        assert await subscriber.handle_message(forged) is True
        assert owner.is_set()
        assert not stranger.is_set()


@pytest.mark.asyncio
async def test_an_unknown_run_wakes_nobody(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    hub = RunStreamHub()
    subscriber = RunEventWakeupSubscriber(
        redis_client=None,  # type: ignore[arg-type]
        session_factory=factory,
        stream_hub=hub,
    )
    import json

    async with hub.subscribe(organization_id=ORG_A, run_id="ghost") as woken:
        assert (
            await subscriber.handle_message(json.dumps(_envelope(run_id="ghost")))
            is False
        )
        assert not woken.is_set()


@pytest.mark.asyncio
async def test_a_published_run_event_wakes_the_owning_tenants_reader_through_redis(
    factory: async_sessionmaker[AsyncSession], redis_client: Any
) -> None:
    hub = RunStreamHub()
    subscriber = RunEventWakeupSubscriber(
        redis_client=redis_client, session_factory=factory, stream_hub=hub
    )
    publisher = EventPublisher()
    publisher.redis_client = redis_client

    subscriber.start()
    try:
        async with (
            hub.subscribe(organization_id=ORG_A, run_id=RUN) as owner,
            hub.subscribe(organization_id=ORG_B, run_id=RUN) as stranger,
        ):
            # The subscription is established asynchronously; publish until seen.
            for _ in range(20):
                await publisher.publish_run_event(
                    run_id=RUN,
                    event_type="run.admitted",
                    payload={"secret": "must never reach a stranger"},
                    event_id="e-1",
                    sequence=1,
                    idempotency_key="k-1",
                )
                if await _woken(owner, 0.25):
                    break
            assert owner.is_set()
            assert not stranger.is_set()
    finally:
        await subscriber.stop()


@pytest.mark.asyncio
async def test_a_malformed_message_does_not_stop_the_subscriber(
    factory: async_sessionmaker[AsyncSession], redis_client: Any
) -> None:
    hub = RunStreamHub()
    subscriber = RunEventWakeupSubscriber(
        redis_client=redis_client, session_factory=factory, stream_hub=hub
    )
    publisher = EventPublisher()
    publisher.redis_client = redis_client

    subscriber.start()
    try:
        async with hub.subscribe(organization_id=ORG_A, run_id=RUN) as owner:
            for _ in range(20):
                await redis_client.publish("research_platform:run_events", "garbage")
                await publisher.publish_run_event(
                    run_id=RUN,
                    event_type="run.admitted",
                    payload={},
                    event_id="e-1",
                    sequence=1,
                    idempotency_key="k-1",
                )
                if await _woken(owner, 0.25):
                    break
            assert owner.is_set()
    finally:
        await subscriber.stop()
