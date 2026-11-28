"""WebSocket authentication has no anonymous bypass."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.api.websocket.auth import (
    WebSocketAuthError,
    verify_project_access,
    verify_websocket_token,
)
from src.auth.jwt_service import JWTService
from src.core.config import Settings, settings


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["development", "production"])
async def test_anonymous_websocket_is_refused_in_every_environment(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", environment)

    with pytest.raises(WebSocketAuthError, match="Authentication token required"):
        await verify_websocket_token(None, AsyncMock(spec=JWTService))
    assert (
        await verify_project_access(
            None, "project-1", organization_id=None, session=None
        )
        is False
    )


def test_the_anonymous_websocket_setting_no_longer_exists() -> None:
    assert "DEV_ALLOW_ANONYMOUS_WEBSOCKETS" not in Settings.model_fields
