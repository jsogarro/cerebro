"""The uploaded-documents migration enforces what the ORM declares."""

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
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.models.db.base import Base
from src.models.db.uploaded_document import UploadedDocument, UsageLedger  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = Path(sys.executable).parent / "alembic"
NEW_TABLES = frozenset({"uploaded_documents", "usage_ledger"})


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


async def test_tables_have_tenant_rls_policies(connection: AsyncConnection) -> None:
    rows = await connection.execute(
        text("SELECT tablename, policyname FROM pg_policies WHERE schemaname='public'")
    )
    policies = {(r[0], r[1]) for r in rows}
    assert {(t, f"tenant_isolation_{t}") for t in NEW_TABLES} <= policies
    flags = await connection.execute(
        text(
            "SELECT relname, relrowsecurity FROM pg_class "
            "WHERE relname IN ('uploaded_documents', 'usage_ledger')"
        )
    )
    assert dict(flags.all()) == {"uploaded_documents": True, "usage_ledger": True}


async def test_every_orm_check_exists_in_the_migrated_database(
    connection: AsyncConnection,
) -> None:
    declared = {
        (table.name, constraint.name)
        for table in Base.metadata.tables.values()
        if table.name in NEW_TABLES
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }
    rows = await connection.execute(
        text(
            "SELECT rel.relname, con.conname FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid WHERE con.contype = 'c'"
        )
    )
    migrated = {(r[0], r[1]) for r in rows if r[0] in NEW_TABLES}
    assert declared and declared <= migrated


async def test_every_orm_index_exists_in_the_migrated_database(
    connection: AsyncConnection,
) -> None:
    declared = {
        (table.name, index.name)
        for table in Base.metadata.tables.values()
        if table.name in NEW_TABLES
        for index in table.indexes
    }
    rows = await connection.execute(
        text("SELECT tablename, indexname FROM pg_indexes WHERE schemaname='public'")
    )
    assert declared <= {(r[0], r[1]) for r in rows}


async def _insert_document(connection: AsyncConnection, **override: object) -> None:
    values = {
        "id": uuid.uuid4(),
        "org": uuid.uuid4(),
        "usr": uuid.uuid4(),
        "media": "pdf",
        "chars": 5,
        **override,
    }
    await connection.execute(
        text(
            "INSERT INTO uploaded_documents (id, organization_id, user_id, "
            "filename_enc, media_type, source_sha256, text_sha256, char_count, "
            "pages, extractor_version, invisible_chars_removed, text_enc, "
            "declared_authors_enc, expires_at) VALUES (:id, :org, :usr, 'f', "
            ":media, 's', 't', :chars, '[]', 'v', 0, 'x', 'y', now())"
        ),
        values,
    )


@pytest.mark.parametrize("override", [{"media": "docx"}, {"chars": 0}])
async def test_document_checks_reject_bad_rows(
    connection: AsyncConnection, override: dict[str, object]
) -> None:
    with pytest.raises(IntegrityError):
        await _insert_document(connection, **override)


async def test_ledger_kind_check_rejects_an_unknown_kind(
    connection: AsyncConnection,
) -> None:
    with pytest.raises(IntegrityError):
        await connection.execute(
            text(
                "INSERT INTO usage_ledger (id, organization_id, user_id, kind, units) "
                "VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), "
                "'bogus', 1)"
            )
        )


async def test_downgrade_removes_tables_and_policies(
    url: str, server: PostgresContainer
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
    finally:
        await engine.dispose()
        _alembic(url, "upgrade", "head")
