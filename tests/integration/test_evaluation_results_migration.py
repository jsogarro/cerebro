"""The evaluation-results migration enforces what the ORM declares.

The SQLite schema test proves the ORM's CHECKs; these prove the migration
(which spells them out separately) rejects the same rows, and that the
append-only trigger holds against raw SQL, which the ORM guard cannot see.
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import CheckConstraint, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.models.db import *  # noqa: F403  (register every table on Base.metadata)
from src.models.db.base import Base
from src.models.db.evaluation_result import AgentEvaluationResult
from tests.unit.models.db.evaluation_result_cases import (
    ORG_ID,
    VIOLATIONS,
    valid_row,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = Path(sys.executable).parent / "alembic"
TABLE = "agent_evaluation_results"
PARENT_REVISION = "b8e3f1a5c7d9"


def _alembic(url: str, *args: str) -> str:
    result = subprocess.run(
        [str(ALEMBIC), *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return result.stdout


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
    _alembic(url, "upgrade", "head")
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


async def test_every_orm_check_and_index_exists_in_the_migrated_database(
    connection: AsyncConnection,
) -> None:
    table = Base.metadata.tables[TABLE]
    declared_checks = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }
    checks = await connection.execute(
        text(
            "SELECT con.conname FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "WHERE con.contype = 'c' AND rel.relname = :t"
        ),
        {"t": TABLE},
    )
    assert declared_checks and declared_checks == {r[0] for r in checks}

    indexes = await connection.execute(
        text("SELECT indexname FROM pg_indexes WHERE tablename = :t"), {"t": TABLE}
    )
    assert {index.name for index in table.indexes} <= {r[0] for r in indexes}


async def _insert(connection: AsyncConnection, **overrides: object) -> None:
    async with AsyncSession(bind=connection, expire_on_commit=False) as session:
        session.add(AgentEvaluationResult(**valid_row(**overrides)))
        await session.flush()


@pytest_asyncio.fixture(name="seeded")
async def seeded_fixture(connection: AsyncConnection) -> None:
    await connection.execute(
        text(
            "INSERT INTO agent_runs (id, run_id, tenant_id, organization_id, "
            "workflow_definition_id, workflow_definition_version, "
            "routing_policy_id, routing_policy_version, idempotency_key, "
            "requested_by, status, created_at, updated_at) VALUES "
            "(gen_random_uuid(), 'run-1', 'tenant-1', :org, 'research', '1', "
            "'default', '1', 'submit-1', 'user-1', 'created', now(), now())"
        ),
        {"org": ORG_ID},
    )


@pytest.mark.usefixtures("seeded")
async def test_a_valid_row_is_accepted(connection: AsyncConnection) -> None:
    await _insert(connection)


@pytest.mark.usefixtures("seeded")
@pytest.mark.parametrize(("constraint", "overrides"), VIOLATIONS)
async def test_the_migration_rejects_what_the_orm_declares_invalid(
    connection: AsyncConnection, constraint: str, overrides: dict[str, object]
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        await _insert(connection, **overrides)


@pytest.mark.usefixtures("seeded")
async def test_the_trigger_rejects_raw_update_and_delete(
    connection: AsyncConnection,
) -> None:
    await _insert(connection)
    for statement in (
        f"UPDATE {TABLE} SET status = 'failed'",
        f"DELETE FROM {TABLE}",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with connection.begin_nested():
                await connection.execute(text(statement))


async def test_the_table_has_a_tenant_rls_policy(connection: AsyncConnection) -> None:
    rows = await connection.execute(
        text("SELECT policyname FROM pg_policies WHERE tablename = :t"), {"t": TABLE}
    )
    assert {r[0] for r in rows} == {f"tenant_isolation_{TABLE}"}


async def test_downgrade_removes_the_table(url: str) -> None:
    _alembic(url, "upgrade", "head")
    _alembic(url, "downgrade", PARENT_REVISION)
    engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with engine.connect() as connection:
            tables = await connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            )
            assert TABLE not in {r[0] for r in tables}
    finally:
        await engine.dispose()
    _alembic(url, "upgrade", "head")
