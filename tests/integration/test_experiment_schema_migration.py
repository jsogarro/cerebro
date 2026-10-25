"""The migrated experiment tables agree with the experiment ORM models.

SQLite cannot show this: the migration declares Postgres enum labels,
nullability, defaults and timezone-aware timestamps. Set
``MIGRATION_TEST_DATABASE_URL`` to an empty Postgres database to run without
Docker; otherwise a throwaway container is used.
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.models.db.base import Base
from src.models.db.experiment import (
    AllocationStrategy,
    Experiment,
    ExperimentStatus,
    ExperimentType,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = Path(sys.executable).parent / "alembic"


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    url = os.environ.get("MIGRATION_TEST_DATABASE_URL")
    if url:
        yield url
        return
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(image="postgres:16-alpine") as container:
        yield (
            f"postgresql://{container.username}:{container.password}"
            f"@{container.get_container_host_ip()}:{container.get_exposed_port(5432)}"
            f"/{container.dbname}"
        )


@pytest.fixture(scope="module")
def migrated_url(database_url: str) -> str:
    result = subprocess.run(
        [str(ALEMBIC), "upgrade", "head"],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    return database_url.replace("postgresql://", "postgresql+asyncpg://")


def _only_experiment_tables(obj, name, type_, reflected, compare_to) -> bool:
    if type_ == "table":
        return name.startswith("experiment")
    table = getattr(obj, "table", None)
    return table is None or table.name.startswith("experiment")


async def test_orm_and_migrated_schema_have_no_difference(migrated_url: str) -> None:
    engine = create_async_engine(migrated_url)

    def diff(connection):
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                "compare_server_default": True,
                "include_object": _only_experiment_tables,
            },
        )
        return compare_metadata(context, Base.metadata)

    async with engine.connect() as connection:
        differences = await connection.run_sync(diff)
    await engine.dispose()
    assert differences == []


async def test_enum_labels_match_the_orm(migrated_url: str) -> None:
    engine = create_async_engine(migrated_url)
    async with engine.connect() as connection:
        for type_name, enum in [
            ("experimenttype", ExperimentType),
            ("experimentstatus", ExperimentStatus),
            ("allocationstrategy", AllocationStrategy),
        ]:
            rows = await connection.execute(
                text(
                    "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t "
                    "ON t.oid = e.enumtypid WHERE t.typname = :name "
                    "ORDER BY e.enumsortorder"
                ),
                {"name": type_name},
            )
            assert [row[0] for row in rows] == [m.value for m in enum]
    await engine.dispose()


async def test_orm_can_insert_an_experiment(migrated_url: str) -> None:
    engine = create_async_engine(migrated_url)
    async with AsyncSession(engine) as session:
        session.add(
            Experiment(name="enum-labels", experiment_type=ExperimentType.PROMPT)
        )
        await session.flush()
        stored = (
            await session.execute(
                text("SELECT experiment_type, status FROM experiments")
            )
        ).one()
        assert tuple(stored) == ("prompt", "draft")
        await session.rollback()
    await engine.dispose()
