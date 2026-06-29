"""Tests for WebSocket performance and scaling scenarios."""

from unittest.mock import AsyncMock

import pytest

from src.api.websocket.connection_manager import ConnectionManager
from src.models.websocket_messages import WSMessage, WSMessageType


class TestPerformanceAndScaling:
    """Test WebSocket performance and scaling scenarios."""

    @pytest.mark.asyncio
    async def test_multiple_concurrent_connections(self) -> None:
        """Test handling multiple concurrent WebSocket connections."""
        connection_manager = ConnectionManager()

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
            mock_websocket.send_text.reset_mock()
            connections.append((client_id, mock_websocket))

        assert len(connection_manager.connections) == 50

        message = WSMessage(
            type=WSMessageType.INFO,
            data={"message": "System maintenance in 5 minutes"},
        )

        await connection_manager.broadcast_to_all(message)

        for _client_id, mock_websocket in connections:
            mock_websocket.send_text.assert_called_once()

        for client_id, _ in connections:
            await connection_manager.disconnect(client_id)

        assert len(connection_manager.connections) == 0

    @pytest.mark.asyncio
    async def test_connection_health_monitoring(self) -> None:
        """Test connection health monitoring and cleanup."""
        connection_manager = ConnectionManager()

        mock_websocket = AsyncMock()
        mock_websocket.send_text = AsyncMock()
        mock_websocket.accept = AsyncMock()

        client_id = await connection_manager.connect(mock_websocket)
        connection = connection_manager.connections[client_id]

        assert connection.is_healthy()

        connection.is_active = False
        assert not connection.is_healthy()

        await connection.send_heartbeat()
        mock_websocket.send_text.assert_called()
