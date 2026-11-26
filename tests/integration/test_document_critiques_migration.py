"""The critique migration enforces what the ORM declares, and erasure reaches it."""

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import CheckConstraint, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.models.db.base import Base
from src.models.db.document_critique import (  # noqa: F401
    CRITIQUE_REASONS,
    CritiqueFinding,
    DocumentCritique,
)
from src.models.db.job_constants import JOB_STATUSES
from src.models.db.uploaded_document import UploadedDocument
from src.models.db.user import User

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = Path(sys.executable).parent / "alembic"
NEW_TABLES = frozenset({"document_critiques", "critique_findings"})


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


def test_there_is_exactly_one_head_and_it_chains_on_the_uploads_revision() -> None:
    heads = [
        line for line in _alembic("postgresql://x/y", "heads").splitlines() if line
    ]
    assert len(heads) == 1
    revision = (
        REPO_ROOT / "alembic" / "versions" / "a7d2e9c4b1f8_add_document_critiques.py"
    ).read_text()
    assert 'down_revision: str | Sequence[str] | None = "e4b7c1a9d6f2"' in revision


async def test_tables_have_tenant_rls_policies(connection: AsyncConnection) -> None:
    rows = await connection.execute(
        text("SELECT tablename, policyname FROM pg_policies WHERE schemaname='public'")
    )
    policies = {(r[0], r[1]) for r in rows}
    assert {(t, f"tenant_isolation_{t}") for t in NEW_TABLES} <= policies
    flags = await connection.execute(
        text(
            "SELECT relname, relrowsecurity FROM pg_class "
            "WHERE relname IN ('document_critiques', 'critique_findings')"
        )
    )
    assert dict(flags.all()) == dict.fromkeys(NEW_TABLES, True)


async def test_every_orm_check_and_index_exists_in_the_migrated_database(
    connection: AsyncConnection,
) -> None:
    tables = [t for t in Base.metadata.tables.values() if t.name in NEW_TABLES]
    declared_checks = {
        (t.name, c.name)
        for t in tables
        for c in t.constraints
        if isinstance(c, CheckConstraint) and c.name
    }
    declared_indexes = {(t.name, i.name) for t in tables for i in t.indexes}
    checks = await connection.execute(
        text(
            "SELECT rel.relname, con.conname FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid WHERE con.contype = 'c'"
        )
    )
    indexes = await connection.execute(
        text("SELECT tablename, indexname FROM pg_indexes WHERE schemaname='public'")
    )
    assert declared_checks and declared_indexes
    assert declared_checks <= {(r[0], r[1]) for r in checks}
    assert declared_indexes <= {(r[0], r[1]) for r in indexes}


async def _user(connection: AsyncConnection) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with AsyncSession(bind=connection, expire_on_commit=False) as session:
        session.add(
            User(
                id=user_id,
                email=f"{user_id}@example.com",
                username=str(user_id),
                hashed_password="x",
            )
        )
        await session.flush()
    return user_id


async def _document(connection: AsyncConnection) -> tuple[uuid.UUID, uuid.UUID]:
    document_id, user_id = uuid.uuid4(), await _user(connection)
    await connection.execute(
        text(
            "INSERT INTO uploaded_documents (id, organization_id, user_id, "
            "filename_enc, media_type, source_sha256, text_sha256, char_count, "
            "pages, extractor_version, invisible_chars_removed, text_enc, "
            "declared_authors_enc, expires_at) VALUES (:id, gen_random_uuid(), :usr, "
            "'f', 'pdf', 's', 't', 5, '[]', 'v', 0, 'x', 'y', now())"
        ),
        {"id": document_id, "usr": user_id},
    )
    return document_id, user_id


async def _critique(
    connection: AsyncConnection,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    status: str = "queued",
    reason: str | None = None,
) -> uuid.UUID:
    critique_id = uuid.uuid4()
    await connection.execute(
        text(
            "INSERT INTO document_critiques (id, document_id, organization_id, "
            "user_id, status, status_reason, model_id, temperature, prompt_versions, "
            "consent_text_version, consent_text_sha256, consent_recorded_at, "
            "reserved_units, role_outcomes) VALUES (:id, :doc, gen_random_uuid(), "
            ":usr, :status, :reason, 'm', 0.2, '{}', 'v', 's', now(), 1, '{}')"
        ),
        {
            "id": critique_id,
            "doc": document_id,
            "usr": user_id,
            "status": status,
            "reason": reason,
        },
    )
    return critique_id


async def _finding(
    connection: AsyncConnection, critique_id: uuid.UUID, **override: str
) -> None:
    values = {"role": "methods", "severity": "major", "kind": "exact", **override}
    await connection.execute(
        text(
            "INSERT INTO critique_findings (id, critique_id, organization_id, "
            "user_id, role, category, severity, quote_enc, raw_start, raw_end, page, "
            "match_kind, problem_enc, fix_enc) VALUES (gen_random_uuid(), :cid, "
            "gen_random_uuid(), gen_random_uuid(), :role, 'c', :severity, 'q', 0, 1, "
            "1, :kind, 'p', 'f')"
        ),
        {"cid": critique_id, **values},
    )


@pytest.mark.parametrize("status", JOB_STATUSES)
async def test_every_job_status_is_accepted_and_others_are_not(
    connection: AsyncConnection, status: str
) -> None:
    document_id, user_id = await _document(connection)
    await _critique(connection, document_id, user_id, status=status)


@pytest.mark.parametrize("reason", [None, *CRITIQUE_REASONS])
async def test_every_reason_the_service_writes_is_accepted(
    connection: AsyncConnection, reason: str | None
) -> None:
    document_id, user_id = await _document(connection)
    await _critique(connection, document_id, user_id, status="failed", reason=reason)


@pytest.mark.parametrize(
    "override",
    [{"status": "cancelled"}, {"reason": "boom: exception text"}],
)
async def test_unknown_statuses_and_reasons_are_rejected(
    connection: AsyncConnection, override: dict[str, str]
) -> None:
    document_id, user_id = await _document(connection)
    with pytest.raises(IntegrityError):
        await _critique(connection, document_id, user_id, **override)


@pytest.mark.parametrize(
    "override", [{"role": "judge"}, {"severity": "fatal"}, {"kind": "fuzzy"}]
)
async def test_finding_checks_reject_bad_rows(
    connection: AsyncConnection, override: dict[str, str]
) -> None:
    document_id, user_id = await _document(connection)
    critique_id = await _critique(connection, document_id, user_id)
    with pytest.raises(IntegrityError):
        await _finding(connection, critique_id, **override)


async def test_a_critique_needs_an_existing_document(
    connection: AsyncConnection,
) -> None:
    with pytest.raises(IntegrityError):
        await _critique(connection, uuid.uuid4(), await _user(connection))


async def test_a_core_delete_of_a_document_removes_its_critiques_and_findings(
    connection: AsyncConnection,
) -> None:
    from sqlalchemy import delete

    document_id, user_id = await _document(connection)
    critique_id = await _critique(connection, document_id, user_id)
    await _finding(connection, critique_id)
    await connection.execute(
        delete(UploadedDocument.__table__).where(
            UploadedDocument.__table__.c.id == document_id
        )
    )
    for sql in (
        "SELECT count(*) FROM document_critiques WHERE id = :id",
        "SELECT count(*) FROM critique_findings WHERE critique_id = :id",
    ):
        assert await connection.scalar(text(sql), {"id": critique_id}) == 0


async def test_deleting_a_user_reaches_critiques_through_their_documents(
    connection: AsyncConnection,
) -> None:
    document_id, user_id = await _document(connection)
    critique_id = await _critique(connection, document_id, user_id)
    await _finding(connection, critique_id)
    await connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
    assert (
        await connection.scalar(
            text("SELECT count(*) FROM critique_findings WHERE critique_id = :id"),
            {"id": critique_id},
        )
        == 0
    )


async def test_downgrade_removes_tables_and_policies_and_upgrade_restores_them(
    url: str,
) -> None:
    _alembic(url, "upgrade", "head")
    _alembic(url, "downgrade", "-1")
    engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with engine.connect() as connection:
            tables = await connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            )
            assert not NEW_TABLES & {r[0] for r in tables}
            policies = await connection.execute(
                text("SELECT tablename FROM pg_policies WHERE schemaname='public'")
            )
            assert not NEW_TABLES & {r[0] for r in policies}
    finally:
        await engine.dispose()
        _alembic(url, "upgrade", "head")
