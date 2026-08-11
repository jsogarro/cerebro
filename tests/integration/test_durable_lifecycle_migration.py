"""The durable lifecycle migration applies, enforces, and reverses cleanly.

The unit-level schema characterization runs on SQLite and can only prove what
SQLAlchemy declares. These tests run the real migration against a real
PostgreSQL server and prove what the database actually enforces: append-only
triggers that survive raw SQL, tenant row-level security, the per-run event
sequence, and a downgrade that leaves no residue.
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from testcontainers.postgres import PostgresContainer

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = Path(sys.executable).parent / "alembic"
REVISION = "c3f5a1d7b209"


def _revision_below(revision_id: str) -> str:
    """Return the revision immediately before ``revision_id`` in the chain.

    Reads the migration graph itself (via Alembic's ``ScriptDirectory``)
    rather than hardcoding a second revision id, so this stays correct
    however many migrations later packets stack on top of the durable
    lifecycle revision.
    """
    config = Config(str(REPO_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    down_revision = script.get_revision(revision_id).down_revision
    assert isinstance(down_revision, str), (
        f"{revision_id} has no single parent revision to downgrade to"
    )
    return down_revision


def _is_ancestor_or_self(target_revision: str, from_revision: str) -> bool:
    """Return whether ``target_revision`` is ``from_revision`` or one of its
    ancestors, walking ``down_revision`` via Alembic's ``ScriptDirectory``.

    Used to check a revision the database actually reports as applied
    (``alembic_version``) against the target, rather than checking whether a
    revision id merely exists as a file in ``versions/`` — ``alembic
    history`` reads only the script directory and never touches the
    database, so it would pass even against a database the migration never
    ran against.
    """
    config = Config(str(REPO_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    frontier: list[str] = [from_revision]
    seen: set[str] = set()
    while frontier:
        revision = frontier.pop()
        if revision in seen:
            continue
        seen.add(revision)
        if revision == target_revision:
            return True
        down_revision = script.get_revision(revision).down_revision
        if down_revision is None:
            continue
        if isinstance(down_revision, str):
            frontier.append(down_revision)
        else:
            frontier.extend(rev for rev in down_revision if rev is not None)
    return False


async def _applied_revision(database_url: str) -> str:
    """Return the revision id the database's ``alembic_version`` table
    reports as currently applied — the actual observed state, not a file on
    disk."""
    engine = create_async_engine(_async_url(database_url))
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT version_num FROM alembic_version")
            )
            return str(result.scalar_one())
    finally:
        await engine.dispose()


NEW_TABLES = frozenset(
    {
        "agent_runs",
        "agent_run_tasks",
        "agent_task_attempts",
        "agent_run_config_snapshots",
        "agent_run_events",
        "agent_run_event_outbox",
    }
)


def _run_alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(ALEMBIC), *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
        )
    return result


def _async_url(database_url: str) -> str:
    return database_url.replace("postgresql://", "postgresql+asyncpg://")


async def _table_names(database_url: str) -> set[str]:
    engine = create_async_engine(_async_url(database_url))
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            )
            return {row[0] for row in rows}
    finally:
        await engine.dispose()


@pytest.fixture(name="postgres_server", scope="module")
def postgres_server_fixture() -> Iterator[PostgresContainer]:
    """A dedicated, empty PostgreSQL server for migration runs."""
    with PostgresContainer(image="postgres:16-alpine") as container:
        yield container


def _database_url(container: PostgresContainer, dbname: str) -> str:
    host = container.get_container_host_ip()
    port = container.get_exposed_port(5432)
    return (
        f"postgresql://{container.username}:{container.password}@{host}:{port}/{dbname}"
    )


@pytest.fixture(name="migrated_database", scope="module")
def migrated_database_fixture(postgres_server: PostgresContainer) -> Iterator[str]:
    """A database with the full migration chain applied."""
    url = _database_url(postgres_server, postgres_server.dbname)
    _run_alembic(url, "upgrade", "head")
    yield url


@pytest_asyncio.fixture(name="connection")
async def connection_fixture(migrated_database: str) -> AsyncIterator[AsyncConnection]:
    """A connection whose work is always rolled back, so tests stay independent."""
    engine = create_async_engine(_async_url(migrated_database))
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()
    await engine.dispose()


async def _seed_run(connection: AsyncConnection, run_id: str) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO agent_runs (
                id, run_id, tenant_id, workflow_definition_id,
                workflow_definition_version, routing_policy_id,
                routing_policy_version, idempotency_key, requested_by, status,
                created_at, updated_at
            ) VALUES (
                gen_random_uuid(), :run_id, 'tenant-1', 'research', '1',
                'default', '1', :idempotency_key, 'user-1', 'created',
                now(), now()
            )
            """
        ),
        {"run_id": run_id, "idempotency_key": f"submit-{run_id}"},
    )


async def _seed_event(
    connection: AsyncConnection,
    *,
    event_id: str,
    run_id: str,
    sequence: int = 1,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO agent_run_events (
                id, event_id, run_id, aggregate_type, aggregate_id, sequence,
                event_type, event_type_version, occurred_at, producer,
                deduplication_key, payload
            ) VALUES (
                gen_random_uuid(), :event_id, :run_id, 'run', :run_id, :sequence,
                'run.created', '1', now(), 'test', :dedup, '{}'
            )
            """
        ),
        {
            "event_id": event_id,
            "run_id": run_id,
            "sequence": sequence,
            "dedup": f"{run_id}:{sequence}:run.created",
        },
    )


async def test_the_migration_chain_reaches_the_durable_lifecycle_revision(
    migrated_database: str,
) -> None:
    """The durable lifecycle revision is the applied revision, or an ancestor
    of it.

    Not "is head" — later packets migrate on top of this one, so head moves,
    and asserting equality to head broke the moment a migration landed on
    top. Reads the revision the database's own ``alembic_version`` table
    reports as applied (the actual observed state), then walks the
    migration graph backwards from it to confirm the target revision is
    that revision or one of its ancestors. ``alembic history`` alone is not
    enough here: it reads only ``versions/`` and never touches the
    database, so it would pass even against a database this migration never
    ran against.
    """
    applied = await _applied_revision(migrated_database)

    assert _is_ancestor_or_self(REVISION, applied)


async def test_durable_tables_exist_after_upgrade(migrated_database: str) -> None:
    assert await _table_names(migrated_database) >= NEW_TABLES


async def test_checkpoints_gained_a_nullable_run_link(
    connection: AsyncConnection,
) -> None:
    result = await connection.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'workflow_checkpoints' AND column_name = 'run_id'"
        )
    )

    assert result.scalar_one() == "YES"


async def test_every_durable_table_enforces_tenant_row_level_security(
    connection: AsyncConnection,
) -> None:
    rows = await connection.execute(
        text(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND rowsecurity = true"
        )
    )

    assert {row[0] for row in rows} >= NEW_TABLES


async def test_every_durable_table_has_a_tenant_isolation_policy(
    connection: AsyncConnection,
) -> None:
    rows = await connection.execute(
        text("SELECT tablename, policyname FROM pg_policies WHERE schemaname='public'")
    )
    policies = {(row[0], row[1]) for row in rows}

    assert {(table, f"tenant_isolation_{table}") for table in NEW_TABLES} <= policies


async def test_a_recorded_event_cannot_be_updated_by_raw_sql(
    connection: AsyncConnection,
) -> None:
    await _seed_run(connection, "run-update")
    await _seed_event(connection, event_id="event-update", run_id="run-update")

    with pytest.raises(DBAPIError, match="append-only"):
        await connection.execute(
            text(
                "UPDATE agent_run_events SET producer = 'tampered' "
                "WHERE event_id = 'event-update'"
            )
        )


async def test_a_recorded_event_cannot_be_deleted_by_raw_sql(
    connection: AsyncConnection,
) -> None:
    await _seed_run(connection, "run-delete")
    await _seed_event(connection, event_id="event-delete", run_id="run-delete")

    with pytest.raises(DBAPIError, match="append-only"):
        await connection.execute(
            text("DELETE FROM agent_run_events WHERE event_id = 'event-delete'")
        )


async def test_a_config_snapshot_cannot_be_updated_by_raw_sql(
    connection: AsyncConnection,
) -> None:
    await _seed_run(connection, "run-snapshot")
    await connection.execute(
        text(
            """
            INSERT INTO agent_run_config_snapshots (
                id, config_snapshot_id, run_id, tenant_id, authority_id,
                authority_version, workflow_definition_id,
                workflow_definition_version, routing_policy_id,
                routing_policy_version, pinned_versions, configuration,
                content_sha256
            ) VALUES (
                gen_random_uuid(), 'snapshot-1', 'run-snapshot', 'tenant-1',
                'authority-1', '1', 'research', '1', 'default', '1',
                '{}', '{}', :digest
            )
            """
        ),
        {"digest": "a" * 64},
    )

    with pytest.raises(DBAPIError, match="append-only"):
        await connection.execute(
            text(
                "UPDATE agent_run_config_snapshots SET authority_version = '2' "
                "WHERE config_snapshot_id = 'snapshot-1'"
            )
        )


async def test_event_sequence_is_unique_per_run_in_postgres(
    connection: AsyncConnection,
) -> None:
    await _seed_run(connection, "run-sequence")
    await _seed_event(connection, event_id="event-a", run_id="run-sequence")

    with pytest.raises(IntegrityError):
        await _seed_event(connection, event_id="event-b", run_id="run-sequence")


async def test_a_tenant_cannot_reuse_a_run_submission_key_in_postgres(
    connection: AsyncConnection,
) -> None:
    await _seed_run(connection, "run-dup-a")

    with pytest.raises(IntegrityError):
        await connection.execute(
            text(
                """
                INSERT INTO agent_runs (
                    id, run_id, tenant_id, workflow_definition_id,
                    workflow_definition_version, routing_policy_id,
                    routing_policy_version, idempotency_key, requested_by,
                    status, created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), 'run-dup-b', 'tenant-1', 'research',
                    '1', 'default', '1', 'submit-run-dup-a', 'user-1',
                    'created', now(), now()
                )
                """
            )
        )


async def test_downgrade_leaves_no_durable_schema_behind(
    postgres_server: PostgresContainer,
) -> None:
    """Upgrade then downgrade an isolated database, so no other test is disturbed.

    Downgrades to the revision immediately below the durable lifecycle one,
    not a fixed step count ("-1"). A relative step count only undoes
    whatever migration happens to be on top of this one; targeting the
    parent revision by name undoes everything back through it regardless of
    how many migrations later packets have stacked above.
    """
    admin_engine = create_async_engine(
        _async_url(_database_url(postgres_server, postgres_server.dbname)),
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text("DROP DATABASE IF EXISTS downgrade_probe"))
            await connection.execute(text("CREATE DATABASE downgrade_probe"))
    finally:
        await admin_engine.dispose()

    probe_url = _database_url(postgres_server, "downgrade_probe")
    _run_alembic(probe_url, "upgrade", "head")
    assert await _table_names(probe_url) >= NEW_TABLES

    _run_alembic(probe_url, "downgrade", _revision_below(REVISION))

    assert not (NEW_TABLES & await _table_names(probe_url))
