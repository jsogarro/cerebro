"""Progress and WebSocket tracking helpers for supervisor coordination."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import WebSocket
from structlog import get_logger

from src.models.supervisor_api_models import (
    SupervisorWebSocketEvent,
    WorkerCoordinationProgressEvent,
)

logger = get_logger()


class SupervisorProgressTracker:
    """Tracks supervisor WebSocket connections and progress events."""

    def __init__(self) -> None:
        self.active_connections: dict[str, list[WebSocket]] = {}
        self.supervisor_subscriptions: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, client_id: str) -> None:
        """Accept and register a WebSocket connection."""
        await websocket.accept()
        if client_id not in self.active_connections:
            self.active_connections[client_id] = []
        self.active_connections[client_id].append(websocket)

    def disconnect(self, websocket: WebSocket, client_id: str) -> None:
        """Remove a WebSocket connection."""
        if client_id in self.active_connections:
            self.active_connections[client_id].remove(websocket)
            if not self.active_connections[client_id]:
                del self.active_connections[client_id]

    def subscribe_supervisor(self, supervisor_type: str, websocket: WebSocket) -> None:
        """Subscribe a WebSocket to supervisor-specific events."""
        if supervisor_type not in self.supervisor_subscriptions:
            self.supervisor_subscriptions[supervisor_type] = []
        self.supervisor_subscriptions[supervisor_type].append(websocket)

    def unsubscribe_supervisor(self, supervisor_type: str, websocket: WebSocket) -> None:
        """Remove a WebSocket from supervisor-specific events."""
        if (
            supervisor_type in self.supervisor_subscriptions
            and websocket in self.supervisor_subscriptions[supervisor_type]
        ):
            self.supervisor_subscriptions[supervisor_type].remove(websocket)

    async def send_supervisor_event(
        self, supervisor_type: str, event: SupervisorWebSocketEvent
    ) -> None:
        """Send an event to all clients subscribed to a supervisor."""
        if supervisor_type in self.supervisor_subscriptions:
            for connection in self.supervisor_subscriptions[supervisor_type]:
                try:
                    await connection.send_json(event.model_dump())
                except Exception as exc:
                    logger.error("supervisor_event_send_failed", error=str(exc))

    async def broadcast_event(self, event: dict[str, Any]) -> None:
        """Broadcast an event to all active WebSocket connections."""
        for client_connections in self.active_connections.values():
            for connection in client_connections:
                try:
                    await connection.send_json(event)
                except Exception as exc:
                    logger.error("supervisor_event_broadcast_failed", error=str(exc))

    async def iter_coordination_progress_events(
        self, coordination_id: str, delay_seconds: float = 1.0
    ) -> AsyncIterator[WorkerCoordinationProgressEvent]:
        """Yield simulated coordination progress events using the legacy schema."""
        for progress in [10, 30, 50, 70, 90, 100]:
            await asyncio.sleep(delay_seconds)
            yield WorkerCoordinationProgressEvent(
                coordination_id=coordination_id,
                event_type="progress",
                progress_percentage=float(progress),
                current_phase=f"Phase {progress // 25 + 1}",
                workers_active=5 - (progress // 25),
                estimated_remaining_seconds=max(0, 10 - progress // 10),
            )
