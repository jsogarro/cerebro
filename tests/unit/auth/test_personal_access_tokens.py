"""Unit tests for personal access token generation, repository, and validation."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.auth.jwt_service import JWTService
from src.auth.models import (
    PAT_PREFIX,
    PAT_SCOPE_READ,
    PAT_SCOPE_START,
    PersonalAccessTokenCreateRequest,
    PersonalAccessTokenPayload,
    TokenPayload,
)
from src.auth.pat_service import (
    PATServiceUnavailableError,
    PATValidationError,
    PersonalAccessTokenService,
    generate_personal_access_token,
    hash_personal_access_token,
)
from src.models.db.base import Base
from src.models.db.personal_access_token import PersonalAccessToken
from src.models.db.user import User
from src.repositories.personal_access_token_repository import (
    PersonalAccessTokenRepository,
)
from src.repositories.tenant_scope import MissingOrganizationContextError


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str) -> bool:
        self._store[key] = value
        return True

    async def setex(self, key: str, _ttl: Any, value: str) -> bool:
        self._store[key] = value
        return True

    async def exists(self, key: str) -> int:
        return 1 if key in self._store else 0

    async def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0

    async def scan(
        self, cursor: int = 0, match: str | None = None, count: int = 100
    ) -> tuple[int, list[str]]:
        prefix = (match or "").rstrip("*")
        keys = [key for key in self._store if key.startswith(prefix)]
        return 0, keys


async def _make_session_factory() -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[User.__table__, PersonalAccessToken.__table__],
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_user(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID | None,
    role: str = "researcher",
    is_active: bool = True,
    is_superuser: bool = False,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        username=f"user_{uuid.uuid4().hex[:8]}",
        hashed_password="x",
        organization_id=org_id,
        role=role,
        is_active=is_active,
        is_superuser=is_superuser,
    )
    async with factory() as session:
        session.add(user)
        await session.commit()
    return user


def test_generate_pat_uses_prefix_high_entropy_and_sha256_hash() -> None:
    plaintext, digest, prefix = generate_personal_access_token()
    assert plaintext.startswith(PAT_PREFIX)
    # 32 random bytes base64url-encoded is 43 characters after the prefix.
    assert len(plaintext) >= len(PAT_PREFIX) + 43
    assert digest == hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    assert len(digest) == 64
    assert digest != plaintext
    assert prefix == plaintext[:16]
    assert hash_personal_access_token(plaintext) == digest

    second, _, _ = generate_personal_access_token()
    assert second != plaintext


def test_pat_create_request_enforces_expiry_and_scopes() -> None:
    default_req = PersonalAccessTokenCreateRequest(name="claude", expires_in_days=30)
    assert default_req.scopes == [PAT_SCOPE_READ]

    opt_in = PersonalAccessTokenCreateRequest(
        name="runner",
        expires_in_days=90,
        scopes=[PAT_SCOPE_READ, PAT_SCOPE_START, PAT_SCOPE_READ],
    )
    assert opt_in.scopes == [PAT_SCOPE_READ, PAT_SCOPE_START]

    with pytest.raises(ValidationError):
        PersonalAccessTokenCreateRequest(name="missing-expiry")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        PersonalAccessTokenCreateRequest(name="zero", expires_in_days=0)
    with pytest.raises(ValidationError):
        PersonalAccessTokenCreateRequest(name="too-long", expires_in_days=91)
    with pytest.raises(ValidationError):
        PersonalAccessTokenCreateRequest(name="   ", expires_in_days=7)
    with pytest.raises(ValidationError):
        PersonalAccessTokenCreateRequest(name="empty", expires_in_days=7, scopes=[])
    with pytest.raises(ValidationError):
        PersonalAccessTokenCreateRequest(
            name="bad-scope", expires_in_days=7, scopes=["admin:all"]
        )


def test_pat_payload_is_distinct_from_jwt_token_payload() -> None:
    now = datetime.now(UTC)
    payload = PersonalAccessTokenPayload(
        sub=str(uuid.uuid4()),
        email="alice@example.com",
        roles=["researcher"],
        permissions=[],
        organization_id=str(uuid.uuid4()),
        pat_id=str(uuid.uuid4()),
        scopes=[PAT_SCOPE_READ],
        iat=now,
        exp=now + timedelta(days=7),
    )
    assert not isinstance(payload, TokenPayload)
    assert payload.has_scope(PAT_SCOPE_READ)
    assert not payload.has_scope(PAT_SCOPE_START)


async def test_repository_enforces_tenancy_and_hides_secrets() -> None:
    engine, factory = await _make_session_factory()
    try:
        org_a, org_b = uuid.uuid4(), uuid.uuid4()
        alice = await _seed_user(factory, org_id=org_a)
        bob_admin = await _seed_user(factory, org_id=org_b, is_superuser=True)
        now = datetime.now(UTC)
        plaintext, digest, prefix = generate_personal_access_token()

        async with factory() as session:
            repo = PersonalAccessTokenRepository(session)
            with pytest.raises(MissingOrganizationContextError):
                await repo.create(
                    user_id=alice.id,
                    organization_id=None,
                    name="no-org",
                    token_hash=digest,
                    token_prefix=prefix,
                    scopes=[PAT_SCOPE_READ],
                    issued_at_ns=1,
                    created_at=now,
                    expires_at=now + timedelta(days=7),
                )

            with pytest.raises(ValueError, match="90"):
                await repo.create(
                    user_id=alice.id,
                    organization_id=org_a,
                    name="too-long",
                    token_hash=digest,
                    token_prefix=prefix,
                    scopes=[PAT_SCOPE_READ],
                    issued_at_ns=1,
                    created_at=now,
                    expires_at=now + timedelta(days=91),
                )

            created = await repo.create(
                user_id=alice.id,
                organization_id=org_a,
                name="alice-cli",
                token_hash=digest,
                token_prefix=prefix,
                scopes=[PAT_SCOPE_READ],
                issued_at_ns=100,
                created_at=now,
                expires_at=now + timedelta(days=30),
            )
            await session.commit()
            assert created.token_hash == digest
            assert created.token_hash != plaintext

        async with factory() as session:
            repo = PersonalAccessTokenRepository(session)
            # Org B admin cannot revoke Org A's PAT even with user_id=None.
            foreign = await repo.revoke(created.id, organization_id=org_b, user_id=None)
            assert foreign is None

            listed_a = await repo.list_for_user(alice.id, organization_id=org_a)
            assert [row.id for row in listed_a] == [created.id]
            assert listed_a[0].revoked_at is None

            listed_b = await repo.list_for_user(bob_admin.id, organization_id=org_b)
            assert listed_b == []

            revoked = await repo.revoke(
                created.id, organization_id=org_a, user_id=alice.id
            )
            assert revoked is not None and revoked.revoked_at is not None
            await session.commit()
    finally:
        await engine.dispose()


async def test_validate_token_checks_revocation_expiry_user_active_org_and_roles() -> (
    None
):
    engine, factory = await _make_session_factory()
    try:
        org_a = uuid.uuid4()
        user = await _seed_user(factory, org_id=org_a, role="researcher")
        redis_fake = _FakeRedis()
        jwt_service = JWTService(redis_client=redis_fake)  # type: ignore[arg-type]
        pat_service = PersonalAccessTokenService(session_factory=factory)

        plaintext, digest, prefix = generate_personal_access_token()
        now = datetime.now(UTC)
        async with factory() as session:
            row = await PersonalAccessTokenRepository(session).create(
                user_id=user.id,
                organization_id=org_a,
                name="dev",
                token_hash=digest,
                token_prefix=prefix,
                scopes=[PAT_SCOPE_READ],
                issued_at_ns=1_000_000,
                created_at=now,
                expires_at=now + timedelta(days=10),
            )
            await session.commit()
            pat_id = row.id

        # Valid call resolves roles at request time and records last_used_at.
        payload = await pat_service.validate_token(
            plaintext, jwt_service=jwt_service, now=now + timedelta(minutes=5)
        )
        assert isinstance(payload, PersonalAccessTokenPayload)
        assert payload.sub == str(user.id)
        assert payload.organization_id == str(org_a)
        assert payload.pat_id == str(pat_id)
        assert payload.scopes == [PAT_SCOPE_READ]
        assert payload.roles == ["researcher"]

        async with factory() as session:
            refreshed = await session.get(PersonalAccessToken, pat_id)
            assert refreshed is not None and refreshed.last_used_at is not None

        # Role changes on User take effect immediately on next PAT validation.
        async with factory() as session:
            db_user = await session.get(User, user.id)
            assert db_user is not None
            db_user.role = "lead"
            db_user.is_superuser = True
            await session.commit()

        updated_payload = await pat_service.validate_token(
            plaintext, jwt_service=jwt_service, now=now + timedelta(minutes=6)
        )
        assert updated_payload.roles == ["lead", "admin"]
        assert updated_payload.permissions == ["*"]

        # Expired token is rejected.
        with pytest.raises(PATValidationError):
            await pat_service.validate_token(
                plaintext, jwt_service=jwt_service, now=now + timedelta(days=11)
            )

        # Inactive user is rejected.
        async with factory() as session:
            db_user = await session.get(User, user.id)
            assert db_user is not None
            db_user.is_active = False
            await session.commit()
        with pytest.raises(PATValidationError):
            await pat_service.validate_token(
                plaintext, jwt_service=jwt_service, now=now + timedelta(minutes=7)
            )

        # Re-activate user, then trigger user-wide revocation marker.
        async with factory() as session:
            db_user = await session.get(User, user.id)
            assert db_user is not None
            db_user.is_active = True
            await session.commit()
        await jwt_service.revoke_all_user_tokens(str(user.id))
        with pytest.raises(PATValidationError):
            await pat_service.validate_token(
                plaintext, jwt_service=jwt_service, now=now + timedelta(minutes=8)
            )

        # Missing Redis fails closed with PATServiceUnavailableError.
        no_redis_jwt = JWTService(redis_client=None)
        with pytest.raises(PATServiceUnavailableError):
            await pat_service.validate_token(
                plaintext, jwt_service=no_redis_jwt, now=now + timedelta(minutes=8)
            )
    finally:
        await engine.dispose()
