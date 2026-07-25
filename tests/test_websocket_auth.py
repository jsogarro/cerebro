"""WebSocket authentication must require an explicit local-only bypass."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.api.websocket.auth import (
    WebSocketAuthError,
    verify_project_access,
    verify_websocket_token,
)
from src.auth.jwt_service import JWTService
from src.core.config import Settings, settings
from src.core.environment import load_environment


@pytest.mark.asyncio
async def test_development_environment_alone_does_not_allow_anonymous_websocket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "DEV_ALLOW_ANONYMOUS_WEBSOCKETS", False)

    with pytest.raises(WebSocketAuthError, match="Authentication token required"):
        await verify_websocket_token(None, AsyncMock(spec=JWTService))
    assert await verify_project_access(None, "project-1") is False


@pytest.mark.asyncio
async def test_explicit_development_opt_in_allows_anonymous_websocket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "DEV_ALLOW_ANONYMOUS_WEBSOCKETS", True)

    assert await verify_websocket_token(None, AsyncMock(spec=JWTService)) is None
    assert await verify_project_access(None, "project-1") is True


@pytest.mark.asyncio
async def test_production_ignores_anonymous_websocket_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "DEV_ALLOW_ANONYMOUS_WEBSOCKETS", True)

    with pytest.raises(WebSocketAuthError, match="Authentication token required"):
        await verify_websocket_token(None, AsyncMock(spec=JWTService))
    assert await verify_project_access(None, "project-1") is False


@pytest.mark.asyncio
async def test_home_development_value_cannot_bypass_websocket_auth_by_itself(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_env = tmp_path / "project.env"
    home_env = tmp_path / "home.env"
    project_env.write_text("ENVIRONMENT=production\n", encoding="utf-8")
    home_env.write_text("ENVIRONMENT=development\n", encoding="utf-8")
    environment: dict[str, str] = {}
    load_environment(
        project_env_path=project_env,
        home_env_path=home_env,
        environ=environment,
    )
    loaded = Settings(
        _env_file=None,
        SECRET_KEY="test-" * 8,
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        **environment,
    )
    monkeypatch.setattr(settings, "ENVIRONMENT", loaded.ENVIRONMENT)
    monkeypatch.setattr(
        settings,
        "DEV_ALLOW_ANONYMOUS_WEBSOCKETS",
        loaded.DEV_ALLOW_ANONYMOUS_WEBSOCKETS,
    )

    assert loaded.ENVIRONMENT == "development"
    assert loaded.DEV_ALLOW_ANONYMOUS_WEBSOCKETS is False
    with pytest.raises(WebSocketAuthError, match="Authentication token required"):
        await verify_websocket_token(None, AsyncMock(spec=JWTService))
