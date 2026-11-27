"""Refresh mints from the account as it is now, never from the old token."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import jwt
import pytest
from sqlalchemy import update

from src.models.db.user import User
from src.repositories.user_repository import UserRepository
from tests.test_session_ending_revokes_refresh import (  # noqa: F401  (fixture)
    ORG,
    _login,
    _refresh,
    auth_env,
)


async def _set(env, **values) -> None:
    await env["db"].execute(
        update(User).where(User.id == env["user"].id).values(**values)
    )
    await env["db"].commit()


def _claims(env, token) -> dict:
    jwt_service = env["jwt"]
    return jwt.decode(token, jwt_service.public_key, algorithms=[jwt_service.algorithm])


async def _refreshed(env, refresh_token):
    return await env["client"].post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )


@pytest.fixture
async def member(auth_env):  # noqa: F811  (the imported fixture)
    await _set(auth_env, organization_id=uuid.UUID(ORG))
    return auth_env


@pytest.mark.asyncio
async def test_changed_email_role_and_organization_propagate(member) -> None:
    tokens = await _login(member)
    new_org = uuid.uuid4()
    await _set(
        member, email="moved@example.com", role="analyst", organization_id=new_org
    )

    response = await _refreshed(member, tokens.refresh_token)

    assert response.status_code == 200, response.text
    claims = _claims(member, response.json()["access_token"])
    assert claims["email"] == "moved@example.com"
    assert claims["roles"] == ["analyst"]
    assert claims["organization_id"] == str(new_org)
    assert claims["permissions"] == []


@pytest.mark.asyncio
async def test_superuser_grant_and_revocation_follow_the_account(member) -> None:
    tokens = await _login(member)
    await _set(member, is_superuser=True)
    granted = _claims(
        member, (await _refreshed(member, tokens.refresh_token)).json()["access_token"]
    )
    assert "admin" in granted["roles"] and granted["permissions"] == ["*"]

    again = (await _refreshed(member, tokens.refresh_token)).status_code
    assert again == 401  # rotated: the old token is spent

    fresh = await _login(member)
    await _set(member, is_superuser=False)
    revoked = _claims(
        member, (await _refreshed(member, fresh.refresh_token)).json()["access_token"]
    )
    assert "admin" not in revoked["roles"] and revoked["permissions"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [{"is_active": False}, {"organization_id": None}],
    ids=["disabled", "no-org"],
)
async def test_unusable_account_cannot_refresh_and_learns_nothing(
    member, change
) -> None:
    tokens = await _login(member)
    await _set(member, **change)

    response = await _refreshed(member, tokens.refresh_token)

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid refresh token"
    assert member["user"].email not in response.text
    if "organization_id" in change:  # refresh must not provision one
        stored = await member["db"].get(User, member["user"].id)
        await member["db"].refresh(stored)
        assert stored.organization_id is None


@pytest.mark.asyncio
async def test_deleted_account_cannot_refresh(member) -> None:
    tokens = await _login(member)
    await _set(member, deleted_at=datetime.now(UTC))

    response = await _refreshed(member, tokens.refresh_token)

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid refresh token"


@pytest.mark.asyncio
async def test_storage_failure_is_unavailable_and_keeps_the_token(
    member, monkeypatch
) -> None:
    tokens = await _login(member)

    async def broken(self, *_, **__):
        raise RuntimeError("db password=hunter2 down")

    monkeypatch.setattr(UserRepository, "get", broken)
    response = await _refreshed(member, tokens.refresh_token)
    monkeypatch.undo()

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Authentication service unavailable"
    assert "hunter2" not in response.text
    assert await _refresh(member, tokens.refresh_token) == 200  # not consumed


@pytest.mark.asyncio
async def test_concurrent_refresh_of_one_token_rotates_once(member) -> None:
    tokens = await _login(member)

    responses = await asyncio.gather(
        *[_refreshed(member, tokens.refresh_token) for _ in range(4)]
    )

    assert sorted(r.status_code for r in responses) == [200, 401, 401, 401]
