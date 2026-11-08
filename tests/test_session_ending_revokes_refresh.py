"""Ending a session must end the refresh token too (issue #141).

Every case runs through ``src.api.main.app`` (auth middleware included) with a
real ``JWTService`` over an in-memory Redis stand-in and a SQLite user table.
"""

from __future__ import annotations

import fnmatch
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api.auth import auth_router
from src.auth.jwt_service import JWTService
from src.middleware import auth_middleware
from src.models.db.session import get_session
from src.models.db.user import User

PASSWORD = "Original-Passw0rd!"
NEW_PASSWORD = "Replacement-Passw0rd!"
ORG = str(uuid.uuid4())


class _MemoryRedis:
    """The subset of redis.asyncio the JWT service uses."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def setex(self, key: str, _ttl: Any, value: Any) -> bool:
        self.data[key] = value
        return True

    async def set(self, key: str, value: Any) -> bool:
        self.data[key] = value
        return True

    async def get(self, key: str) -> Any:
        return self.data.get(key)

    async def getdel(self, key: str) -> Any:
        return self.data.pop(key, None)

    async def delete(self, *keys: str) -> int:
        return sum(self.data.pop(key, None) is not None for key in keys)

    async def exists(self, key: str) -> int:
        return int(key in self.data)

    async def scan(
        self, cursor: int, match: str = "*", count: int = 100
    ) -> tuple[int, list[str]]:
        return 0, [key for key in list(self.data) if fnmatch.fnmatch(key, match)]


class _PasswordService:
    """Accepts any strong-enough password; the reset token names the user."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id

    async def __aenter__(self) -> _PasswordService:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def validate_reset_token(self, _token: str) -> str:
        return self.user_id

    def validate_password_strength(self, *_: Any, **__: Any) -> dict[str, Any]:
        return {"valid": True, "issues": []}

    async def check_password_history(self, *_: Any) -> bool:
        return False

    async def check_password_breach(self, *_: Any) -> bool:
        return False

    async def add_to_password_history(self, *_: Any) -> None:
        return None


@pytest_asyncio.fixture
async def auth_env() -> AsyncIterator[dict[str, Any]]:
    from src.api.main import app

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(User.__table__.create)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    user = User(
        email="owner@example.com",
        username="owner",
        hashed_password="placeholder",
        is_active=True,
    )
    user.update_password(PASSWORD)
    session.add(user)
    await session.commit()

    jwt_service = JWTService(redis_client=_MemoryRedis())  # type: ignore[arg-type]
    overrides = {
        auth_router.get_jwt_service: lambda: jwt_service,
        auth_middleware.get_jwt_service: lambda: jwt_service,
        auth_router.get_password_service: lambda: _PasswordService(str(user.id)),
        get_session: lambda: session,
    }
    app.dependency_overrides.update(overrides)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield {"client": client, "jwt": jwt_service, "user": user, "db": session}
    finally:
        for dependency in overrides:
            app.dependency_overrides.pop(dependency, None)
        await session.close()
        await engine.dispose()


async def _login(env: dict[str, Any], device_id: str = "device-1") -> Any:
    return await env["jwt"].generate_token_pair(
        user_id=str(env["user"].id),
        email=env["user"].email,
        device_id=device_id,
        organization_id=ORG,
    )


async def _refresh(env: dict[str, Any], refresh_token: str) -> int:
    response = await env["client"].post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    return response.status_code


def _bearer(tokens: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens.access_token}"}


@pytest.mark.asyncio
async def test_untouched_refresh_token_still_refreshes(auth_env) -> None:
    tokens = await _login(auth_env)

    assert await _refresh(auth_env, tokens.refresh_token) == 200


@pytest.mark.asyncio
async def test_logout_ends_the_sessions_refresh_token(auth_env) -> None:
    tokens = await _login(auth_env)

    response = await auth_env["client"].post(
        "/api/v1/auth/logout", headers=_bearer(tokens)
    )

    assert response.status_code == 204
    assert await _refresh(auth_env, tokens.refresh_token) == 401


@pytest.mark.asyncio
async def test_password_change_revokes_every_refresh_token(auth_env) -> None:
    current, other_device = await _login(auth_env), await _login(auth_env, "device-2")

    response = await auth_env["client"].post(
        "/api/v1/auth/change-password",
        headers=_bearer(current),
        json={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )

    assert response.status_code == 200, response.text
    assert await _refresh(auth_env, other_device.refresh_token) == 401


@pytest.mark.asyncio
async def test_password_reset_revokes_every_refresh_token(auth_env) -> None:
    stolen = await _login(auth_env)

    response = await auth_env["client"].post(
        "/api/v1/auth/reset-password",
        json={
            "token": "reset-token",
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )

    assert response.status_code == 200, response.text
    assert await _refresh(auth_env, stolen.refresh_token) == 401


@pytest.mark.asyncio
async def test_revoking_a_session_ends_its_refresh_token(auth_env) -> None:
    stolen, current = await _login(auth_env, "stolen"), await _login(auth_env, "mine")

    response = await auth_env["client"].delete(
        "/api/v1/auth/sessions/stolen", headers=_bearer(current)
    )

    assert response.status_code == 204, response.text
    assert await _refresh(auth_env, stolen.refresh_token) == 401
    assert await _refresh(auth_env, current.refresh_token) == 200


@pytest.mark.asyncio
async def test_refresh_refuses_a_deactivated_user(auth_env) -> None:
    tokens = await _login(auth_env)
    await auth_env["db"].execute(
        update(User).where(User.id == auth_env["user"].id).values(is_active=False)
    )
    await auth_env["db"].commit()

    assert await _refresh(auth_env, tokens.refresh_token) == 401
