"""Focused restart-safety tests for the compatibility event publisher."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.api.services.event_publisher import EventPublisher


@pytest.mark.asyncio
async def test_event_publisher_supports_two_sequential_lifecycles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_publish = AsyncMock()
    first_subscribe = AsyncMock()
    second_publish = AsyncMock()
    second_subscribe = AsyncMock()
    clients = iter((first_publish, first_subscribe, second_publish, second_subscribe))
    monkeypatch.setattr(
        "src.api.services.event_publisher.redis.from_url",
        lambda *_args, **_kwargs: next(clients),
    )
    publisher = EventPublisher()
    monkeypatch.setattr(publisher, "_redis_subscriber", AsyncMock())

    await publisher.initialize()
    await publisher.shutdown()
    await publisher.initialize()
    await publisher.shutdown()

    for client in (
        first_publish,
        first_subscribe,
        second_publish,
        second_subscribe,
    ):
        client.ping.assert_awaited_once()
        client.close.assert_awaited_once()
    assert publisher.subscription_task is None
    assert publisher.redis_client is None
    assert publisher.redis_subscriber is None
