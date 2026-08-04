"""Unit coverage for ``EventPublisher.publish_run_event`` (Wave 3 Packet 3C).

This is the outbox relay's only delivery call — see
``src/api/services/outbox_relay.py`` for why it is deliberately Redis-only
rather than an unscoped WebSocket broadcast (no project-scoped routing
exists yet for an arbitrary persisted run event).
"""

import json
from unittest.mock import AsyncMock

import pytest

from src.api.services.event_publisher import EventPublisher


@pytest.mark.asyncio
async def test_publish_run_event_returns_false_without_redis() -> None:
    publisher = EventPublisher()
    assert publisher.redis_client is None

    delivered = await publisher.publish_run_event(
        run_id="run-1", event_type="run.started", payload={"phase": "x"}
    )

    assert delivered is False


@pytest.mark.asyncio
async def test_publish_run_event_publishes_envelope_to_redis_channel() -> None:
    publisher = EventPublisher()
    publisher.redis_client = AsyncMock()

    delivered = await publisher.publish_run_event(
        run_id="run-1",
        event_type="run.succeeded",
        payload={"workers_used": 2},
        occurred_at="2026-08-04T00:00:00+00:00",
    )

    assert delivered is True
    publisher.redis_client.publish.assert_awaited_once()
    channel, body = publisher.redis_client.publish.call_args.args
    assert channel == "research_platform:run_events"
    decoded = json.loads(body)
    assert decoded == {
        "run_id": "run-1",
        "event_type": "run.succeeded",
        "payload": {"workers_used": 2},
        "occurred_at": "2026-08-04T00:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_publish_run_event_returns_false_when_redis_raises() -> None:
    publisher = EventPublisher()
    publisher.redis_client = AsyncMock()
    publisher.redis_client.publish.side_effect = RuntimeError("connection reset")

    delivered = await publisher.publish_run_event(
        run_id="run-1", event_type="run.started", payload={}
    )

    assert delivered is False
