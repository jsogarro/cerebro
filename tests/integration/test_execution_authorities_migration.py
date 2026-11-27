"""The authorities migration is additive and enforces what the ORM declares."""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import CheckConstraint, UniqueConstraint, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.models.db.base import Base
from src.models.db.execution_authority import AgentExecutionAuthority  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = Path(sys.executable).parent / "alembic"
TABLE = "agent_execution_authorities"
REVISION = "b5e1c8d3a9f7"
PARENT = "a7d2e9c4b1f8"


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


@pytest.fixture(name="url", scope="module")
def url_fixture() -> Iterator[str]:
    with PostgresContainer(image="postgres:16-alpine") as server:
        host, port = server.get_container_host_ip(), server.get_exposed_port(5432)
        yield (
            f"postgresql://{server.username}:{server.password}"
            f"@{host}:{port}/{server.dbname}"
        )


@pytest.fixture(name="migrated", scope="module")
def migrated_fixture(url: str) -> str:
    _alembic(url, "upgrade", PARENT)
    return url


@pytest_asyncio.fixture(name="connection")
async def connection_fixture(migrated: str) -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(
        migrated.replace("postgresql://", "postgresql+asyncpg://")
    )
    async with engine.connect() as connection:
        yield connection
    await engine.dispose()


def test_there_is_one_head_and_it_chains_on_the_critiques_revision() -> None:
    heads = [
        line for line in _alembic("postgresql://x/y", "heads").splitlines() if line
    ]
    assert len(heads) == 1
    assert heads[0].startswith(REVISION)
    revision = (
        REPO_ROOT / "alembic" / "versions" / f"{REVISION}_add_execution_authorities.py"
    ).read_text()
    assert f'down_revision: str | Sequence[str] | None = "{PARENT}"' in revision


async def _tables(connection: AsyncConnection) -> set[str]:
    rows = await connection.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    )
    return {row[0] for row in rows}


async def test_the_migration_adds_only_the_authorities_table(
    url: str, connection: AsyncConnection
) -> None:
    before = await _tables(connection)
    assert TABLE not in before

    _alembic(url, "upgrade", REVISION)

    assert await _tables(connection) - before == {TABLE}


async def test_every_orm_check_and_unique_constraint_exists_once_migrated(
    url: str, connection: AsyncConnection
) -> None:
    _alembic(url, "upgrade", REVISION)
    table = Base.metadata.tables[TABLE]
    declared = {
        c.name
        for c in table.constraints
        if isinstance(c, CheckConstraint | UniqueConstraint) and c.name
    }
    rows = await connection.execute(
        text(
            "SELECT con.conname FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "WHERE rel.relname = :table AND con.contype IN ('c', 'u')"
        ),
        {"table": TABLE},
    )
    assert declared == {
        "ck_agent_execution_authority_digest",
        "uq_agent_execution_authority_identity",
    }
    assert declared <= {row[0] for row in rows}


async def test_the_table_has_a_tenant_rls_policy(
    url: str, connection: AsyncConnection
) -> None:
    _alembic(url, "upgrade", REVISION)
    rows = await connection.execute(
        text("SELECT policyname FROM pg_policies WHERE tablename = :table"),
        {"table": TABLE},
    )
    assert {row[0] for row in rows} == {f"tenant_isolation_{TABLE}"}
