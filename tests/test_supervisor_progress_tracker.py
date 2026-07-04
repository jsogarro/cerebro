"""Characterization tests for supervisor progress tracking extraction."""

from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocket

from src.api.services.supervisor_progress_tracker import SupervisorProgressTracker
from src.models.supervisor_api_models import SupervisorWebSocketEvent


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
    websocket = AsyncMock(spec=WebSocket)
    await tracker.connect(websocket, "client-1")

    await tracker.broadcast_event({"event_type": "test"})

    websocket.send_json.assert_awaited_once_with({"event_type": "test"})


@pytest.mark.asyncio
async def test_progress_tracker_sends_supervisor_event_dump() -> None:
    tracker = SupervisorProgressTracker()
    websocket = AsyncMock(spec=WebSocket)
    tracker.subscribe_supervisor("research", websocket)
    event = SupervisorWebSocketEvent(
        event_type="task_completed",
        supervisor_type="research",
        data={"execution_id": "exec-1"},
    )

    await tracker.send_supervisor_event("research", event)

    websocket.send_json.assert_awaited_once_with(event.model_dump())


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
