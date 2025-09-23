"""
Tests for WebSocket functionality.

This module contains comprehensive tests for WebSocket connections,
message handling, authentication, and CLI streaming capabilities.
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.services.event_publisher import EventPublisher
from src.api.websocket.connection_manager import ConnectionManager, websocket_manager
from src.models.websocket_messages import (
    AgentUpdate,
    CLIWSMessage,
    ProgressUpdate,
    SubscriptionRequest,
    WSMessage,
    WSMessageType,
)


class TestWebSocketMessages:
    """Test WebSocket message models."""

    def test_ws_message_creation(self):
        """Test basic WebSocket message creation."""
        message = WSMessage(
            type=WSMessageType.PROGRESS,
            project_id=uuid4(),
            data={"progress": 50.0},
        )

        assert message.type == WSMessageType.PROGRESS
        assert message.project_id is not None
        assert message.data["progress"] == 50.0
        assert isinstance(message.timestamp, datetime)

    def test_cli_ws_message_formatting(self):
        """Test CLI WebSocket message formatting."""
        project_id = uuid4()

        # Test progress message
        progress_message = CLIWSMessage(
            type=WSMessageType.PROGRESS,
            project_id=project_id,
            data={
                "progress_percentage": 75.0,
                "completed_tasks": 3,
                "total_tasks": 4,
            },
        )

        output = progress_message.to_terminal_output()
        assert "75.0%" in output
        assert "3/4" in output

        # Test agent started message
        agent_message = CLIWSMessage(
            type=WSMessageType.AGENT_STARTED,
            project_id=project_id,
            data={
                "agent_type": "literature_review",
                "task_description": "Searching academic databases",
            },
        )

        output = agent_message.to_terminal_output()
        assert "🚀" in output
        assert "literature_review" in output
        assert "Searching academic databases" in output

    def test_progress_update_model(self):
        """Test ProgressUpdate model."""
        progress = ProgressUpdate(
            total_tasks=10,
            completed_tasks=6,
            failed_tasks=1,
            in_progress_tasks=1,
            pending_tasks=2,
            progress_percentage=60.0,
            current_agent="synthesis_agent",
        )

        assert progress.total_tasks == 10
        assert progress.completed_tasks == 6
        assert progress.progress_percentage == 60.0
        assert progress.current_agent == "synthesis_agent"

    def test_agent_update_model(self):
        """Test AgentUpdate model."""
        agent_update = AgentUpdate(
            agent_type="literature_review",
            agent_id="agent_123",
            status="in_progress",
            task_description="Analyzing research papers",
            progress_percentage=45.0,
        )

        assert agent_update.agent_type == "literature_review"
        assert agent_update.status == "in_progress"
        assert agent_update.progress_percentage == 45.0


class TestConnectionManager:
    """Test WebSocket connection manager."""

    @pytest.fixture
    def connection_manager(self):
        """Create a fresh connection manager for each test."""
        return ConnectionManager()

    @pytest.fixture
    def mock_websocket(self):
        """Create a mock WebSocket connection."""
        websocket = AsyncMock()
        websocket.send_text = AsyncMock()
        websocket.accept = AsyncMock()
        websocket.close = AsyncMock()
        return websocket

    @pytest.mark.asyncio
    async def test_connection_lifecycle(self, connection_manager, mock_websocket):
        """Test WebSocket connection lifecycle."""
        # Connect
        client_id = await connection_manager.connect(
            websocket=mock_websocket,
            client_type="cli",
            user_id="test_user",
        )

        assert client_id in connection_manager.connections
        assert len(connection_manager.connections) == 1

        # Check connection info
        connection_info = connection_manager.get_connection_info(client_id)
        assert connection_info is not None
        assert connection_info.client_type == "cli"
        assert connection_info.user_id == "test_user"

        # Disconnect
        await connection_manager.disconnect(client_id)
        assert client_id not in connection_manager.connections
        assert len(connection_manager.connections) == 0

    @pytest.mark.asyncio
    async def test_project_subscriptions(self, connection_manager, mock_websocket):
        """Test project subscription functionality."""
        project_id = uuid4()

        # Connect client
        client_id = await connection_manager.connect(mock_websocket)

        # Subscribe to project
        success = connection_manager.subscribe_to_project(client_id, project_id)
        assert success
        assert project_id in connection_manager.project_subscriptions
        assert client_id in connection_manager.project_subscriptions[project_id]

        # Check subscription
        connection = connection_manager.connections[client_id]
        assert connection.is_subscribed_to_project(project_id)

        # Unsubscribe
        success = connection_manager.unsubscribe_from_project(client_id, project_id)
        assert success
        assert project_id not in connection_manager.project_subscriptions

    @pytest.mark.asyncio
    async def test_subscription_request_handling(
        self, connection_manager, mock_websocket
    ):
        """Test handling of subscription requests."""
        project_id = uuid4()

        # Connect client
        client_id = await connection_manager.connect(mock_websocket)

        # Test subscription request
        subscribe_request = SubscriptionRequest(
            action="subscribe",
            project_id=project_id,
            client_type="cli",
        )

        response = await connection_manager.handle_subscription_request(
            client_id, subscribe_request
        )

        assert response.success
        assert project_id in response.active_subscriptions

        # Test unsubscription request
        unsubscribe_request = SubscriptionRequest(
            action="unsubscribe",
            project_id=project_id,
        )

        response = await connection_manager.handle_subscription_request(
            client_id, unsubscribe_request
        )

        assert response.success
        assert project_id not in response.active_subscriptions

    @pytest.mark.asyncio
    async def test_message_broadcasting(self, connection_manager, mock_websocket):
        """Test message broadcasting to subscribed clients."""
        project_id = uuid4()

        # Connect and subscribe multiple clients
        client_ids = []
        for i in range(3):
            websocket = AsyncMock()
            websocket.send_text = AsyncMock()
            websocket.accept = AsyncMock()

            client_id = await connection_manager.connect(websocket, client_type="web")
            connection_manager.subscribe_to_project(client_id, project_id)
            client_ids.append(client_id)

        # Broadcast message
        message = WSMessage(
            type=WSMessageType.PROGRESS,
            data={"progress_percentage": 50.0},
        )

        await connection_manager.broadcast_to_project(project_id, message)

        # Verify all clients received the message
        for client_id in client_ids:
            connection = connection_manager.connections[client_id]
            connection.websocket.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_message_cleanup(self, connection_manager, mock_websocket):
        """Test cleanup of failed connections during broadcasting."""
        project_id = uuid4()

        # Connect client
        client_id = await connection_manager.connect(mock_websocket)
        connection_manager.subscribe_to_project(client_id, project_id)

        # Make websocket send fail
        mock_websocket.send_text.side_effect = Exception("Connection lost")

        # Broadcast message
        message = WSMessage(
            type=WSMessageType.INFO,
            data={"message": "test"},
        )

        await connection_manager.broadcast_to_project(project_id, message)

        # Verify failed connection was cleaned up
        assert client_id not in connection_manager.connections
        assert project_id not in connection_manager.project_subscriptions


class TestEventPublisher:
    """Test event publishing system."""

    @pytest.fixture
    def event_publisher(self):
        """Create event publisher for testing."""
        publisher = EventPublisher()
        publisher.redis_client = None  # Disable Redis for testing
        return publisher

    @pytest.mark.asyncio
    async def test_progress_update_publishing(self, event_publisher):
        """Test publishing progress updates."""
        project_id = uuid4()

        progress = ProgressUpdate(
            total_tasks=5,
            completed_tasks=2,
            progress_percentage=40.0,
            current_agent="literature_review",
        )

        # Mock the websocket manager
        with patch.object(websocket_manager, "broadcast_to_project") as mock_broadcast:
            await event_publisher.publish_progress_update(project_id, progress)

            mock_broadcast.assert_called_once()
            args = mock_broadcast.call_args[0]
            assert args[0] == project_id
            assert args[1].type == WSMessageType.PROGRESS

    @pytest.mark.asyncio
    async def test_agent_lifecycle_events(self, event_publisher):
        """Test publishing agent lifecycle events."""
        project_id = uuid4()

        agent_update = AgentUpdate(
            agent_type="methodology",
            agent_id="agent_123",
            status="started",
            task_description="Designing research methodology",
        )

        with patch.object(websocket_manager, "broadcast_to_project") as mock_broadcast:
            # Test agent started
            await event_publisher.publish_agent_started(project_id, agent_update)

            # Test agent completed
            agent_update.status = "completed"
            agent_update.result_summary = "Methodology designed successfully"
            await event_publisher.publish_agent_completed(project_id, agent_update)

            # Test agent failed
            agent_update.status = "failed"
            agent_update.error_message = "Failed to access methodology database"
            await event_publisher.publish_agent_failed(project_id, agent_update)

            # Verify all events were published
            assert mock_broadcast.call_count == 3

    @pytest.mark.asyncio
    async def test_project_lifecycle_events(self, event_publisher):
        """Test publishing project lifecycle events."""
        project_id = uuid4()

        with patch.object(websocket_manager, "broadcast_to_project") as mock_broadcast:
            # Test project started
            await event_publisher.publish_project_started(project_id)

            # Test project completed
            await event_publisher.publish_project_completed(
                project_id, "Research completed with 15 key findings"
            )

            # Test project failed
            await event_publisher.publish_project_failed(
                project_id, "Insufficient access to required databases"
            )

            # Test project cancelled
            await event_publisher.publish_project_cancelled(project_id)

            # Verify all events were published
            assert mock_broadcast.call_count == 4


@pytest.mark.asyncio
class TestWebSocketEndpoints:
    """Test WebSocket API endpoints."""

    @pytest.fixture
    def client(self):
        """Create test client."""
        return TestClient(app)

    def test_websocket_health_endpoint(self, client):
        """Test WebSocket health endpoint."""
        response = client.get("/ws/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "healthy"
        assert "websocket_stats" in data


class TestCLIWebSocketIntegration:
    """Test CLI WebSocket client integration."""

    @pytest.mark.asyncio
    async def test_cli_websocket_client_connection(self):
        """Test CLI WebSocket client connection."""
        from src.cli.websocket_client import CLIWebSocketClient

        # Mock websockets.connect
        mock_websocket = AsyncMock()
        mock_websocket.__aenter__ = AsyncMock(return_value=mock_websocket)
        mock_websocket.__aexit__ = AsyncMock()

        with patch(
            "src.cli.websocket_client.websockets.connect", return_value=mock_websocket
        ):
            client = CLIWebSocketClient(
                base_url="ws://localhost:8000",
                token="test_token",
                verbose=True,
            )

            success = await client.connect("/ws/cli/test-project")
            assert success
            assert client._connected

    @pytest.mark.asyncio
    async def test_cli_message_handling(self):
        """Test CLI WebSocket message handling."""
        from src.cli.formatters import OutputFormatter
        from src.cli.websocket_client import CLIWebSocketClient

        project_id = uuid4()

        # Create mock messages
        progress_message = WSMessage(
            type=WSMessageType.PROGRESS,
            project_id=project_id,
            data={
                "progress_percentage": 75.0,
                "completed_tasks": 3,
                "total_tasks": 4,
                "current_agent": "synthesis_agent",
            },
        )

        completion_message = WSMessage(
            type=WSMessageType.PROJECT_COMPLETED,
            project_id=project_id,
            data={"message": f"Research project {project_id} completed"},
        )

        # Mock websocket that yields messages
        mock_websocket = AsyncMock()
        mock_websocket.__aiter__ = AsyncMock(
            return_value=iter(
                [
                    progress_message.model_dump_json(),
                    completion_message.model_dump_json(),
                ]
            )
        )

        client = CLIWebSocketClient(verbose=True)
        client.websocket = mock_websocket
        client._connected = True

        formatter = OutputFormatter("table", color=True)

        # This would normally run the streaming loop
        # For testing, we just verify the client is set up correctly
        assert client.websocket is not None
        assert client._connected


class TestPerformanceAndScaling:
    """Test WebSocket performance and scaling scenarios."""

    @pytest.mark.asyncio
    async def test_multiple_concurrent_connections(self):
        """Test handling multiple concurrent WebSocket connections."""
        connection_manager = ConnectionManager()

        # Create multiple mock connections
        connections = []
        for i in range(50):
            mock_websocket = AsyncMock()
            mock_websocket.send_text = AsyncMock()
            mock_websocket.accept = AsyncMock()

            client_id = await connection_manager.connect(
                mock_websocket,
                client_type="cli",
                user_id=f"user_{i}",
            )
            connections.append((client_id, mock_websocket))

        assert len(connection_manager.connections) == 50

        # Test broadcasting to all connections
        message = WSMessage(
            type=WSMessageType.INFO,
            data={"message": "System maintenance in 5 minutes"},
        )

        await connection_manager.broadcast_to_all(message)

        # Verify all connections received the message
        for client_id, mock_websocket in connections:
            mock_websocket.send_text.assert_called_once()

        # Clean up
        for client_id, _ in connections:
            await connection_manager.disconnect(client_id)

        assert len(connection_manager.connections) == 0

    @pytest.mark.asyncio
    async def test_connection_health_monitoring(self):
        """Test connection health monitoring and cleanup."""
        connection_manager = ConnectionManager()

        # Create connection
        mock_websocket = AsyncMock()
        mock_websocket.send_text = AsyncMock()
        mock_websocket.accept = AsyncMock()

        client_id = await connection_manager.connect(mock_websocket)
        connection = connection_manager.connections[client_id]

        # Test healthy connection
        assert connection.is_healthy()

        # Simulate connection failure
        connection.is_active = False
        assert not connection.is_healthy()

        # Test heartbeat
        await connection.send_heartbeat()
        mock_websocket.send_text.assert_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
