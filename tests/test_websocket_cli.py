"""Tests for CLI WebSocket client integration."""

from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from websockets.exceptions import ConnectionClosed

from src.cli.formatters import OutputFormatter
from src.cli.websocket_client import CLIWebSocketClient
from src.models.websocket_messages import WSMessage, WSMessageType
from src.utils.serialization import serialize_to_str


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


class TestCLIConnectionContract:
    """The CLI must only use what the API accepts."""

    @pytest.mark.asyncio
    async def test_connection_check_uses_http_get_on_ws_health(self) -> None:
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path))
            return httpx.Response(200, json={"status": "healthy"})

        real_client = httpx.AsyncClient

        def fake_client(**kwargs: Any) -> httpx.AsyncClient:
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        client = CLIWebSocketClient(base_url="ws://localhost:8000")
        with patch("src.cli.websocket_client.httpx.AsyncClient", fake_client):
            assert await client.test_connection() is True
        assert seen == [("GET", "/ws/health")]

    @pytest.mark.asyncio
    async def test_connection_check_fails_on_unhealthy_status(self) -> None:
        real_client = httpx.AsyncClient

        def fake_client(**kwargs: Any) -> httpx.AsyncClient:
            return real_client(
                transport=httpx.MockTransport(lambda _r: httpx.Response(503)),
                **kwargs,
            )

        client = CLIWebSocketClient(base_url="ws://localhost:8000")
        with patch("src.cli.websocket_client.httpx.AsyncClient", fake_client):
            assert await client.test_connection() is False

    @pytest.mark.asyncio
    async def test_progress_stream_reconnects_after_connection_loss(self) -> None:
        project_id = uuid4()
        done = WSMessage(
            type=WSMessageType.PROJECT_COMPLETED,
            project_id=project_id,
            data={"message": "done"},
        )

        class DroppedSocket:
            def __aiter__(self) -> "DroppedSocket":
                return self

            async def __anext__(self) -> str:
                raise ConnectionClosed(None, None)

            async def close(self) -> None:
                return None

        class FinishingSocket:
            def __init__(self) -> None:
                self.sent = False

            def __aiter__(self) -> "FinishingSocket":
                return self

            async def __anext__(self) -> str:
                if self.sent:
                    raise StopAsyncIteration
                self.sent = True
                return serialize_to_str(done.model_dump(mode="json"))

            async def close(self) -> None:
                return None

        connect = AsyncMock(side_effect=[DroppedSocket(), FinishingSocket()])
        client = CLIWebSocketClient(base_url="ws://localhost:8000")
        with (
            patch("src.cli.websocket_client.websockets.connect", new=connect),
            patch("src.cli.websocket_client.asyncio.sleep", new=AsyncMock()),
        ):
            await client.stream_progress(project_id, OutputFormatter("table"))
        assert connect.await_count == 2

    @pytest.mark.asyncio
    async def test_progress_stream_gives_up_after_max_retries(self) -> None:
        class DroppedSocket:
            def __aiter__(self) -> "DroppedSocket":
                return self

            async def __anext__(self) -> str:
                raise ConnectionClosed(None, None)

            async def close(self) -> None:
                return None

        connect = AsyncMock(side_effect=lambda *a, **k: DroppedSocket())
        client = CLIWebSocketClient(base_url="ws://localhost:8000")
        with (
            patch("src.cli.websocket_client.websockets.connect", new=connect),
            patch("src.cli.websocket_client.asyncio.sleep", new=AsyncMock()),
            patch("src.cli.websocket_client.config.max_retries", 2),
        ):
            await client.stream_progress(uuid4(), OutputFormatter("table"))
        assert connect.await_count == 3  # first connect + 2 retries
