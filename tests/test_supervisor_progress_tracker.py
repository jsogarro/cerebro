"""Characterization tests for supervisor progress tracking extraction."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocket

from src.api.services.supervisor_progress_tracker import SupervisorProgressTracker
from src.models.supervisor_api_models import SupervisorWebSocketEvent


def _socket(organization_id: str | None = "org-a") -> AsyncMock:
    websocket = AsyncMock(spec=WebSocket)
    websocket.state = SimpleNamespace(
        websocket_principal=SimpleNamespace(organization_id=organization_id)
    )
    return websocket


@pytest.mark.asyncio
async def test_progress_tracker_connection_lifecycle() -> None:
    tracker = SupervisorProgressTracker()
    websocket = AsyncMock(spec=WebSocket)

    await tracker.connect(websocket, "client-1")
    tracker.subscribe_supervisor("research", websocket)

    assert tracker.active_connections["client-1"] == [websocket]
    assert tracker.supervisor_subscriptions["research"] == [websocket]
    websocket.accept.assert_awaited_once()

    tracker.unsubscribe_supervisor("research", websocket)
    tracker.disconnect(websocket, "client-1")

    assert tracker.active_connections == {}
    assert tracker.supervisor_subscriptions["research"] == []


@pytest.mark.asyncio
async def test_progress_tracker_broadcasts_events() -> None:
    tracker = SupervisorProgressTracker()
    own, foreign = _socket("org-a"), _socket("org-b")
    await tracker.connect(own, "client-1")
    await tracker.connect(foreign, "client-2")

    await tracker.broadcast_event({"event_type": "test"}, "org-a")

    own.send_json.assert_awaited_once_with({"event_type": "test"})
    foreign.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_progress_tracker_sends_supervisor_event_dump() -> None:
    tracker = SupervisorProgressTracker()
    own, foreign, unscoped = _socket("org-a"), _socket("org-b"), _socket(None)
    for client_id, websocket in (("a", own), ("b", foreign), ("c", unscoped)):
        await tracker.connect(websocket, client_id)
        tracker.subscribe_supervisor("research", websocket)
    event = SupervisorWebSocketEvent(
        event_type="task_completed",
        supervisor_type="research",
        data={"execution_id": "exec-1"},
    )

    await tracker.send_supervisor_event("research", event, "org-a")

    own.send_json.assert_awaited_once_with(event.model_dump())
    foreign.send_json.assert_not_awaited()
    unscoped.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_progress_tracker_iterates_legacy_coordination_schema() -> None:
    tracker = SupervisorProgressTracker()

    events = [
        event
        async for event in tracker.iter_coordination_progress_events(
            "coord-1", delay_seconds=0
        )
    ]

    assert [event.progress_percentage for event in events] == [
        10.0,
        30.0,
        50.0,
        70.0,
        90.0,
        100.0,
    ]
    assert events[0].model_dump() == {
        "coordination_id": "coord-1",
        "event_type": "progress",
        "progress_percentage": 10.0,
        "current_phase": "Phase 1",
        "workers_active": 5,
        "estimated_remaining_seconds": 9,
        "details": None,
    }


@pytest.mark.asyncio
async def test_progress_tracker_drops_events_without_an_organization() -> None:
    """An unattributed event reaches nobody, not even claimless connections."""
    tracker = SupervisorProgressTracker()
    unscoped = _socket(None)
    await tracker.connect(unscoped, "client-1")

    await tracker.broadcast_event({"event_type": "test"}, None)

    unscoped.send_json.assert_not_awaited()
