"""Tests for TalkHier WebSocket session events."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocket

from src.api.websocket.talkhier_websocket_events import TalkHierWebSocketHandler
from src.models.talkhier_api_models import InteractiveMessage, MessageRole


class TestTalkHierWebSocketHandler:
    """Test TalkHier WebSocket handler."""

    @pytest.fixture
    def websocket_handler(self) -> TalkHierWebSocketHandler:
        """Create WebSocket handler instance."""
        return TalkHierWebSocketHandler()

    @pytest.mark.asyncio
    async def test_register_session_connection(
        self, websocket_handler: TalkHierWebSocketHandler
    ) -> None:
        """Test session connection registration."""
        session_id = "test-session"
        connection_id = "conn-123"
        websocket = MagicMock(spec=WebSocket)

        await websocket_handler.register_session_connection(
            session_id,
            connection_id,
            websocket,
        )

        assert session_id in websocket_handler.session_connections
        assert connection_id in websocket_handler.session_connections[session_id]
        assert connection_id in websocket_handler.connections
        assert websocket_handler.connections[connection_id] == websocket

    @pytest.mark.asyncio
    async def test_broadcast_round_started(
        self, websocket_handler: TalkHierWebSocketHandler
    ) -> None:
        """Test round started event broadcasting."""
        session_id = "test-session"
        connection_id = "conn-456"
        websocket = AsyncMock(spec=WebSocket)

        await websocket_handler.register_session_connection(
            session_id,
            connection_id,
            websocket,
        )

        await websocket_handler.broadcast_round_started(
            session_id,
            round_number=2,
            participants=["agent1", "agent2"],
        )

        websocket.send_json.assert_called_once()
        sent_data = websocket.send_json.call_args[0][0]
        assert sent_data["event_type"] == "round_started"
        assert sent_data["session_id"] == session_id
        assert sent_data["round_number"] == 2

    @pytest.mark.asyncio
    async def test_interactive_session_management(
        self, websocket_handler: TalkHierWebSocketHandler
    ) -> None:
        """Test interactive session management."""
        session_id = "interactive-session"
        connection_id = "conn-789"
        websocket = AsyncMock(spec=WebSocket)

        await websocket_handler.register_interactive_session(
            session_id,
            connection_id,
            websocket,
        )

        assert session_id in websocket_handler.interactive_sessions
        assert connection_id in websocket_handler.interactive_sessions[session_id]

        message = InteractiveMessage(
            content="Test message",
            role=MessageRole.WORKER,
            agent_id="test-agent",
            confidence=0.85,
        )

        await websocket_handler.handle_interactive_message(
            session_id,
            connection_id,
            message,
        )

    @pytest.mark.asyncio
    async def test_coordination_monitoring(
        self, websocket_handler: TalkHierWebSocketHandler
    ) -> None:
        """Test coordination monitoring."""
        coordination_id = "coord-123"
        connection_id = "monitor-001"
        websocket = AsyncMock(spec=WebSocket)

        await websocket_handler.register_coordination_monitor(
            coordination_id,
            connection_id,
            websocket,
        )

        assert coordination_id in websocket_handler.coordination_monitors
        assert connection_id in websocket_handler.coordination_monitors[coordination_id]

        await websocket_handler.broadcast_coordination_update(
            coordination_id,
            {
                "overall_progress": 0.5,
                "session_count": 3,
                "average_quality": 0.83,
            },
        )

        websocket.send_json.assert_called_once()
        sent_data = websocket.send_json.call_args[0][0]
        assert sent_data["type"] == "coordination_update"
        assert sent_data["coordination_id"] == coordination_id
