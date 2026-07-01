"""Regression tests for progress-update broadcasting during execution.

`DirectExecutionService._publish_progress_update` used to call
`EventPublisher.publish_project_event`, a logs-only compatibility shim that
does NOT fan out over WebSocket. As a result no PROGRESS event ever reached a
subscribed `/ws` client while a query was running, even though the WebSocket
auth/subscription plumbing was working.

These tests assert the service uses the typed, actually-broadcasting
`publish_progress_update` path with a valid `UUID` project id and a
`ProgressUpdate` payload. They fail on the pre-fix code (which called
`publish_project_event` and never `publish_progress_update`).
"""

import uuid
from unittest.mock import AsyncMock

import pytest

from src.api.services.direct_execution_service import (
    DirectExecutionService,
    ExecutionStatus,
)
from src.models.websocket_messages import ProgressUpdate


@pytest.fixture
def service() -> DirectExecutionService:
    svc = DirectExecutionService.__new__(DirectExecutionService)
    svc.event_publisher = AsyncMock()
    return svc


async def test_progress_update_is_broadcast_not_logged_only(
    service: DirectExecutionService,
) -> None:
    project_id = str(uuid.uuid4())
    status = ExecutionStatus(
        execution_id="exec-1",
        project_id=project_id,
        status="running",
        progress_percentage=40.0,
        current_phase="hierarchical_coordination",
        supervisor_type="research",
    )

    await service._publish_progress_update(status)

    # Must use the broadcasting path, not the logs-only shim.
    service.event_publisher.publish_progress_update.assert_awaited_once()
    service.event_publisher.publish_project_event.assert_not_called()

    call = service.event_publisher.publish_progress_update.await_args
    sent_project_id, sent_progress = call.args[0], call.args[1]
    assert sent_project_id == uuid.UUID(project_id)
    assert isinstance(sent_progress, ProgressUpdate)
    assert sent_progress.progress_percentage == 40.0
    assert sent_progress.current_phase == "hierarchical_coordination"


async def test_non_uuid_project_id_is_skipped_without_crashing(
    service: DirectExecutionService,
) -> None:
    status = ExecutionStatus(
        execution_id="exec-2",
        project_id="not-a-uuid",
        status="running",
    )

    # Should not raise, and should not attempt to broadcast an unroutable event.
    await service._publish_progress_update(status)
    service.event_publisher.publish_progress_update.assert_not_awaited()


async def test_no_publisher_is_a_noop() -> None:
    svc = DirectExecutionService.__new__(DirectExecutionService)
    svc.event_publisher = None
    status = ExecutionStatus(
        execution_id="exec-3",
        project_id=str(uuid.uuid4()),
        status="running",
    )
    # Must not raise when the publisher is unavailable.
    await svc._publish_progress_update(status)


class _CaptureWebSocket:
    """Minimal stand-in for a Starlette WebSocket that records sent frames."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def accept(self) -> None:  # pragma: no cover - not exercised
        pass

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


async def test_progress_update_reaches_a_subscribed_ws_client_end_to_end() -> None:
    """Full chain: _publish_progress_update -> EventPublisher -> connection_manager
    -> a subscribed connection actually receives a PROGRESS frame.

    This exercises the real broadcast infrastructure (no mocks on the publisher
    or the connection manager), which is what makes real-time query progress
    visible to `/ws` clients.
    """
    import json as _json

    from src.api.services.event_publisher import EventPublisher
    from src.api.websocket.connection_manager import websocket_manager

    project_id = uuid.uuid4()
    fake_ws = _CaptureWebSocket()
    client_id = await websocket_manager.connect(
        websocket=fake_ws,  # type: ignore[arg-type]
        client_type="web",
        accept=False,
    )
    try:
        assert websocket_manager.subscribe_to_project(client_id, project_id)

        service = DirectExecutionService.__new__(DirectExecutionService)
        # A fresh EventPublisher with no Redis broadcasts locally only.
        service.event_publisher = EventPublisher()

        status = ExecutionStatus(
            execution_id="exec-e2e",
            project_id=str(project_id),
            status="running",
            progress_percentage=73.0,
            current_phase="synthesis",
            supervisor_type="research",
        )
        await service._publish_progress_update(status)

        assert fake_ws.sent, "subscribed client received no WebSocket frame"
        payloads = [_json.loads(frame) for frame in fake_ws.sent]
        progress_frames = [p for p in payloads if p.get("type") == "progress"]
        assert progress_frames, (
            f"no PROGRESS frame among {[p.get('type') for p in payloads]}"
        )
        assert progress_frames[-1]["data"]["progress_percentage"] == 73.0
        assert progress_frames[-1]["data"]["current_phase"] == "synthesis"
    finally:
        await websocket_manager.disconnect(client_id)
