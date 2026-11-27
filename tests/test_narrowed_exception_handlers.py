"""Handlers that used to swallow every exception now catch only what the body raises.

Each test pairs an expected failure that is still absorbed with an unexpected
one (a bug) that must reach the caller.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import redis.asyncio as redis
from jose import JWTError
from sqlalchemy.exc import OperationalError
from starlette.requests import Request
from websockets.exceptions import ConnectionClosedError

from src.auth.jwt_service import JWTService
from src.auth.password_service import PasswordService
from src.cli.websocket_client import CLIWebSocketClient
from src.core import telemetry, tracing
from src.mcp.academic_filters import AcademicSearchFilters
from src.mcp.tools.academic_search_tool import AcademicSearchTool
from src.middleware import auth_middleware
from src.models.db.session import DatabaseManager
from src.prompts.manager import PromptManager


class TestRevokeToken:
    @pytest.fixture
    async def service(self) -> JWTService:
        client = AsyncMock()
        client.setex.return_value = True
        return JWTService(redis_client=client)

    async def test_invalid_token_is_not_revoked(self, service: JWTService) -> None:
        assert await service.revoke_token("not-a-jwt") is False

    async def test_redis_outage_is_not_revoked(self, service: JWTService) -> None:
        pair = await service.generate_token_pair(user_id="u1", email="a@b.co")
        service.redis_client.setex.side_effect = redis.ConnectionError("down")
        assert await service.revoke_token(pair.access_token) is False

    async def test_unexpected_error_propagates(self, service: JWTService) -> None:
        pair = await service.generate_token_pair(user_id="u1", email="a@b.co")
        service.redis_client.setex.side_effect = RuntimeError("bug")
        with pytest.raises(RuntimeError, match="bug"):
            await service.revoke_token(pair.access_token)


class TestBreachCheck:
    @pytest.fixture
    def service(self) -> PasswordService:
        svc = PasswordService(redis_client=AsyncMock(), check_breaches=True)
        svc.http_client = AsyncMock()
        return svc

    async def test_api_outage_fails_open(self, service: PasswordService) -> None:
        service.http_client.get.side_effect = httpx.ConnectError("down")
        assert await service.check_password_breach("hunter2") is False

    async def test_unexpected_error_propagates(self, service: PasswordService) -> None:
        service.http_client.get.side_effect = RuntimeError("bug")
        with pytest.raises(RuntimeError, match="bug"):
            await service.check_password_breach("hunter2")


class TestWebSocketClient:
    async def test_refused_connection_returns_false(self) -> None:
        client = CLIWebSocketClient(base_url="http://x", token="t")
        with patch("websockets.connect", AsyncMock(side_effect=OSError("refused"))):
            assert await client.connect("/ws") is False

    async def test_unexpected_connect_error_propagates(self) -> None:
        client = CLIWebSocketClient(base_url="http://x", token="t")
        with (
            patch("websockets.connect", AsyncMock(side_effect=RuntimeError("bug"))),
            pytest.raises(RuntimeError, match="bug"),
        ):
            await client.connect("/ws")

    async def test_heartbeat_on_closed_socket_is_absorbed(self) -> None:
        client = CLIWebSocketClient(base_url="http://x", token="t")
        client.websocket = AsyncMock()
        client.websocket.send.side_effect = ConnectionClosedError(None, None)
        client._connected = True
        await client.send_heartbeat_response()

    async def test_unexpected_heartbeat_error_propagates(self) -> None:
        client = CLIWebSocketClient(base_url="http://x", token="t")
        client.websocket = AsyncMock()
        client.websocket.send.side_effect = RuntimeError("bug")
        client._connected = True
        with pytest.raises(RuntimeError, match="bug"):
            await client.send_heartbeat_response()


class TestSettingsProbes:
    def test_telemetry_enabled_unexpected_error_propagates(self) -> None:
        with (
            patch("src.core.config.get_settings", side_effect=RuntimeError("bug")),
            pytest.raises(RuntimeError, match="bug"),
        ):
            telemetry.telemetry_enabled()

    def test_tracing_enabled_misconfiguration_is_false(self) -> None:
        with patch("src.core.config.get_settings", side_effect=ValueError("bad env")):
            assert tracing._tracing_enabled() is False

    def test_tracing_enabled_unexpected_error_propagates(self) -> None:
        with (
            patch("src.core.config.get_settings", side_effect=RuntimeError("bug")),
            pytest.raises(RuntimeError, match="bug"),
        ):
            tracing._tracing_enabled()

    def test_count_tokens_encoder_error_is_zero(self) -> None:
        encoder = MagicMock()
        encoder.encode.side_effect = ValueError("disallowed special token")
        with (
            patch.object(telemetry, "TIKTOKEN_AVAILABLE", True),
            patch.object(telemetry, "_get_encoder", return_value=encoder),
        ):
            assert telemetry.count_tokens("<|endoftext|>") == 0

    def test_count_tokens_unexpected_error_propagates(self) -> None:
        encoder = MagicMock()
        encoder.encode.side_effect = RuntimeError("bug")
        with (
            patch.object(telemetry, "TIKTOKEN_AVAILABLE", True),
            patch.object(telemetry, "_get_encoder", return_value=encoder),
            pytest.raises(RuntimeError, match="bug"),
        ):
            telemetry.count_tokens("x")


class TestAcademicSearchBackends:
    @pytest.fixture
    def tool(self) -> AcademicSearchTool:
        tool = AcademicSearchTool()
        tool.client = AsyncMock()
        return tool

    @pytest.mark.parametrize("backend", ["pubmed", "arxiv", "semantic_scholar"])
    async def test_network_failure_yields_no_results(
        self, tool: AcademicSearchTool, backend: str
    ) -> None:
        tool.client.get.side_effect = httpx.ConnectError("down")
        search = getattr(tool, f"_search_{backend}")
        assert await search("q", 5, AcademicSearchFilters()) == []

    @pytest.mark.parametrize("backend", ["pubmed", "arxiv", "semantic_scholar"])
    async def test_unexpected_error_propagates(
        self, tool: AcademicSearchTool, backend: str
    ) -> None:
        tool.client.get.side_effect = RuntimeError("bug")
        search = getattr(tool, f"_search_{backend}")
        with pytest.raises(RuntimeError, match="bug"):
            await search("q", 5, AcademicSearchFilters())

    async def test_malformed_arxiv_xml_yields_no_results(
        self, tool: AcademicSearchTool
    ) -> None:
        tool.client.get.return_value = SimpleNamespace(status_code=200, text="<oops")
        assert await tool._search_arxiv("q", 5, AcademicSearchFilters()) == []


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/private",
            "headers": [(b"authorization", b"Bearer tok")],
            "app": SimpleNamespace(dependency_overrides={}),
        }
    )


class TestAuthMiddleware:
    async def test_optional_user_invalid_token_is_anonymous(self) -> None:
        getter = auth_middleware.optional_user()
        service = SimpleNamespace(validate_token=AsyncMock(side_effect=JWTError("bad")))
        creds = SimpleNamespace(credentials="tok")
        assert await getter(creds, MagicMock(), service) is None

    async def test_optional_user_database_error_propagates(self) -> None:
        getter = auth_middleware.optional_user()
        service = SimpleNamespace(
            validate_token=AsyncMock(return_value=SimpleNamespace(sub="u1"))
        )
        repo = MagicMock()
        repo.get = AsyncMock(
            side_effect=OperationalError("select", {}, Exception("db"))
        )
        with (
            patch.object(auth_middleware, "UserRepository", return_value=repo),
            pytest.raises(OperationalError),
        ):
            await getter(SimpleNamespace(credentials="tok"), MagicMock(), service)


class TestDatabase:
    async def test_connection_failure_reports_false(self) -> None:
        engine = MagicMock()
        engine.begin.side_effect = OSError("refused")
        assert await DatabaseManager(engine).check_connection() is False

    async def test_unexpected_connection_check_error_propagates(self) -> None:
        engine = MagicMock()
        engine.begin.side_effect = RuntimeError("bug")
        with pytest.raises(RuntimeError, match="bug"):
            await DatabaseManager(engine).check_connection()

    async def test_slow_query_diagnostics_absent_extension_is_empty(self) -> None:
        engine = MagicMock()
        engine.begin.side_effect = OperationalError("select", {}, Exception("nope"))
        assert await DatabaseManager(engine).get_slow_queries() == []


class TestPromptManager:
    @pytest.fixture
    def manager(self, tmp_path: Path) -> PromptManager:
        return PromptManager({"templates_dir": str(tmp_path)})

    async def test_register_template_error_propagates(
        self, manager: PromptManager
    ) -> None:
        with pytest.raises(AttributeError):
            await manager.register_template(object())  # type: ignore[arg-type]

    async def test_update_performance_error_propagates(
        self, manager: PromptManager
    ) -> None:
        manager._templates["t"] = SimpleNamespace(  # type: ignore[assignment]
            metadata=SimpleNamespace(usage_count="many")
        )
        with pytest.raises(TypeError):
            await manager.update_template_performance("t", True, 0.5, 10)

    async def test_unparseable_template_file_is_skipped(
        self, manager: PromptManager, tmp_path: Path
    ) -> None:
        (tmp_path / "bad.yaml").write_text("a: [unclosed")
        await manager._load_all_templates()
        assert manager._templates == {}

    async def test_unexpected_load_error_propagates(
        self, manager: PromptManager, tmp_path: Path
    ) -> None:
        (tmp_path / "any.yaml").write_text("a: 1")
        with (
            patch.object(
                manager, "_load_template_file", side_effect=RuntimeError("bug")
            ),
            pytest.raises(RuntimeError, match="bug"),
        ):
            await manager._load_all_templates()
