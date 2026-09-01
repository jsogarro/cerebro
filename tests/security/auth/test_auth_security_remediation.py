"""Regression tests for authentication lifecycle security controls."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

import src.api.auth.auth_router as auth_router
from src.api.main import app
from src.auth.models import PasswordResetConfirm, PasswordResetRequest, TokenPair
from src.auth.password_service import PasswordService
from src.middleware.auth_middleware import PUBLIC_ROUTE_ALLOWLIST


class _MemoryTokenStore:
    """Small Redis-shaped store for deterministic token lifecycle tests."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.get_calls: list[str] = []
        self.getdel_calls: list[str] = []
        self.delete_calls: list[str] = []

    async def ping(self) -> bool:
        return True

    async def setex(self, key: str, _expires_in: int, value: str) -> bool:
        self.values[key] = value
        return True

    async def get(self, key: str) -> str | None:
        self.get_calls.append(key)
        return self.values.get(key)

    async def getdel(self, key: str) -> str | None:
        self.getdel_calls.append(key)
        return self.values.pop(key, None)

    async def delete(self, key: str) -> int:
        self.delete_calls.append(key)
        return int(self.values.pop(key, None) is not None)


class _Database:
    """Minimal database seam used by direct auth endpoint tests."""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_logout_returns_service_unavailable_when_revocation_fails() -> None:
    """Logout must not return 204 when the blacklist write fails."""
    jwt_service = SimpleNamespace(revoke_token=AsyncMock(return_value=False))
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials="access-token"
    )

    with pytest.raises(HTTPException) as raised:
        await auth_router.logout(credentials, jwt_service)

    assert raised.value.status_code == 503
    assert raised.value.detail == "Authentication service unavailable"


@pytest.mark.asyncio
async def test_reset_password_reports_store_unavailable_instead_of_success() -> None:
    """Reset must expose a store outage rather than accepting an uncheckable token."""
    service = PasswordService(redis_client=None, check_breaches=False)
    request = PasswordResetConfirm(
        token="reset-token",
        new_password="NewSecurePass123!",
        confirm_password="NewSecurePass123!",
    )

    with pytest.raises(HTTPException) as raised:
        await auth_router.reset_password(request, _Database(), service)

    assert raised.value.status_code == 503
    assert raised.value.detail == "Password recovery service unavailable"


@pytest.mark.asyncio
async def test_forgot_password_reports_store_unavailable_before_lookup() -> None:
    """Recovery does not reveal an account by succeeding while its store is down."""
    service = PasswordService(redis_client=None, check_breaches=False)
    request = PasswordResetRequest(email="user@example.com")

    with pytest.raises(HTTPException) as raised:
        await auth_router.forgot_password(
            request, BackgroundTasks(), _Database(), service
        )

    assert raised.value.status_code == 503
    assert raised.value.detail == "Password recovery service unavailable"


@pytest.mark.asyncio
async def test_reset_token_storage_fails_closed_without_redis() -> None:
    """The token service must not silently drop a reset token."""
    service = PasswordService(redis_client=None, check_breaches=False)

    with pytest.raises(RuntimeError):
        await service.store_reset_token("user-123", "reset-token")


@pytest.mark.asyncio
async def test_email_verification_consumes_real_token_and_updates_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid stored verification token marks exactly its user as verified."""
    user_id = UUID("00000000-0000-0000-0000-000000000123")
    user = SimpleNamespace(id=user_id, is_verified=False)
    database = _Database()
    store = _MemoryTokenStore()
    service = PasswordService(redis_client=store, check_breaches=False)
    token = service.generate_verification_token()
    await service.store_verification_token(str(user_id), token)

    class _UserRepository:
        def __init__(self, _db: Any) -> None:
            pass

        async def verify_email(self, requested_user_id: UUID) -> Any:
            assert requested_user_id == user_id
            user.is_verified = True
            return user

    monkeypatch.setattr(auth_router, "UserRepository", _UserRepository)

    response = await auth_router.verify_email(token, database, service)

    assert response == {"message": "Email verified successfully"}
    assert user.is_verified is True
    assert database.commits == 1
    assert store.values == {}
    assert store.getdel_calls == [f"{service.verification_token_prefix}{token}"]
    assert store.get_calls == []
    assert store.delete_calls == []


@pytest.mark.asyncio
async def test_reset_token_consumption_is_atomic_and_single_use() -> None:
    """A reset token is read-and-deleted as one Redis operation."""
    store = _MemoryTokenStore()
    service = PasswordService(redis_client=store, check_breaches=False)
    token = "reset-token"
    await service.store_reset_token("user-123", token)

    assert await service.validate_reset_token(token) == "user-123"
    assert await service.validate_reset_token(token) is None
    assert store.getdel_calls == [f"{service.reset_token_prefix}{token}"] * 2
    assert store.get_calls == []
    assert store.delete_calls == []


@pytest.mark.asyncio
async def test_email_verification_rejects_unknown_token() -> None:
    """An invalid verification token must not claim that an email was verified."""
    service = PasswordService(redis_client=_MemoryTokenStore(), check_breaches=False)

    with pytest.raises(HTTPException) as raised:
        await auth_router.verify_email("not-a-token", _Database(), service)

    assert raised.value.status_code == 400
    assert raised.value.detail == "Invalid or expired verification token"


@pytest.mark.asyncio
async def test_email_verification_reports_store_unavailable() -> None:
    """Verification must fail explicitly when its token store is unavailable."""
    service = PasswordService(redis_client=None, check_breaches=False)

    with pytest.raises(HTTPException) as raised:
        await auth_router.verify_email("verification-token", _Database(), service)

    assert raised.value.status_code == 503
    assert raised.value.detail == "Email verification service unavailable"


def test_refresh_is_public_and_works_without_access_bearer() -> None:
    """Refresh uses its refresh credential at the real HTTP entry point."""
    assert ("POST", "/api/v1/auth/refresh") in PUBLIC_ROUTE_ALLOWLIST

    expected = TokenPair(
        access_token="new-access-token",
        refresh_token="new-refresh-token",
        expires_in=900,
    )
    service = SimpleNamespace(
        refresh_tokens=AsyncMock(return_value=expected),
    )
    dependency = auth_router.get_jwt_service
    previous = app.dependency_overrides.get(dependency)
    app.dependency_overrides[dependency] = lambda: service

    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "stored-refresh-token"},
        )
        client.close()
    finally:
        if previous is None:
            app.dependency_overrides.pop(dependency, None)
        else:
            app.dependency_overrides[dependency] = previous

    assert response.status_code == 200
    assert response.json() == expected.model_dump()
    service.refresh_tokens.assert_awaited_once_with(
        refresh_token="stored-refresh-token", device_id=None
    )
