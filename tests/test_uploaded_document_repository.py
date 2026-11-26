import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.acquisition.pdf_text import ExtractedText, PageSpan
from src.core.contracts.redaction import snapshot_digest
from src.core.memory_encryption import MemoryEncryption
from src.models.db.base import Base
from src.models.db.uploaded_document import UploadedDocument, UsageLedger
from src.repositories.tenant_scope import MissingOrganizationContextError
from src.repositories.uploaded_document_repository import UploadedDocumentRepository

ORG_A = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
ORG_B = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
USER_A = uuid.UUID("00000000-0000-0000-0000-00000000000a")
USER_B = uuid.UUID("00000000-0000-0000-0000-00000000000b")
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
TEXT = "alpha\nbravo"


@pytest.fixture(name="cipher")
def cipher_fixture() -> MemoryEncryption:
    return MemoryEncryption(Fernet.generate_key().decode(), env_fallback=False)


@pytest_asyncio.fixture(name="session")
async def session_fixture() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


def _extracted() -> ExtractedText:
    return ExtractedText(
        text=TEXT,
        pages=(PageSpan(1, 0, 5), PageSpan(2, 6, 11)),
        text_sha256=snapshot_digest(TEXT.encode()),
        source_sha256=snapshot_digest(b"raw"),
    )


async def _create(
    repo: UploadedDocumentRepository,
    *,
    org: uuid.UUID = ORG_A,
    user: uuid.UUID = USER_A,
    expires_at: datetime | None = None,
) -> uuid.UUID:
    row = await repo.create(
        organization_id=org,
        user_id=user,
        filename="paper.pdf",
        media_type="pdf",
        extracted=_extracted(),
        extractor_version="pypdf-7.1.1/normalizer-1",
        invisible_chars_removed=3,
        declared_authors=["Ada Lovelace"],
        expires_at=expires_at or NOW + timedelta(days=30),
    )
    return row.id


async def test_round_trip_decrypts_only_in_the_two_read_methods(
    session: AsyncSession, cipher: MemoryEncryption
) -> None:
    repo = UploadedDocumentRepository(session, cipher)
    doc_id = await _create(repo)

    stored = (await session.execute(select(UploadedDocument))).scalar_one()
    assert TEXT not in stored.text_enc
    assert "paper.pdf" not in stored.filename_enc
    assert "Ada" not in stored.declared_authors_enc

    meta = await repo.get_meta(doc_id, organization_id=ORG_A, user_id=USER_A)
    assert meta is not None
    assert (meta.filename, meta.declared_authors) == ("paper.pdf", ["Ada Lovelace"])
    assert meta.invisible_chars_removed == 3

    extracted = await repo.get_text(doc_id, organization_id=ORG_A, user_id=USER_A)
    assert extracted == _extracted()
    assert extracted is not None
    assert extracted.page_of(extracted.text.index("bravo")) == 2


async def test_other_users_and_orgs_see_nothing(
    session: AsyncSession, cipher: MemoryEncryption
) -> None:
    repo = UploadedDocumentRepository(session, cipher)
    doc_id = await _create(repo)
    for org, user in ((ORG_A, USER_B), (ORG_B, USER_A)):
        assert await repo.get_meta(doc_id, organization_id=org, user_id=user) is None
        assert await repo.get_text(doc_id, organization_id=org, user_id=user) is None
        assert await repo.list_recent(organization_id=org, user_id=user) == []
        assert not await repo.delete(doc_id, organization_id=org, user_id=user)
    assert await repo.get_meta(doc_id, organization_id=ORG_A, user_id=USER_A)


async def test_a_missing_identity_is_refused(
    session: AsyncSession, cipher: MemoryEncryption
) -> None:
    repo = UploadedDocumentRepository(session, cipher)
    with pytest.raises(MissingOrganizationContextError):
        await repo.list_recent(organization_id=ORG_A, user_id=None)  # type: ignore[arg-type]


async def test_list_recent_is_newest_first_and_capped(
    session: AsyncSession, cipher: MemoryEncryption
) -> None:
    repo = UploadedDocumentRepository(session, cipher)
    for _ in range(55):
        await _create(repo)
    rows = await repo.list_recent(organization_id=ORG_A, user_id=USER_A, limit=500)
    assert len(rows) == 50
    assert [r.created_at for r in rows] == sorted(
        (r.created_at for r in rows), reverse=True
    )


async def test_delete_removes_the_row_but_not_ledger_rows(
    session: AsyncSession, cipher: MemoryEncryption
) -> None:
    repo = UploadedDocumentRepository(session, cipher)
    doc_id = await _create(repo)
    session.add(
        UsageLedger(organization_id=ORG_A, user_id=USER_A, kind="upload", units=1)
    )
    await session.flush()
    assert await repo.delete(doc_id, organization_id=ORG_A, user_id=USER_A)
    assert await repo.get_meta(doc_id, organization_id=ORG_A, user_id=USER_A) is None
    ledger = (await session.execute(select(UsageLedger))).scalars().all()
    assert len(ledger) == 1


async def test_get_text_refuses_a_plaintext_column(
    session: AsyncSession, cipher: MemoryEncryption
) -> None:
    repo = UploadedDocumentRepository(session, cipher)
    doc_id = await _create(repo)
    await session.execute(
        text("UPDATE uploaded_documents SET text_enc = :t"), {"t": TEXT}
    )
    with pytest.raises(ValueError, match="not encrypted"):
        await repo.get_text(doc_id, organization_id=ORG_A, user_id=USER_A)


async def test_purge_expired_deletes_only_expired_rows_and_is_idempotent(
    session: AsyncSession, cipher: MemoryEncryption
) -> None:
    repo = UploadedDocumentRepository(session, cipher)
    expired = await _create(repo, expires_at=NOW)
    await _create(repo, expires_at=NOW + timedelta(seconds=1))
    assert await repo.purge_expired(NOW) == 1
    assert await repo.purge_expired(NOW) == 0
    assert await repo.get_meta(expired, organization_id=ORG_A, user_id=USER_A) is None
    assert len(await repo.list_recent(organization_id=ORG_A, user_id=USER_A)) == 1


async def test_authors_are_stored_as_encrypted_json(
    session: AsyncSession, cipher: MemoryEncryption
) -> None:
    repo = UploadedDocumentRepository(session, cipher)
    await _create(repo)
    stored = (await session.execute(select(UploadedDocument))).scalar_one()
    assert json.loads(cipher.decrypt_text(stored.declared_authors_enc) or "") == [
        "Ada Lovelace"
    ]


async def test_metadata_reads_do_not_load_the_encrypted_body(
    session: AsyncSession, cipher: MemoryEncryption
) -> None:
    repo = UploadedDocumentRepository(session, cipher)
    doc_id = await _create(repo)
    session.expunge_all()
    row = await repo._get(doc_id, ORG_A, USER_A)
    assert row is not None
    assert {"text_enc", "pages"} <= sa_inspect(row).unloaded
