"""POST /api/v1/auth/register must answer 409, never 500, on a unique-index collision."""

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api.auth.auth_router import get_jwt_service, get_password_service
from src.api.main import app
from src.auth.models import TokenPair
from src.auth.password_service import PasswordService
from src.models.db.session import get_session
from src.models.db.user import User

PASSWORD = "Tr0ub4dor&3-horse-staple!"


def _body(email: str, username: str) -> dict[str, object]:
    return {
        "email": email,
        "username": username,
        "password": PASSWORD,
        "confirm_password": PASSWORD,
        "accept_terms": True,
    }


@pytest.fixture
async def register():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as seed:
        seed.add(
            User.create_with_password(
                email="gone@example.com",
                username="gone",
                password=PASSWORD,
                deleted_at=datetime.now(UTC),
            )
        )
        await seed.commit()

    jwt = MagicMock()
    jwt.generate_token_pair = AsyncMock(
        return_value=TokenPair(access_token="a", refresh_token="r", expires_in=60)
    )
    passwords = PasswordService(
        redis_client=AsyncMock(), bcrypt_rounds=4, check_breaches=False
    )

    async def session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_jwt_service] = lambda: jwt
    app.dependency_overrides[get_password_service] = lambda: passwords
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, factory
    for dependency in (get_session, get_jwt_service, get_password_service):
        app.dependency_overrides.pop(dependency, None)
    await engine.dispose()


async def test_new_email_registers(register) -> None:
    client, _ = register
    response = await client.post(
        "/api/v1/auth/register", json=_body("new@example.com", "newbie")
    )
    assert response.status_code == 201, response.text


async def test_soft_deleted_email_is_a_conflict_not_a_500(register) -> None:
    client, factory = register
    response = await client.post(
        "/api/v1/auth/register", json=_body("gone@example.com", "fresh")
    )
    assert response.status_code == 409, response.text
    async with factory() as db:
        assert len((await db.execute(select(User))).scalars().all()) == 1


async def test_soft_deleted_username_is_a_conflict_not_a_500(register) -> None:
    client, _ = register
    response = await client.post(
        "/api/v1/auth/register", json=_body("fresh@example.com", "gone")
    )
    assert response.status_code == 409, response.text


async def test_case_variant_of_a_taken_email_is_a_conflict(register) -> None:
    client, factory = register
    response = await client.post(
        "/api/v1/auth/register", json=_body("Gone@Example.com", "fresh")
    )
    assert response.status_code == 409, response.text
    async with factory() as db:
        assert len((await db.execute(select(User))).scalars().all()) == 1


def test_migration_creates_the_orm_declared_lower_indexes() -> None:
    orm = {
        index.name
        for index in User.__table__.indexes
        if index.unique and index.name and index.name.endswith("_lower")
    }
    assert orm == {"uq_users_email_lower", "uq_users_username_lower"}

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            "a1b2c3d4e5f6:b5d7e9f1a3c2",
            "--sql",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_URL": "postgresql://user:pass@localhost/db"},
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert "CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email))" in (
        result.stdout
    )
    assert (
        "CREATE UNIQUE INDEX uq_users_username_lower ON users (lower(username))"
        in result.stdout
    )
