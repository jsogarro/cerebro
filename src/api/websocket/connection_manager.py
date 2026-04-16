"""
WebSocket connection manager for real-time updates.

This module manages WebSocket connections, handles subscriptions,
and provides message broadcasting capabilities.
"""

import asyncio
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import WebSocket
from structlog import get_logger

from src.models.websocket_messages import (
    CLIWSMessage,
    ConnectionInfo,
    HeartbeatMessage,
    SubscriptionRequest,
    SubscriptionResponse,
    WSMessage,
    WSMessageType,
)

logger = get_logger()


class WebSocketConnection:
    """Represents a single WebSocket connection."""

    def __init__(
        self,
        websocket: WebSocket,
        client_id: str,
        client_type: str = "web",
        user_id: str | None = None,
    ):
        self.websocket = websocket
        self.client_id = client_id
        self.client_type = client_type
        self.user_id = user_id
        self.project_subscriptions: set[UUID] = set()
        self.connected_at = datetime.utcnow()
        self.last_heartbeat = datetime.utcnow()
        self.is_active = True

    async def send_message(self, message: WSMessage) -> bool:
        """Send a message to this connection."""
        try:
            # Format message based on client type
            if self.client_type == "cli":
                cli_message = CLIWSMessage(**message.model_dump())
                await self.websocket.send_text(cli_message.model_dump_json())
            else:
                await self.websocket.send_text(message.model_dump_json())

            return True
        except Exception as e:
            logger.warning(
                "Failed to send message to WebSocket",
                client_id=self.client_id,
                error=str(e),
            )
            self.is_active = False
            return False

    async def send_heartbeat(self) -> bool:
        heartbeat = HeartbeatMessage(client_id=self.client_id)
        heartbeat_message = WSMessage(
            type=WSMessageType.HEARTBEAT,
            project_id=None,
            data=heartbeat.model_dump(),
        )
        return await self.send_message(heartbeat_message)

    def update_heartbeat(self) -> None:
        self.last_heartbeat = datetime.utcnow()

    def is_healthy(self, timeout_seconds: int = 60) -> bool:
        """Check if connection is healthy based on heartbeat."""
        if not self.is_active:
            return False

        time_since_heartbeat = datetime.utcnow() - self.last_heartbeat
        return time_since_heartbeat.total_seconds() < timeout_seconds

    def subscribe_to_project(self, project_id: UUID) -> None:
        self.project_subscriptions.add(project_id)

    def unsubscribe_from_project(self, project_id: UUID) -> None:
        self.project_subscriptions.discard(project_id)

    def is_subscribed_to_project(self, project_id: UUID) -> bool:
        """Check if subscribed to a project."""
        return project_id in self.project_subscriptions


class ConnectionManager:
    """Manages WebSocket connections and message broadcasting."""

    def __init__(self) -> None:
        self.connections: dict[str, WebSocketConnection] = {}
        self.project_subscriptions: dict[UUID, set[str]] = {}
        self.user_subscriptions: dict[str, set[str]] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._shutdown = False

    async def connect(
        self,
        websocket: WebSocket,
        client_type: str = "web",
        user_id: str | None = None,
    ) -> str:
        """Accept a new WebSocket connection."""
        await websocket.accept()

        client_id = str(uuid4())
        connection = WebSocketConnection(
            websocket=websocket,
            client_id=client_id,
            client_type=client_type,
            user_id=user_id,
        )

        self.connections[client_id] = connection

        # Track user subscriptions
        if user_id:
            if user_id not in self.user_subscriptions:
                self.user_subscriptions[user_id] = set()
            self.user_subscriptions[user_id].add(client_id)

        logger.info(
            "WebSocket connection established",
            client_id=client_id,
            client_type=client_type,
            user_id=user_id,
        )

        welcome_message = WSMessage(
            type=WSMessageType.CONNECTED,
            project_id=None,
            data={
                "client_id": client_id,
                "message": "Connected to Research Platform WebSocket",
                "server_time": datetime.utcnow().isoformat(),
            },
        )
        await connection.send_message(welcome_message)

        # Start heartbeat monitoring if not already running
        if not self._heartbeat_task:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_monitor())

        return client_id

    async def disconnect(self, client_id: str) -> None:
        """Disconnect and clean up a WebSocket connection."""
        if client_id not in self.connections:
            return

        connection = self.connections[client_id]

        # Remove from project subscriptions
        for project_id in list(connection.project_subscriptions):
            self.unsubscribe_from_project(client_id, project_id)

        # Remove from user subscriptions
        if connection.user_id and connection.user_id in self.user_subscriptions:
            self.user_subscriptions[connection.user_id].discard(client_id)
            if not self.user_subscriptions[connection.user_id]:
                del self.user_subscriptions[connection.user_id]

        # Remove connection
        del self.connections[client_id]

        logger.info(
            "WebSocket connection closed",
            client_id=client_id,
            client_type=connection.client_type,
            user_id=connection.user_id,
        )

    def subscribe_to_project(self, client_id: str, project_id: UUID) -> bool:
        """Subscribe a client to project updates."""
        if client_id not in self.connections:
            return False

        connection = self.connections[client_id]
        connection.subscribe_to_project(project_id)

        # Track in project subscriptions
        if project_id not in self.project_subscriptions:
            self.project_subscriptions[project_id] = set()
        self.project_subscriptions[project_id].add(client_id)

        logger.info(
            "Client subscribed to project",
            client_id=client_id,
            project_id=str(project_id),
        )
        return True

    def unsubscribe_from_project(self, client_id: str, project_id: UUID) -> bool:
        """Unsubscribe a client from project updates."""
        if client_id not in self.connections:
            return False

        connection = self.connections[client_id]
        connection.unsubscribe_from_project(project_id)

        # Remove from project subscriptions
        if project_id in self.project_subscriptions:
            self.project_subscriptions[project_id].discard(client_id)
            if not self.project_subscriptions[project_id]:
                del self.project_subscriptions[project_id]

        logger.info(
            "Client unsubscribed from project",
            client_id=client_id,
            project_id=str(project_id),
        )
        return True

    async def handle_subscription_request(
        self, client_id: str, request: SubscriptionRequest
    ) -> SubscriptionResponse:
        """Handle subscription/unsubscription requests."""
        if client_id not in self.connections:
            return SubscriptionResponse(
                success=False,
                message="Client not connected",
            )

        connection = self.connections[client_id]

        if request.action == "subscribe" and request.project_id:
            success = self.subscribe_to_project(client_id, request.project_id)
            return SubscriptionResponse(
                success=success,
                message=(
                    f"Subscribed to project {request.project_id}"
                    if success
                    else "Failed to subscribe"
                ),
                active_subscriptions=list(connection.project_subscriptions),
            )

        elif request.action == "unsubscribe" and request.project_id:
            success = self.unsubscribe_from_project(client_id, request.project_id)
            return SubscriptionResponse(
                success=success,
                message=(
                    f"Unsubscribed from project {request.project_id}"
                    if success
                    else "Failed to unsubscribe"
                ),
                active_subscriptions=list(connection.project_subscriptions),
            )

        else:
            return SubscriptionResponse(
                success=False,
                message="Invalid subscription request",
            )

    async def broadcast_to_project(self, project_id: UUID, message: WSMessage) -> None:
        """Broadcast a message to all clients subscribed to a project."""
        if project_id not in self.project_subscriptions:
            return

        message.project_id = project_id
        client_ids = list(self.project_subscriptions[project_id])

        logger.debug(
            "Broadcasting message to project subscribers",
            project_id=str(project_id),
            message_type=message.type,
            client_count=len(client_ids),
        )

        # Send to all subscribed clients
        failed_clients = []
        for client_id in client_ids:
            if client_id in self.connections:
                success = await self.connections[client_id].send_message(message)
                if not success:
                    failed_clients.append(client_id)

        # Clean up failed connections
        for client_id in failed_clients:
            await self.disconnect(client_id)

    async def broadcast_to_user(self, user_id: str, message: WSMessage) -> None:
        """Broadcast a message to all connections for a user."""
        if user_id not in self.user_subscriptions:
            return

        client_ids = list(self.user_subscriptions[user_id])

        logger.debug(
            "Broadcasting message to user",
            user_id=user_id,
            message_type=message.type,
            client_count=len(client_ids),
        )

        # Send to all user's clients
        failed_clients = []
        for client_id in client_ids:
            if client_id in self.connections:
                success = await self.connections[client_id].send_message(message)
                if not success:
                    failed_clients.append(client_id)

        # Clean up failed connections
        for client_id in failed_clients:
            await self.disconnect(client_id)

    async def broadcast_to_all(self, message: WSMessage) -> None:
        """Broadcast a message to all connected clients."""
        client_ids = list(self.connections.keys())

        logger.debug(
            "Broadcasting message to all clients",
            message_type=message.type,
            client_count=len(client_ids),
        )

        # Send to all clients
        failed_clients = []
        for client_id in client_ids:
            success = await self.connections[client_id].send_message(message)
            if not success:
                failed_clients.append(client_id)

        # Clean up failed connections
        for client_id in failed_clients:
            await self.disconnect(client_id)

    def get_connection_info(self, client_id: str) -> ConnectionInfo | None:
        """Get information about a connection."""
        if client_id not in self.connections:
            return None

        connection = self.connections[client_id]
        return ConnectionInfo(
            client_id=client_id,
            client_type=connection.client_type,
            user_id=connection.user_id,
            project_subscriptions=list(connection.project_subscriptions),
            connected_at=connection.connected_at,
            last_heartbeat=connection.last_heartbeat,
        )

    def get_stats(self) -> dict[str, int | dict[str, int] | list[UUID]]:
        """Get connection statistics."""
        return {
            "total_connections": len(self.connections),
            "connections_by_type": dict[str, int]({
                client_type: len(
                    [
                        c
                        for c in self.connections.values()
                        if c.client_type == client_type
                    ]
                )
                for client_type in set(c.client_type for c in self.connections.values())
            }),
            "total_project_subscriptions": len(self.project_subscriptions),
            "total_user_subscriptions": len(self.user_subscriptions),
            "active_projects": list(self.project_subscriptions.keys()),
        }

    async def _heartbeat_monitor(self) -> None:
        """Background task to monitor connection health."""
        while not self._shutdown:
            try:
                # Check all connections for health
                unhealthy_clients = []
                for client_id, connection in self.connections.items():
                    if not connection.is_healthy():
                        unhealthy_clients.append(client_id)
                    else:
                        # Send heartbeat ping
                        await connection.send_heartbeat()

                # Disconnect unhealthy clients
                for client_id in unhealthy_clients:
                    logger.warning(
                        "Disconnecting unhealthy WebSocket client",
                        client_id=client_id,
                    )
                    await self.disconnect(client_id)

                # Wait before next heartbeat check
                await asyncio.sleep(30)  # Check every 30 seconds

            except Exception as e:
                logger.error(
                    "Error in heartbeat monitor",
                    error=str(e),
                )
                await asyncio.sleep(10)  # Wait before retrying

    async def shutdown(self) -> None:
        """Shutdown the connection manager."""
        self._shutdown = True

        # Cancel heartbeat task
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # Close all connections
        client_ids = list(self.connections.keys())
        for client_id in client_ids:
            await self.disconnect(client_id)

        logger.info("WebSocket connection manager shutdown complete")


# Global connection manager instance
websocket_manager = ConnectionManager()
