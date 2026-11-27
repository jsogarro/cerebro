"""get_password_service applies its settings."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.auth import auth_router
from src.auth.jwt_service import JWTService
from src.core.config import get_settings


@pytest.mark.asyncio
async def test_password_settings_are_applied() -> None:
    jwt_service = MagicMock(spec=JWTService)
    jwt_service.redis_client = AsyncMock()
    settings = get_settings().model_copy(
        update={"CHECK_PASSWORD_BREACHES": False, "PASSWORD_HISTORY_LIMIT": 9}
    )
    with patch.object(auth_router, "settings", settings):
        agen = auth_router.get_password_service(jwt_service)
        service = await agen.__anext__()
        assert service.check_breaches is False
        assert service.password_history_limit == 9
        await agen.aclose()
