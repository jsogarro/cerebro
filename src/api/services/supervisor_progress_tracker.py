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
        self.connection_organizations: dict[WebSocket, str | None] = {}

    async def connect(self, websocket: WebSocket, client_id: str) -> None:
        """Accept and register a WebSocket connection."""
        await websocket.accept()
        principal = getattr(websocket.state, "websocket_principal", None)
        self.connection_organizations[websocket] = getattr(
            principal, "organization_id", None
        )
        if client_id not in self.active_connections:
            self.active_connections[client_id] = []
        self.active_connections[client_id].append(websocket)

    def disconnect(self, websocket: WebSocket, client_id: str) -> None:
        """Remove a WebSocket connection."""
        if client_id in self.active_connections:
            self.active_connections[client_id].remove(websocket)
            self.connection_organizations.pop(websocket, None)
            if not self.active_connections[client_id]:
                del self.active_connections[client_id]

    def subscribe_supervisor(self, supervisor_type: str, websocket: WebSocket) -> None:
        """Subscribe a WebSocket to supervisor-specific events."""
        if supervisor_type not in self.supervisor_subscriptions:
            self.supervisor_subscriptions[supervisor_type] = []
        self.supervisor_subscriptions[supervisor_type].append(websocket)

    def unsubscribe_supervisor(
        self, supervisor_type: str, websocket: WebSocket
    ) -> None:
        """Remove a WebSocket from supervisor-specific events."""
        if (
            supervisor_type in self.supervisor_subscriptions
            and websocket in self.supervisor_subscriptions[supervisor_type]
        ):
            self.supervisor_subscriptions[supervisor_type].remove(websocket)

    def _same_tenant(self, connection: WebSocket, organization_id: str | None) -> bool:
        return (
            organization_id is not None
            and self.connection_organizations.get(connection) == organization_id
        )

    async def send_supervisor_event(
        self,
        supervisor_type: str,
        event: SupervisorWebSocketEvent,
        organization_id: str | None,
    ) -> None:
        """Send an event to the organization's clients subscribed to a supervisor."""
        if supervisor_type in self.supervisor_subscriptions:
            for connection in self.supervisor_subscriptions[supervisor_type]:
                if not self._same_tenant(connection, organization_id):
                    continue
                try:
                    await connection.send_json(event.model_dump())
                except Exception as exc:
                    logger.error("supervisor_event_send_failed", error=str(exc))

    async def broadcast_event(
        self, event: dict[str, Any], organization_id: str | None
    ) -> None:
        """Broadcast an event to the organization's WebSocket connections."""
        for client_connections in self.active_connections.values():
            for connection in client_connections:
                if not self._same_tenant(connection, organization_id):
                    continue
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
