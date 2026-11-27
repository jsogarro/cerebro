"""Password-service resources are owned, shared or released deterministically."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.auth import auth_router
from src.auth.jwt_service import JWTService
from src.core.config import get_settings


def _settings(**update):
    return get_settings().model_copy(update=update)


def _jwt(client) -> JWTService:
    service = MagicMock(spec=JWTService)
    service.redis_client = client
    return service


async def _open(jwt_service, settings, owned):
    with (
        patch.object(auth_router, "settings", settings),
        patch.object(auth_router.redis, "from_url", AsyncMock(return_value=owned)),
    ):
        agen = auth_router.get_password_service(_jwt(jwt_service))
        return agen, await agen.__anext__()


@pytest.mark.asyncio
async def test_shared_redis_client_is_reused_and_never_closed():
    shared, owned = AsyncMock(), AsyncMock()
    agen, service = await _open(shared, _settings(), owned)
    assert service.redis_client is shared
    with pytest.raises(StopAsyncIteration):
        await agen.__anext__()
    shared.close.assert_not_awaited()
    owned.close.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [None, RuntimeError("boom"), asyncio.CancelledError()])
async def test_owned_redis_client_is_closed_on_success_error_and_cancel(exc):
    owned = AsyncMock()
    agen, service = await _open(None, _settings(), owned)
    assert service.redis_client is owned
    owned.close.assert_not_awaited()
    if exc is None:
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()
    else:
        with pytest.raises(type(exc)):
            await agen.athrow(exc)
    owned.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [None, RuntimeError("boom"), asyncio.CancelledError()])
async def test_breach_http_client_is_closed_however_the_request_ends(exc):
    http = AsyncMock()
    _, service = await _open(AsyncMock(), _settings(CHECK_PASSWORD_BREACHES=True), None)
    with patch("src.auth.password_service.httpx.AsyncClient", return_value=http):
        try:
            async with service:
                assert service.http_client is http
                if exc:
                    raise exc
        except (RuntimeError, asyncio.CancelledError):
            pass
    http.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_repeated_requests_leave_no_http_clients_open():
    opened = []

    def make(**_):
        client = AsyncMock()
        opened.append(client)
        return client

    _, service = await _open(AsyncMock(), _settings(CHECK_PASSWORD_BREACHES=True), None)
    with patch("src.auth.password_service.httpx.AsyncClient", side_effect=make):
        for _ in range(5):
            async with service:
                pass
    assert len(opened) == 5
    assert all(c.aclose.await_count == 1 for c in opened)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_breach_toggle_decides_whether_the_breach_client_is_called(enabled):
    http = AsyncMock()
    http.get.return_value = MagicMock(status_code=200, text="")
    _, service = await _open(
        AsyncMock(), _settings(CHECK_PASSWORD_BREACHES=enabled), None
    )
    with patch("src.auth.password_service.httpx.AsyncClient", return_value=http):
        async with service:
            await service.check_password_breach("Correct-Horse-9!")
    assert http.get.await_count == (1 if enabled else 0)


class _ListRedis:
    def __init__(self):
        self.lists: dict[str, list] = {}

    async def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    async def ltrim(self, key, start, stop):
        self.lists[key] = self.lists[key][start : stop + 1]

    async def lrange(self, key, start, stop):
        return self.lists.get(key, [])[start : None if stop == -1 else stop + 1]

    async def expire(self, *_):
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,old_still_blocked", [(2, False), (3, True)])
async def test_history_limit_decides_which_old_passwords_are_still_refused(
    limit, old_still_blocked
):
    _, service = await _open(
        _ListRedis(), _settings(PASSWORD_HISTORY_LIMIT=limit, BCRYPT_ROUNDS=4), None
    )
    first = "First-Passw0rd-1!"
    for pw in (first, "Second-Passw0rd-2!", "Third-Passw0rd-3!"):
        await service.add_to_password_history("u1", service.hash_password(pw))
    assert await service.check_password_history("u1", first) is old_still_blocked


@pytest.mark.asyncio
async def test_unavailable_history_store_still_fails_closed():
    from src.auth.password_service import PasswordHistoryStoreUnavailableError

    _, service = await _open(None, _settings(), None)
    with pytest.raises(PasswordHistoryStoreUnavailableError):
        await service.check_password_history("u1", "Whatever-Passw0rd-1!")
