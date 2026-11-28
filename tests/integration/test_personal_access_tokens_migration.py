"""The personal-access-tokens migration enforces what the ORM and RLS declare."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import CheckConstraint, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.models.db.base import Base
from src.models.db.personal_access_token import PersonalAccessToken  # noqa: F401
from src.models.db.user import User

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = Path(sys.executable).parent / "alembic"
TABLE_NAME = "personal_access_tokens"


def _run(command: list[str], url: str, **env: str) -> str:
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": url, **env},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return result.stdout


def _alembic(url: str, *args: str) -> str:
    return _run([str(ALEMBIC), *args], url)


@pytest.fixture(name="server", scope="module")
def server_fixture() -> Iterator[PostgresContainer]:
    with PostgresContainer(image="postgres:16-alpine") as container:
        yield container


@pytest.fixture(name="url", scope="module")
def url_fixture(server: PostgresContainer) -> str:
    host, port = server.get_container_host_ip(), server.get_exposed_port(5432)
    return f"postgresql://{server.username}:{server.password}@{host}:{port}/{server.dbname}"


@pytest.fixture(name="migrated", scope="module")
def migrated_fixture(url: str) -> str:
    _run(
        [sys.executable, "docker/apply_roles.py"],
        url,
        ROLES_ADMIN_DATABASE_URL=url,
        CEREBRO_MIGRATOR_PASSWORD="migrator-pw",
        CEREBRO_APP_PASSWORD="app-pw",
        CEREBRO_MAINT_PASSWORD="maint-pw",
    )
    _alembic(url, "upgrade", "head")
    _run(
        [sys.executable, "docker/apply_roles.py"],
        url,
        ROLES_ADMIN_DATABASE_URL=url,
        CEREBRO_MIGRATOR_PASSWORD="migrator-pw",
        CEREBRO_APP_PASSWORD="app-pw",
        CEREBRO_MAINT_PASSWORD="maint-pw",
    )
    return url


@pytest_asyncio.fixture(name="connection")
async def connection_fixture(migrated: str) -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()
    await engine.dispose()


def test_there_is_exactly_one_head() -> None:
    heads = [
        line for line in _alembic("postgresql://x/y", "heads").splitlines() if line
    ]
    assert len(heads) == 1


async def test_table_has_forced_rls_and_maintenance_policies(
    connection: AsyncConnection,
) -> None:
    rows = await connection.execute(
        text(
            "SELECT policyname FROM pg_policies "
            "WHERE schemaname='public' AND tablename = :t"
        ),
        {"t": TABLE_NAME},
    )
    policies = {r[0] for r in rows}
    assert {
        f"tenant_isolation_{TABLE_NAME}",
        f"maintenance_select_{TABLE_NAME}",
        f"maintenance_update_{TABLE_NAME}",
    } <= policies

    row = (
        await connection.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = :t"
            ),
            {"t": TABLE_NAME},
        )
    ).one()
    assert tuple(row) == (True, True)


async def test_every_orm_check_exists_in_the_migrated_database(
    connection: AsyncConnection,
) -> None:
    table = Base.metadata.tables[TABLE_NAME]
    declared = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }
    rows = await connection.execute(
        text(
            "SELECT con.conname FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "WHERE con.contype = 'c' AND rel.relname = :t"
        ),
        {"t": TABLE_NAME},
    )
    migrated = {r[0] for r in rows}
    assert declared and declared <= migrated


async def test_every_orm_index_exists_in_the_migrated_database(
    connection: AsyncConnection,
) -> None:
    table = Base.metadata.tables[TABLE_NAME]
    declared = {index.name for index in table.indexes}
    rows = await connection.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname='public' AND tablename = :t"
        ),
        {"t": TABLE_NAME},
    )
    assert declared <= {r[0] for r in rows}


async def _insert_user(
    connection: AsyncConnection, org_id: uuid.UUID | None = None
) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with AsyncSession(bind=connection, expire_on_commit=False) as session:
        session.add(
            User(
                id=user_id,
                email=f"{user_id}@example.com",
                username=str(user_id),
                hashed_password="x",
                organization_id=org_id or uuid.uuid4(),
            )
        )
        await session.flush()
    return user_id


async def _insert_pat(
    connection: AsyncConnection, **override: object
) -> tuple[uuid.UUID, str]:
    org = override.pop("org", uuid.uuid4())
    usr = override.pop("usr", None) or await _insert_user(
        connection, org if isinstance(org, uuid.UUID) else None
    )
    pat_id = uuid.uuid4()
    token_hash = str(override.pop("token_hash", uuid.uuid4().hex * 2))
    values = {
        "id": pat_id,
        "org": org,
        "usr": usr,
        "name": "ci",
        "token_hash": token_hash,
        "token_prefix": "crb_pat_12345678",
        "scopes": '["mcp:read"]',
        "issued_at_ns": 1000,
        "expires_offset": timedelta(days=1),
        **override,
    }
    await connection.execute(
        text(
            "INSERT INTO personal_access_tokens (id, organization_id, user_id, name, "
            "token_hash, token_prefix, scopes, issued_at_ns, expires_at) "
            "VALUES (:id, :org, :usr, :name, :token_hash, :token_prefix, "
            "CAST(:scopes AS json), :issued_at_ns, now() + CAST(:expires_offset AS interval))"
        ),
        values,
    )
    return pat_id, token_hash


@pytest.mark.parametrize(
    "override",
    [
        {"org": None},
        {"name": "   "},
        {"token_hash": "short"},
        {"token_prefix": ""},
        {"issued_at_ns": 0},
        {"expires_offset": timedelta(hours=-1)},
    ],
)
async def test_pat_checks_reject_bad_rows(
    connection: AsyncConnection, override: dict[str, object]
) -> None:
    with pytest.raises(IntegrityError):
        await _insert_pat(connection, **override)


async def test_deleting_user_cascades_to_pats(connection: AsyncConnection) -> None:
    org = uuid.uuid4()
    user_id = await _insert_user(connection, org)
    await _insert_pat(connection, org=org, usr=user_id)
    await connection.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
    count = await connection.scalar(
        text("SELECT count(*) FROM personal_access_tokens WHERE user_id = :u"),
        {"u": user_id},
    )
    assert count == 0


async def test_app_and_maintenance_roles_enforce_least_privilege(
    migrated: str,
) -> None:
    rest = migrated.split("@", 1)[1]
    admin = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    app_engine = create_async_engine(f"postgresql+asyncpg://cerebro_app:app-pw@{rest}")
    maint_engine = create_async_engine(
        f"postgresql+asyncpg://cerebro_maint:maint-pw@{rest}"
    )
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    try:
        async with admin.begin() as conn:
            user_a = await _insert_user(conn, org_a)
            user_b = await _insert_user(conn, org_b)
            pat_a, hash_a = await _insert_pat(conn, org=org_a, usr=user_a)
            await _insert_pat(conn, org=org_b, usr=user_b)

        # cerebro_app sees 0 rows without tenant, and only Org A with Org A set.
        async with app_engine.begin() as conn:
            assert (
                await conn.scalar(text("SELECT count(*) FROM personal_access_tokens"))
                == 0
            )
            await conn.execute(
                text("SELECT set_config('app.current_org_id', :o, true)"),
                {"o": str(org_a)},
            )
            assert (
                await conn.scalar(text("SELECT count(*) FROM personal_access_tokens"))
                == 1
            )

        # cerebro_maint can read by hash joined with users and update last_used_at,
        # but cannot modify organization_id/revoked_at or delete.
        async with maint_engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT p.id, u.is_active FROM personal_access_tokens p "
                        "JOIN users u ON u.id = p.user_id WHERE p.token_hash = :h"
                    ),
                    {"h": hash_a},
                )
            ).one()
            assert row[0] == pat_a and row[1] is True
            await conn.execute(
                text(
                    "UPDATE personal_access_tokens SET last_used_at = now() "
                    "WHERE id = :id"
                ),
                {"id": pat_a},
            )
        async with maint_engine.begin() as conn:
            with pytest.raises(DBAPIError, match="permission denied"):
                await conn.execute(
                    text(
                        "UPDATE personal_access_tokens SET organization_id = :b "
                        "WHERE id = :id"
                    ),
                    {"b": org_b, "id": pat_a},
                )
        async with maint_engine.begin() as conn:
            with pytest.raises(DBAPIError, match="permission denied"):
                await conn.execute(
                    text("DELETE FROM personal_access_tokens WHERE id = :id"),
                    {"id": pat_a},
                )
    finally:
        async with admin.begin() as conn:
            await conn.execute(
                text("DELETE FROM users WHERE organization_id IN (:a, :b)"),
                {"a": org_a, "b": org_b},
            )
        await admin.dispose()
        await app_engine.dispose()
        await maint_engine.dispose()


async def test_downgrade_removes_table_and_policies(url: str) -> None:
    _alembic(url, "upgrade", "head")
    _alembic(url, "downgrade", "d5a1c8e2b7f3")
    engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with engine.connect() as connection:
            tables = {
                r[0]
                for r in await connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                )
            }
            assert TABLE_NAME not in tables
    finally:
        await engine.dispose()
        _alembic(url, "upgrade", "head")
