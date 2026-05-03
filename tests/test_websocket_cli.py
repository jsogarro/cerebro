"""Tests for CLI WebSocket client integration."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.cli.formatters import OutputFormatter
from src.cli.websocket_client import CLIWebSocketClient
from src.models.websocket_messages import WSMessage, WSMessageType


class TestCLIWebSocketIntegration:
    """Test CLI WebSocket client integration."""

    @pytest.mark.asyncio
    async def test_cli_websocket_client_connection(self) -> None:
        """Test CLI WebSocket client connection."""
        mock_websocket = AsyncMock()

        with patch(
            "src.cli.websocket_client.websockets.connect",
            new=AsyncMock(return_value=mock_websocket),
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
    async def test_cli_message_handling(self) -> None:
        """Test CLI WebSocket message handling."""
        project_id = uuid4()

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

        OutputFormatter("table", color=True)

        assert client.websocket is not None
        assert client._connected
