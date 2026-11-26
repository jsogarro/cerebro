"""get_password_service applies its settings and releases the Redis client."""

from unittest.mock import AsyncMock, patch

import pytest

from src.api.auth import auth_router
from src.core.config import get_settings


@pytest.mark.asyncio
async def test_password_settings_are_applied_and_redis_is_closed() -> None:
    client = AsyncMock()
    settings = get_settings().model_copy(
        update={"CHECK_PASSWORD_BREACHES": False, "PASSWORD_HISTORY_LIMIT": 9}
    )
    with (
        patch.object(auth_router, "settings", settings),
        patch.object(auth_router.redis, "from_url", AsyncMock(return_value=client)),
    ):
        agen = auth_router.get_password_service()
        service = await agen.__anext__()
        assert service.check_breaches is False
        assert service.password_history_limit == 9
        client.close.assert_not_awaited()
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()

    client.close.assert_awaited_once()
