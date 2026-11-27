"""The similarity migration enforces what the ORM declares, and erasure reaches it."""

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import CheckConstraint, delete, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.models.db.base import Base
from src.models.db.job_constants import JOB_STATUSES, RUN_JOB_REASONS
from src.models.db.similarity_check import (  # noqa: F401
    ATTRIBUTIONS,
    EGRESS_HOSTS,
    MATCH_KINDS,
    REQUEST_CLASSES,
    SOURCE_ORIGINS,
    EgressEvent,
    SimilarityCheck,
    SimilarityMatch,
)
from src.models.db.uploaded_document import UploadedDocument
from src.models.db.user import User

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = Path(sys.executable).parent / "alembic"
NEW_TABLES = frozenset({"similarity_checks", "similarity_matches", "egress_events"})


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


def test_one_head_chained_on_the_execution_authorities_revision() -> None:
    heads = [
        line for line in _alembic("postgresql://x/y", "heads").splitlines() if line
    ]
    assert len(heads) == 1
    revisions = list((REPO_ROOT / "alembic" / "versions").glob("*similarity_checks.py"))
    assert len(revisions) == 1
    assert (
        'down_revision: str | Sequence[str] | None = "b5e1c8d3a9f7"'
        in revisions[0].read_text()
    )


async def test_tables_have_tenant_rls_policies(connection: AsyncConnection) -> None:
    rows = await connection.execute(
        text("SELECT tablename, policyname FROM pg_policies WHERE schemaname='public'")
    )
    policies = {(r[0], r[1]) for r in rows}
    assert {(t, f"tenant_isolation_{t}") for t in NEW_TABLES} <= policies
    flags = await connection.execute(
        text(
            "SELECT relname, relrowsecurity FROM pg_class "
            "WHERE relname IN ('similarity_checks', 'similarity_matches', 'egress_events')"
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


async def _document(
    connection: AsyncConnection, user_id: uuid.UUID | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    document_id = uuid.uuid4()
    user_id = user_id or await _user(connection)
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


async def _check(
    connection: AsyncConnection,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    status: str = "queued",
    reason: str | None = None,
) -> uuid.UUID:
    check_id = uuid.uuid4()
    await connection.execute(
        text(
            "INSERT INTO similarity_checks (id, document_id, organization_id, "
            "user_id, status, status_reason, options, coverage, policy_versions) "
            "VALUES (:id, :doc, gen_random_uuid(), :usr, :status, :reason, '{}', "
            "'{}', '{}')"
        ),
        {
            "id": check_id,
            "doc": document_id,
            "usr": user_id,
            "status": status,
            "reason": reason,
        },
    )
    return check_id


async def _match(
    connection: AsyncConnection,
    check_id: uuid.UUID,
    *,
    source_document_id: uuid.UUID | None = None,
    **override: str,
) -> None:
    values = {
        "kind": "verbatim",
        "attribution": "unattributed",
        "origin": "openalex",
        "text_kind": "full_text",
        **override,
    }
    await connection.execute(
        text(
            "INSERT INTO similarity_matches (id, check_id, organization_id, user_id, "
            "kind, attribution, from_user_upload, doc_start, doc_end, tokens, "
            "source_origin, source_document_id, source_id, source_title_enc, "
            "source_licence, source_text_kind, src_start, src_end, normalizations_applied, "
            "declared_author_overlap, section) VALUES "
            "(gen_random_uuid(), :cid, gen_random_uuid(), gen_random_uuid(), :kind, "
            ":attribution, false, 0, 1, 12, :origin, :sdoc, 'W1', 't', '{}', "
            ":text_kind, 0, 1, '[]', false, 'body')"
        ),
        {"cid": check_id, "sdoc": source_document_id, **values},
    )


async def _egress(
    connection: AsyncConnection, check_id: uuid.UUID | None, **override: str
) -> None:
    values = {"host": "api.openalex.org", "request_class": "search", **override}
    await connection.execute(
        text(
            "INSERT INTO egress_events (id, check_id, organization_id, user_id, host, "
            "request_class, term_count, bytes_received, duration_ms, status) VALUES "
            "(gen_random_uuid(), :cid, gen_random_uuid(), gen_random_uuid(), :host, "
            ":request_class, 1, 0, 0, '200')"
        ),
        {"cid": check_id, **values},
    )


@pytest.mark.parametrize("status", JOB_STATUSES)
async def test_every_job_status_is_accepted(
    connection: AsyncConnection, status: str
) -> None:
    document_id, user_id = await _document(connection)
    await _check(connection, document_id, user_id, status=status)


@pytest.mark.parametrize("reason", [None, *RUN_JOB_REASONS])
async def test_every_reason_the_service_writes_is_accepted(
    connection: AsyncConnection, reason: str | None
) -> None:
    document_id, user_id = await _document(connection)
    await _check(connection, document_id, user_id, status="failed", reason=reason)


@pytest.mark.parametrize(
    "override",
    [
        {"status": "cancelled"},
        {"reason": "deadline_reached"},
        {"reason": "boom: exception text"},
    ],
)
async def test_unknown_statuses_and_reasons_are_rejected(
    connection: AsyncConnection, override: dict[str, str]
) -> None:
    document_id, user_id = await _document(connection)
    with pytest.raises(IntegrityError):
        await _check(connection, document_id, user_id, **override)


@pytest.mark.parametrize(
    "override",
    [
        *({"kind": kind} for kind in MATCH_KINDS),
        *({"attribution": value} for value in ATTRIBUTIONS),
        *({"origin": origin} for origin in SOURCE_ORIGINS),
        {"text_kind": "abstract"},
    ],
)
async def test_every_value_the_orm_allows_is_accepted(
    connection: AsyncConnection, override: dict[str, str]
) -> None:
    document_id, user_id = await _document(connection)
    check_id = await _check(connection, document_id, user_id)
    await _match(connection, check_id, **override)


@pytest.mark.parametrize(
    "override",
    [
        {"kind": "plagiarism"},
        {"attribution": "original"},
        {"origin": "crossref"},
        {"text_kind": "snippet"},
    ],
)
async def test_match_checks_reject_bad_rows(
    connection: AsyncConnection, override: dict[str, str]
) -> None:
    document_id, user_id = await _document(connection)
    check_id = await _check(connection, document_id, user_id)
    with pytest.raises(IntegrityError):
        await _match(connection, check_id, **override)


@pytest.mark.parametrize(
    "override",
    [{"host": "export.arxiv.org"}, {"request_class": "crawl"}],
)
async def test_egress_checks_reject_bad_rows(
    connection: AsyncConnection, override: dict[str, str]
) -> None:
    with pytest.raises(IntegrityError):
        await _egress(connection, None, **override)


@pytest.mark.parametrize("host", EGRESS_HOSTS)
@pytest.mark.parametrize("request_class", REQUEST_CLASSES)
async def test_every_host_and_request_class_is_accepted(
    connection: AsyncConnection, host: str, request_class: str
) -> None:
    await _egress(connection, None, host=host, request_class=request_class)


async def test_a_check_needs_an_existing_document(connection: AsyncConnection) -> None:
    with pytest.raises(IntegrityError):
        await _check(connection, uuid.uuid4(), await _user(connection))


async def test_a_core_delete_of_a_document_removes_its_checks_and_matches(
    connection: AsyncConnection,
) -> None:
    document_id, user_id = await _document(connection)
    check_id = await _check(connection, document_id, user_id)
    await _match(connection, check_id)
    await connection.execute(
        delete(UploadedDocument.__table__).where(
            UploadedDocument.__table__.c.id == document_id
        )
    )
    for sql in (
        "SELECT count(*) FROM similarity_checks WHERE id = :id",
        "SELECT count(*) FROM similarity_matches WHERE check_id = :id",
    ):
        assert await connection.scalar(text(sql), {"id": check_id}) == 0


async def test_deleting_a_source_document_removes_the_matches_that_cite_it(
    connection: AsyncConnection,
) -> None:
    document_id, user_id = await _document(connection)
    source_id, _ = await _document(connection, user_id)
    check_id = await _check(connection, document_id, user_id)
    await _match(connection, check_id, source_document_id=source_id)
    await _match(connection, check_id)
    await connection.execute(
        delete(UploadedDocument.__table__).where(
            UploadedDocument.__table__.c.id == source_id
        )
    )
    count = await connection.scalar(
        text("SELECT count(*) FROM similarity_matches WHERE check_id = :id"),
        {"id": check_id},
    )
    assert count == 1


async def test_deleting_a_check_keeps_its_egress_rows_without_the_link(
    connection: AsyncConnection,
) -> None:
    document_id, user_id = await _document(connection)
    check_id = await _check(connection, document_id, user_id)
    await _egress(connection, check_id)
    await connection.execute(
        text("DELETE FROM similarity_checks WHERE id = :id"), {"id": check_id}
    )
    remaining = await connection.scalar(
        text("SELECT count(*) FROM egress_events WHERE check_id IS NULL")
    )
    assert remaining >= 1


async def test_deleting_a_user_reaches_checks_through_their_documents(
    connection: AsyncConnection,
) -> None:
    document_id, user_id = await _document(connection)
    check_id = await _check(connection, document_id, user_id)
    await _match(connection, check_id)
    await connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
    assert (
        await connection.scalar(
            text("SELECT count(*) FROM similarity_matches WHERE check_id = :id"),
            {"id": check_id},
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
