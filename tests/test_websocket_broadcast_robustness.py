"""Regression tests: duplicate Redis delivery and mid-broadcast disconnects."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.services.event_publisher import EventPublisher
from src.api.websocket.connection_manager import ConnectionManager
from src.models.websocket_messages import WSMessage


class _FakePubSub:
    def __init__(self, queue: asyncio.Queue[bytes]) -> None:
        self.queue = queue

    async def subscribe(self, *_a: object) -> None: ...
    async def unsubscribe(self, *_a: object) -> None: ...
    async def close(self) -> None: ...

    async def get_message(self, timeout: float = 1.0) -> dict[str, object] | None:
        try:
            data = await asyncio.wait_for(self.queue.get(), timeout=0.05)
        except TimeoutError:
            return None
        return {"type": "message", "data": data}


class _FakeRedis:
    def __init__(self, queue: asyncio.Queue[bytes]) -> None:
        self.queue = queue

    async def publish(self, _channel: str, payload: str) -> None:
        await self.queue.put(payload.encode())

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self.queue)


@pytest.mark.asyncio
async def test_publisher_does_not_rebroadcast_its_own_redis_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broadcast = AsyncMock()
    monkeypatch.setattr(
        "src.api.services.event_publisher.websocket_manager.broadcast_to_all",
        broadcast,
    )
    queue: asyncio.Queue[bytes] = asyncio.Queue()
    publisher = EventPublisher()
    publisher.redis_client = _FakeRedis(queue)  # type: ignore[assignment]
    publisher.redis_subscriber = _FakeRedis(queue)  # type: ignore[assignment]

    task = asyncio.create_task(publisher._redis_subscriber())
    await publisher._publish_event(WSMessage(type="info"))
    await asyncio.sleep(0.3)
    publisher._shutdown = True
    await task

    assert broadcast.await_count == 1


@pytest.mark.asyncio
async def test_publisher_delivers_message_from_another_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broadcast = AsyncMock()
    monkeypatch.setattr(
        "src.api.services.event_publisher.websocket_manager.broadcast_to_all",
        broadcast,
    )
    queue: asyncio.Queue[bytes] = asyncio.Queue()
    sender = EventPublisher()
    sender.redis_client = _FakeRedis(queue)  # type: ignore[assignment]
    receiver = EventPublisher()
    receiver.redis_subscriber = _FakeRedis(queue)  # type: ignore[assignment]

    task = asyncio.create_task(receiver._redis_subscriber())
    await sender._publish_event(WSMessage(type="info"))
    await asyncio.sleep(0.3)
    receiver._shutdown = True
    await task

    # one local delivery on the sender, one from Redis on the receiver
    assert broadcast.await_count == 2


def _stub(manager: ConnectionManager, cid: str, on_send=None) -> MagicMock:
    conn = MagicMock()

    async def send_message(_m: WSMessage) -> bool:
        if on_send:
            on_send()
        return True

    conn.send_message = AsyncMock(side_effect=send_message)
    conn.send_heartbeat = AsyncMock(return_value=True)
    conn.is_healthy = MagicMock(return_value=True)
    manager.connections[cid] = conn
    return conn


@pytest.mark.asyncio
async def test_broadcast_to_all_survives_disconnect_mid_broadcast() -> None:
    manager = ConnectionManager()
    _stub(manager, "A", on_send=lambda: manager.connections.pop("B", None))
    _stub(manager, "B")
    c = _stub(manager, "C")

    await manager.broadcast_to_all(WSMessage(type="info"))

    c.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_heartbeat_monitor_tolerates_dict_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ConnectionManager()
    a = _stub(manager, "A")
    a.send_heartbeat = AsyncMock(
        side_effect=lambda: manager.connections.__setitem__("D", MagicMock())
    )
    c = _stub(manager, "C")

    async def stop(_s: float) -> None:
        manager._shutdown = True

    monkeypatch.setattr("src.api.websocket.connection_manager.asyncio.sleep", stop)
    await manager._heartbeat_monitor()

    c.send_heartbeat.assert_awaited_once()
