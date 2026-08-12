"""The Wave 4 persistence migration applies, enforces, and reverses cleanly.

Mirrors ``test_durable_lifecycle_migration.py``: the SQLite-backed
characterization in ``tests/unit/models/db/test_wave4_persistence_schema.py``
can only prove what SQLAlchemy declares. These tests run the real migration
against a real PostgreSQL server and prove what the database actually
enforces — most importantly, that the append-only triggers on
``agent_evidence`` and ``agent_claim_supports`` reject raw SQL, not just the
SQLAlchemy ORM's ``before_update``/``before_delete`` hooks.
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
from sqlalchemy import CheckConstraint, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.models.db import *  # noqa: F403  (register every table on Base.metadata)
from src.models.db.base import Base

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = Path(sys.executable).parent / "alembic"
REVISION = "5d04cec6c232"


def _revision_below(revision_id: str) -> str:
    """Return the revision immediately before ``revision_id`` in the chain.

    Reads the migration graph itself (via Alembic's ``ScriptDirectory``)
    rather than hardcoding a second revision id, so this stays correct
    however many migrations later packets stack on top of this one — see
    ``test_durable_lifecycle_migration.py``, which this mirrors and which
    broke exactly this way when this migration landed on top of it.
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
        "agent_capability_grants",
        "agent_capability_approvals",
        "agent_artifacts",
        "agent_tool_invocations",
        "agent_evidence",
        "agent_claim_supports",
    }
)
APPEND_ONLY_TABLES = frozenset({"agent_evidence", "agent_claim_supports"})


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
    """A database with the full migration chain, including this one, applied."""
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


async def _seed_task(connection: AsyncConnection, *, task_id: str, run_id: str) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO agent_run_tasks (
                id, task_id, run_id, task_key, task_type, objective,
                idempotency_key, dependency_ids, "input", status,
                created_at, updated_at
            ) VALUES (
                gen_random_uuid(), :task_id, :run_id, :task_id, 'research',
                'objective', :idempotency_key, '[]', '{}', 'pending',
                now(), now()
            )
            """
        ),
        {"task_id": task_id, "run_id": run_id, "idempotency_key": f"key-{task_id}"},
    )


async def _seed_attempt(
    connection: AsyncConnection, *, attempt_id: str, task_id: str, run_id: str
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO agent_task_attempts (
                id, attempt_id, task_id, run_id, ordinal, idempotency_key,
                status, created_at, updated_at
            ) VALUES (
                gen_random_uuid(), :attempt_id, :task_id, :run_id, 1,
                :idempotency_key, 'created', now(), now()
            )
            """
        ),
        {
            "attempt_id": attempt_id,
            "task_id": task_id,
            "run_id": run_id,
            "idempotency_key": f"key-{attempt_id}",
        },
    )


async def _seed_grant(
    connection: AsyncConnection, *, grant_id: str, run_id: str, task_id: str
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO agent_capability_grants (
                id, grant_id, run_id, task_id, capability_scope, tool_name,
                tool_versions, sensitivity, max_input_trust, requires_approval,
                issued_at, expires_at, created_at, updated_at
            ) VALUES (
                gen_random_uuid(), :grant_id, :run_id, :task_id, 'search',
                'academic_search', '["1.0"]', 'read_only', 'user_supplied',
                false, now(), now() + interval '1 day', now(), now()
            )
            """
        ),
        {"grant_id": grant_id, "run_id": run_id, "task_id": task_id},
    )


async def _seed_invocation(
    connection: AsyncConnection,
    *,
    invocation_id: str,
    run_id: str,
    task_id: str,
    attempt_id: str,
    idempotency_key: str,
    status: str,
    effect: str,
    denial_reason: str | None = None,
    grant_id: str | None = None,
) -> None:
    """Insert one tool invocation by raw SQL, bypassing the ORM entirely.

    The point of this file is what the *migrated database* enforces. Going
    through the models would test the models' opinion of the migration, which
    is the thing being cross-checked rather than the thing to trust.
    """
    await connection.execute(
        text(
            "INSERT INTO agent_tool_invocations ("
            "id, tool_invocation_id, run_id, task_id, attempt_id, tool_name, "
            "tool_version, status, capability_scope, idempotency_key, "
            '"input", input_trust, input_sha256, producer_kind, '
            "capability_decision_effect, capability_denial_reason, "
            "capability_grant_id, request_fingerprint, "
            "requested_at, created_at, updated_at) VALUES ("
            "gen_random_uuid(), :invocation_id, :run_id, :task_id, :attempt_id, "
            "'academic_search', '1.0', :status, 'search', :idempotency_key, "
            "'{}', 'user_supplied', :digest, 'system', :effect, :denial_reason, "
            ":grant_id, :fingerprint, now(), now(), now())"
        ),
        {
            "invocation_id": invocation_id,
            "run_id": run_id,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "status": status,
            "idempotency_key": idempotency_key,
            "digest": "a" * 64,
            "effect": effect,
            "denial_reason": denial_reason,
            "grant_id": grant_id,
            "fingerprint": "c" * 64,
        },
    )


async def _seed_denial(
    connection: AsyncConnection,
    *,
    invocation_id: str,
    run_id: str,
    task_id: str,
    attempt_id: str,
    idempotency_key: str,
) -> None:
    await _seed_invocation(
        connection,
        invocation_id=invocation_id,
        run_id=run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        idempotency_key=idempotency_key,
        status="denied",
        effect="deny",
        denial_reason="no_matching_grant",
    )


async def _seed_success(
    connection: AsyncConnection,
    *,
    invocation_id: str,
    run_id: str,
    task_id: str,
    attempt_id: str,
    idempotency_key: str,
    grant_id: str,
) -> None:
    await _seed_invocation(
        connection,
        invocation_id=invocation_id,
        run_id=run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        idempotency_key=idempotency_key,
        status="succeeded",
        effect="allow",
        grant_id=grant_id,
    )


async def _seed_artifact(
    connection: AsyncConnection, *, artifact_id: str, run_id: str
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO agent_artifacts (
                id, artifact_id, run_id, kind, media_type, storage_uri,
                content_sha256, status, trust, producer, "metadata",
                producer_kind, created_at, updated_at
            ) VALUES (
                gen_random_uuid(), :artifact_id, :run_id, 'source_snapshot',
                'text/html', 's3://bucket/key', :digest, 'final',
                'external_untrusted', 'acquisition-tool', '{}', 'system',
                now(), now()
            )
            """
        ),
        {"artifact_id": artifact_id, "run_id": run_id, "digest": "a" * 64},
    )


async def _seed_evidence(
    connection: AsyncConnection,
    *,
    evidence_id: str,
    run_id: str,
    task_id: str,
    artifact_id: str,
    locator: str = "char:0-120",
    locator_scheme: str = "char",
    locator_start: int = 0,
    locator_end: int = 120,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO agent_evidence (
                id, evidence_id, run_id, task_id, source_type, source_uri,
                snapshot_artifact_id, content_sha256, locator, locator_scheme,
                locator_start, locator_end, trust, producer_kind,
                prompt_id, prompt_version, template_sha256, rendered_sha256,
                parent_evidence_ids, acquired_at
            ) VALUES (
                gen_random_uuid(), :evidence_id, :run_id, :task_id,
                'web_page', 'https://example.org/paper', :artifact_id,
                :digest, :locator, :locator_scheme, :locator_start,
                :locator_end, 'external_untrusted', 'model_turn', 'prompt-1',
                '1.0', :template_digest, :rendered_digest, '[]', now()
            )
            """
        ),
        {
            "evidence_id": evidence_id,
            "run_id": run_id,
            "task_id": task_id,
            "artifact_id": artifact_id,
            "digest": "a" * 64,
            "locator": locator,
            "locator_scheme": locator_scheme,
            "locator_start": locator_start,
            "locator_end": locator_end,
            "template_digest": "b" * 64,
            "rendered_digest": "c" * 64,
        },
    )


async def _seed_claim_support(
    connection: AsyncConnection,
    *,
    claim_support_id: str,
    run_id: str,
    artifact_id: str,
    claim_id: str = "claim-1",
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO agent_claim_supports (
                id, claim_support_id, run_id, artifact_id, claim_id,
                claim_text, status, evidence_ids, evidence_count,
                absent_evidence_reason, evaluator_id, evaluator_version,
                producer_kind, prompt_id, prompt_version, template_sha256,
                rendered_sha256, explanation, evaluated_at
            ) VALUES (
                gen_random_uuid(), :claim_support_id, :run_id, :artifact_id,
                :claim_id, 'The model improves accuracy.', 'unsupported',
                '[]', 0, 'no_source_found', 'evaluator-1', '1.0',
                'model_turn', 'prompt-2', '1.0', :template_digest,
                :rendered_digest, 'No evidence found.', now()
            )
            """
        ),
        {
            "claim_support_id": claim_support_id,
            "run_id": run_id,
            "artifact_id": artifact_id,
            "claim_id": claim_id,
            "template_digest": "d" * 64,
            "rendered_digest": "e" * 64,
        },
    )


async def test_the_migration_chain_reaches_the_wave4_persistence_revision(
    migrated_database: str,
) -> None:
    """The Wave 4 persistence revision is the applied revision, or an
    ancestor of it.

    Not "is head" — a later packet (4E, 4F) will migrate on top of this one,
    and asserting equality to head would break the moment that happens.
    Reads the revision the database's own ``alembic_version`` table reports
    as applied, then walks the migration graph backwards from it. ``alembic
    history`` alone is not enough here: it reads only ``versions/`` and
    never touches the database, so it would pass even against a database
    this migration never ran against.
    """
    applied = await _applied_revision(migrated_database)

    assert _is_ancestor_or_self(REVISION, applied)


async def test_wave4_tables_exist_after_upgrade(migrated_database: str) -> None:
    assert await _table_names(migrated_database) >= NEW_TABLES


async def test_producer_kind_carries_no_column_default_after_upgrade(
    connection: AsyncConnection,
) -> None:
    """``producer_kind`` has no server default on any of the four provenance
    tables in the schema Alembic actually produces.

    The repository integration tests provision their schema via
    ``Base.metadata.create_all()`` against the SQLAlchemy models, not via
    this migration -- so they cannot catch a default that survives in the
    migration's DDL alone (for example, a call site left pointing at the old
    ``_optional_prompt_binding_columns()`` helper). This is the one place a
    default could be reintroduced without any other Wave 4 test noticing.
    """
    rows = await connection.execute(
        text(
            "SELECT table_name, column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'producer_kind' "
            "AND table_name = ANY(:tables)"
        ),
        {
            "tables": [
                "agent_artifacts",
                "agent_tool_invocations",
                "agent_evidence",
                "agent_claim_supports",
            ]
        },
    )
    defaults = dict(rows.all())

    assert set(defaults) == {
        "agent_artifacts",
        "agent_tool_invocations",
        "agent_evidence",
        "agent_claim_supports",
    }
    assert all(value is None for value in defaults.values()), defaults


async def test_the_migration_declares_every_check_the_orm_does(
    connection: AsyncConnection,
) -> None:
    """The migration and the models must not drift apart silently.

    The unit schema suite builds these tables with ``Base.metadata.create_all``
    and proves what the *ORM* declares; production builds them from this
    migration. Nothing until now compared the two, so a CHECK added to one and
    forgotten in the other would leave the fast suite green and the deployed
    database unguarded — a constraint that exists in every test and in no
    running system. This repository has been bitten by that divergence three
    times.

    Names rather than expressions, deliberately: Postgres normalizes a CHECK
    body (reformatting, casts, parenthesization), so comparing text would fail
    on rewordings that change nothing and teach the reader to ignore it. A
    name is stable and is what a migration author actually forgets to copy.
    """

    declared = {
        (table.name, constraint.name)
        for table in Base.metadata.tables.values()
        if table.name in NEW_TABLES
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }

    rows = await connection.execute(
        text(
            "SELECT rel.relname, con.conname "
            "FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace "
            "WHERE con.contype = 'c' AND nsp.nspname = 'public'"
        )
    )
    migrated = {(row[0], row[1]) for row in rows if row[0] in NEW_TABLES}

    missing = declared - migrated
    assert not missing, (
        f"{len(missing)} CHECK constraint(s) are declared on the ORM models "
        f"but absent from the migrated database: {sorted(missing)}. The unit "
        "schema suite passes on create_all and would not notice."
    )


async def test_the_migration_declares_every_unique_index_the_orm_does(
    connection: AsyncConnection,
) -> None:
    """The same parity check, extended to indexes — because uniqueness moved.

    The CHECK-name comparison above was the whole of the ORM/migration parity
    guard, and it could not see this change at all: ``(attempt_id,
    idempotency_key)`` uniqueness is now a *partial unique index*, since a
    table constraint carries no predicate. A unique index declared on the
    models and forgotten in the migration would leave the fast suite green
    and the deployed database unguarded, which is exactly the divergence the
    CHECK test was written for.

    **Unique indexes only, and the restriction is deliberate rather than
    convenient.** Extended to all indexes this fails today for a reason that
    predates this change: the Wave 4 models declare 24 single-column
    ``index=True`` indexes (``ix_agent_artifacts_run_id`` and its siblings on
    every Wave 4 table) that the migration never creates — it creates
    differently-named composite indexes instead. That gap is real and is
    recorded here rather than silently accommodated, but it is a query-plan
    divergence with no correctness consequence, and closing 24 of them is not
    this change's scope. A unique index is the case where a missing index
    means a rule the database does not enforce.

    Names rather than definitions: Postgres normalizes an index definition,
    and a name is what a migration author forgets to copy. The predicate
    itself is proven separately, and behaviourally.
    """

    declared = {
        (table.name, index.name)
        for table in Base.metadata.tables.values()
        if table.name in NEW_TABLES
        for index in table.indexes
        if index.name and index.unique
    }

    rows = await connection.execute(
        text("SELECT tablename, indexname FROM pg_indexes WHERE schemaname='public'")
    )
    migrated = {(row[0], row[1]) for row in rows if row[0] in NEW_TABLES}

    assert declared, "no unique index was collected, so this test proves nothing"

    missing = declared - migrated
    assert not missing, (
        f"{len(missing)} unique index(es) are declared on the ORM models but "
        f"absent from the migrated database: {sorted(missing)}. The unit "
        "schema suite passes on create_all and would not notice."
    )


async def test_the_migrated_idempotency_index_is_unique_and_excludes_denials(
    connection: AsyncConnection,
) -> None:
    """Read the predicate out of the database that has it.

    An index of the right name over the right columns with *no* predicate is
    the pre-change behaviour wearing the new name, and the parity test above
    would pass on it. This is the assertion that the migration shipped the
    partial index and not merely a renamed one.
    """

    row = await connection.execute(
        text(
            "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
            "AND tablename='agent_tool_invocations' "
            "AND indexname='uq_agent_tool_invocation_idempotency'"
        )
    )
    definition = row.scalar_one()

    assert "CREATE UNIQUE INDEX" in definition
    assert "(attempt_id, idempotency_key)" in definition
    assert "WHERE" in definition
    assert "denied" in definition


async def test_the_migrated_database_has_the_plain_lookup_index(
    connection: AsyncConnection,
) -> None:
    """Named separately because the parity test above cannot see it.

    That test compares *unique* indexes only, for reasons it states, so a
    migration that dropped the non-unique lookup index would pass every other
    check here. The index matters: the replay lookup reads ``(attempt_id,
    idempotency_key)`` on every mediated call and must see denied rows, which
    the partial unique index does not cover, and the unconditional constraint
    that used to back this lookup for free is gone.
    """

    row = await connection.execute(
        text(
            "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
            "AND tablename='agent_tool_invocations' "
            "AND indexname='ix_agent_tool_invocation_idempotency_lookup'"
        )
    )
    definition = row.scalar_one()

    assert "UNIQUE" not in definition
    assert "(attempt_id, idempotency_key)" in definition
    assert "WHERE" not in definition


async def test_postgres_admits_repeated_refusals_under_one_idempotency_key(
    connection: AsyncConnection,
) -> None:
    """Two denials, one key, two rows — enforced by the real server.

    The SQLite characterization proves what SQLAlchemy declares. This proves
    what Postgres does, which is the only thing a deployment experiences: an
    idempotency key reserves one unit of work, and a refusal is a decision
    about a call that never ran.
    """

    await _seed_run(connection, "run-denial")
    await _seed_task(connection, task_id="task-denial", run_id="run-denial")
    await _seed_attempt(
        connection,
        attempt_id="attempt-denial",
        task_id="task-denial",
        run_id="run-denial",
    )

    for ordinal in (1, 2, 3):
        await _seed_denial(
            connection,
            invocation_id=f"denied-{ordinal}",
            run_id="run-denial",
            task_id="task-denial",
            attempt_id="attempt-denial",
            idempotency_key="one-derived-key",
        )

    stored = await connection.execute(
        text(
            "SELECT count(*) FROM agent_tool_invocations "
            "WHERE attempt_id = 'attempt-denial' "
            "AND idempotency_key = 'one-derived-key'"
        )
    )

    assert stored.scalar_one() == 3


async def test_postgres_still_refuses_a_reused_key_for_work_that_ran(
    connection: AsyncConnection,
) -> None:
    """The half of the constraint that must survive the narrowing.

    Excluding denials is the change; excluding anything else would be a
    regression, and a partial index whose predicate is wrong in the other
    direction (say ``status = 'denied'``, or no predicate at all on the
    inverse) would let one unit of work be recorded twice under one key.
    """

    await _seed_run(connection, "run-work")
    await _seed_task(connection, task_id="task-work", run_id="run-work")
    await _seed_attempt(
        connection, attempt_id="attempt-work", task_id="task-work", run_id="run-work"
    )
    await _seed_grant(
        connection, grant_id="grant-work", run_id="run-work", task_id="task-work"
    )
    await _seed_success(
        connection,
        invocation_id="succeeded-1",
        run_id="run-work",
        task_id="task-work",
        attempt_id="attempt-work",
        idempotency_key="one-derived-key",
        grant_id="grant-work",
    )

    with pytest.raises(IntegrityError):
        await _seed_success(
            connection,
            invocation_id="succeeded-2",
            run_id="run-work",
            task_id="task-work",
            attempt_id="attempt-work",
            idempotency_key="one-derived-key",
            grant_id="grant-work",
        )


async def test_a_denied_key_stays_available_to_the_authorized_caller(
    connection: AsyncConnection,
) -> None:
    """The victim case, at the level that produced it.

    A refusal followed by a real call under the same key. The insert that
    used to raise here is the *authorized* one, and it raised at a caller who
    then had no record of its own call either.
    """

    await _seed_run(connection, "run-mixed")
    await _seed_task(connection, task_id="task-mixed", run_id="run-mixed")
    await _seed_attempt(
        connection, attempt_id="attempt-mixed", task_id="task-mixed", run_id="run-mixed"
    )
    await _seed_grant(
        connection, grant_id="grant-mixed", run_id="run-mixed", task_id="task-mixed"
    )
    await _seed_denial(
        connection,
        invocation_id="denied-first",
        run_id="run-mixed",
        task_id="task-mixed",
        attempt_id="attempt-mixed",
        idempotency_key="victim-chosen-key",
    )

    await _seed_success(
        connection,
        invocation_id="allowed-second",
        run_id="run-mixed",
        task_id="task-mixed",
        attempt_id="attempt-mixed",
        idempotency_key="victim-chosen-key",
        grant_id="grant-mixed",
    )

    stored = await connection.execute(
        text(
            "SELECT count(*) FROM agent_tool_invocations "
            "WHERE attempt_id = 'attempt-mixed' "
            "AND idempotency_key = 'victim-chosen-key'"
        )
    )

    assert stored.scalar_one() == 2


async def test_every_wave4_table_enforces_tenant_row_level_security(
    connection: AsyncConnection,
) -> None:
    """RLS is installed as posture, not enforcement.

    Per the accepted Wave 3 tenant identity decision, the application
    connects as the Postgres table owner, which bypasses row-level security
    policies entirely — this test proves the policy exists, not that it
    blocks anything. The real boundary is each repository's
    ``organization_id`` filter, proven separately in the repository tests.
    """
    rows = await connection.execute(
        text(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' AND rowsecurity = true"
        )
    )

    assert {row[0] for row in rows} >= NEW_TABLES


async def test_every_wave4_table_has_a_tenant_isolation_policy(
    connection: AsyncConnection,
) -> None:
    rows = await connection.execute(
        text("SELECT tablename, policyname FROM pg_policies WHERE schemaname='public'")
    )
    policies = {(row[0], row[1]) for row in rows}

    assert {(table, f"tenant_isolation_{table}") for table in NEW_TABLES} <= policies


async def test_recorded_evidence_cannot_be_updated_by_raw_sql(
    connection: AsyncConnection,
) -> None:
    await _seed_run(connection, "run-evidence-update")
    await _seed_task(
        connection, task_id="task-evidence-update", run_id="run-evidence-update"
    )
    await _seed_artifact(
        connection, artifact_id="artifact-evidence-update", run_id="run-evidence-update"
    )
    await _seed_evidence(
        connection,
        evidence_id="evidence-update",
        run_id="run-evidence-update",
        task_id="task-evidence-update",
        artifact_id="artifact-evidence-update",
    )

    with pytest.raises(DBAPIError, match="append-only"):
        await connection.execute(
            text(
                "UPDATE agent_evidence SET locator = 'char:0-999' "
                "WHERE evidence_id = 'evidence-update'"
            )
        )


async def test_recorded_evidence_cannot_be_deleted_by_raw_sql(
    connection: AsyncConnection,
) -> None:
    await _seed_run(connection, "run-evidence-delete")
    await _seed_task(
        connection, task_id="task-evidence-delete", run_id="run-evidence-delete"
    )
    await _seed_artifact(
        connection, artifact_id="artifact-evidence-delete", run_id="run-evidence-delete"
    )
    await _seed_evidence(
        connection,
        evidence_id="evidence-delete",
        run_id="run-evidence-delete",
        task_id="task-evidence-delete",
        artifact_id="artifact-evidence-delete",
    )

    with pytest.raises(DBAPIError, match="append-only"):
        await connection.execute(
            text("DELETE FROM agent_evidence WHERE evidence_id = 'evidence-delete'")
        )


async def test_recorded_claim_support_cannot_be_updated_by_raw_sql(
    connection: AsyncConnection,
) -> None:
    await _seed_run(connection, "run-claim-update")
    await _seed_artifact(
        connection, artifact_id="artifact-claim-update", run_id="run-claim-update"
    )
    await _seed_claim_support(
        connection,
        claim_support_id="claim-support-update",
        run_id="run-claim-update",
        artifact_id="artifact-claim-update",
    )

    with pytest.raises(DBAPIError, match="append-only"):
        await connection.execute(
            text(
                "UPDATE agent_claim_supports SET status = 'disputed' "
                "WHERE claim_support_id = 'claim-support-update'"
            )
        )


async def test_recorded_claim_support_cannot_be_deleted_by_raw_sql(
    connection: AsyncConnection,
) -> None:
    await _seed_run(connection, "run-claim-delete")
    await _seed_artifact(
        connection, artifact_id="artifact-claim-delete", run_id="run-claim-delete"
    )
    await _seed_claim_support(
        connection,
        claim_support_id="claim-support-delete",
        run_id="run-claim-delete",
        artifact_id="artifact-claim-delete",
    )

    with pytest.raises(DBAPIError, match="append-only"):
        await connection.execute(
            text(
                "DELETE FROM agent_claim_supports "
                "WHERE claim_support_id = 'claim-support-delete'"
            )
        )


async def test_the_evidence_span_uniqueness_is_enforced_in_postgres(
    connection: AsyncConnection,
) -> None:
    await _seed_run(connection, "run-span")
    await _seed_task(connection, task_id="task-span", run_id="run-span")
    await _seed_artifact(connection, artifact_id="artifact-span", run_id="run-span")
    await _seed_evidence(
        connection,
        evidence_id="evidence-span-a",
        run_id="run-span",
        task_id="task-span",
        artifact_id="artifact-span",
    )

    with pytest.raises(IntegrityError):
        await _seed_evidence(
            connection,
            evidence_id="evidence-span-b",
            run_id="run-span",
            task_id="task-span",
            artifact_id="artifact-span",
        )


async def test_downgrade_leaves_no_wave4_schema_behind(
    postgres_server: PostgresContainer,
) -> None:
    """Upgrade then downgrade an isolated database, so no other test is disturbed.

    Downgrades to the revision immediately below this one, not a fixed step
    count ("-1"). A relative step count only undoes whatever migration a
    later packet has stacked on top; targeting the parent revision by name
    undoes everything back through it regardless of what is stacked above.
    """
    admin_engine = create_async_engine(
        _async_url(_database_url(postgres_server, postgres_server.dbname)),
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(
                text("DROP DATABASE IF EXISTS wave4_downgrade_probe")
            )
            await connection.execute(text("CREATE DATABASE wave4_downgrade_probe"))
    finally:
        await admin_engine.dispose()

    probe_url = _database_url(postgres_server, "wave4_downgrade_probe")
    _run_alembic(probe_url, "upgrade", "head")
    assert await _table_names(probe_url) >= NEW_TABLES

    _run_alembic(probe_url, "downgrade", _revision_below(REVISION))

    assert not (NEW_TABLES & await _table_names(probe_url))
