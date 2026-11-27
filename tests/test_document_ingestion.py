import io
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
import structlog
from cryptography.fernet import Fernet
from fpdf import FPDF
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import Settings, settings
from src.core.contracts.redaction import snapshot_digest
from src.core.memory_encryption import DocumentEncryptionUnavailable
from src.models.db.base import Base
from src.models.db.uploaded_document import UploadedDocument, UsageLedger
from src.models.db.user import (
    User,  # noqa: F401 - the FK target must be in the metadata
)
from src.repositories.uploaded_document_repository import UploadedDocumentRepository
from src.services import document_ingestion as di
from src.services.document_ingestion import (
    DocumentIngestionError,
    extract_pdf_sandboxed,
    ingest,
    require_document_key,
    sniff_media_type,
)

ZWSP = chr(0x200B)
RLO = chr(0x202E)
ORG = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
USER = uuid.UUID("00000000-0000-0000-0000-00000000000a")
KEY = Fernet.generate_key().decode()


def _pdf(pages: list[str]) -> bytes:
    doc = FPDF()
    doc.set_creation_date(datetime(2026, 1, 1, tzinfo=UTC))
    for text in pages:
        doc.add_page()
        doc.set_font("Helvetica", size=12)
        if text:
            doc.cell(text=text)
    return bytes(doc.output())


def _pdf_with_zero_width(pages: list[str]) -> bytes:
    """Pages whose text may contain U+200B, which Helvetica cannot emit."""
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): DictionaryObject(
                {
                    NameObject("/BaseEncoding"): NameObject("/WinAnsiEncoding"),
                    NameObject("/Differences"): ArrayObject(
                        [NumberObject(1), NameObject("/uni200B")]
                    ),
                }
            ),
        }
    )
    font_ref = writer._add_object(font)
    for text in pages:
        page = writer.add_blank_page(612, 792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        stream = DecodedStreamObject()
        body = text.replace(ZWSP, "\x01").encode("latin-1")
        stream.set_data(b"BT /F1 12 Tf 50 700 Td (" + body + b") Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture(autouse=True)
def _document_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_ENCRYPTION_KEY", KEY)


@pytest_asyncio.fixture(name="session")
async def session_fixture() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def _ingest(
    session: AsyncSession,
    raw: bytes,
    media: str = "text/plain",
    *,
    authors: list[str] | None = None,
    filename: str = "paper.txt",
) -> UploadedDocument:
    return await ingest(
        raw,
        media,
        filename,
        authors or [],
        organization_id=ORG,
        user_id=USER,
        session=session,
    )


async def _code(coro: object) -> str:
    with pytest.raises(DocumentIngestionError) as caught:
        await coro  # type: ignore[misc]
    return caught.value.code


def test_sniffing_classifies_and_requires_the_declared_type_to_agree() -> None:
    assert sniff_media_type(b"%PDF-1.7\n...", "application/pdf") == "pdf"
    assert sniff_media_type("héllo".encode(), "text/plain") == "plain"
    assert sniff_media_type(b"# hi", "text/markdown; charset=utf-8") == "markdown"


@pytest.mark.parametrize(
    ("raw", "declared", "code"),
    [
        (b"abc\x00def", "text/plain", "unsupported_type"),
        ("café".encode("latin-1"), "text/plain", "unsupported_type"),
        (b"%PDF-1.7", "text/plain", "type_mismatch"),
        (b"plain", "application/pdf", "type_mismatch"),
        (b"plain", "application/zip", "unsupported_type"),
    ],
)
def test_sniffing_refuses(raw: bytes, declared: str, code: str) -> None:
    with pytest.raises(DocumentIngestionError) as caught:
        sniff_media_type(raw, declared)
    assert caught.value.code == code


async def test_text_ingest_stores_encrypted_normalized_text(
    session: AsyncSession,
) -> None:
    raw = f"line one\r\nsec{ZWSP}ond{RLO} line".encode()
    row = await _ingest(session, raw, authors=["Ada"], filename="../dir/my notes.txt")
    repo = UploadedDocumentRepository(session, di.document_cipher())
    extracted = await repo.get_text(row.id, organization_id=ORG, user_id=USER)
    assert extracted is not None
    assert extracted.text == "line one\nsecond line"
    assert extracted.text_sha256 == snapshot_digest(extracted.text.encode())
    assert extracted.source_sha256 == snapshot_digest(raw)
    assert [(s.page, s.start, s.end) for s in extracted.pages] == [(1, 0, 20)]
    assert row.invisible_chars_removed == 2
    assert row.media_type == "plain"
    assert row.text_enc != extracted.text
    meta = await repo.get_meta(row.id, organization_id=ORG, user_id=USER)
    assert meta is not None and meta.filename == "my notes.txt"
    assert meta.declared_authors == ["Ada"]


async def test_oversized_upload_is_refused_before_anything_else(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_MAX_BYTES", 10)
    assert await _code(_ingest(session, b"x" * 11)) == "too_large"


async def test_text_over_the_character_cap_is_refused_not_truncated(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_MAX_CHARS", 5)
    assert await _code(_ingest(session, b"abcdefg")) == "too_much_text"


async def test_empty_text_is_refused(session: AsyncSession) -> None:
    assert await _code(_ingest(session, b"\xef\xbb\xbf")) == "no_text"


@pytest.mark.parametrize(
    "authors", [["a"] * 21, ["x" * 101]], ids=["too-many", "too-long"]
)
async def test_declared_authors_are_bounded(
    session: AsyncSession, authors: list[str]
) -> None:
    assert await _code(_ingest(session, b"text", authors=authors)) == "invalid_authors"


async def test_pdf_ingest_goes_through_the_sandbox(session: AsyncSession) -> None:
    raw = _pdf(["alpha page one", "bravo page two"])
    row = await _ingest(session, raw, "application/pdf", filename="p.pdf")
    repo = UploadedDocumentRepository(session, di.document_cipher())
    extracted = await repo.get_text(row.id, organization_id=ORG, user_id=USER)
    assert extracted is not None
    assert row.media_type == "pdf" and row.page_count == 2
    assert extracted.page_of(extracted.text.index("bravo")) == 2
    assert row.extractor_version.startswith("pypdf-")


async def test_pdf_refusals_carry_typed_codes(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.encrypt("pw")
    locked = io.BytesIO()
    writer.write(locked)
    assert await _code(_ingest(session, locked.getvalue(), "application/pdf")) == (
        "encrypted_pdf"
    )
    assert await _code(_ingest(session, _pdf([""] * 3), "application/pdf")) == (
        "no_text"
    )
    assert await _code(_ingest(session, b"%PDF-1.7 garbage", "application/pdf")) == (
        "malformed_pdf"
    )
    monkeypatch.setattr(settings, "DOCUMENT_MAX_PAGES", 2)
    assert await _code(_ingest(session, _pdf(["a"] * 3), "application/pdf")) == (
        "too_many_pages"
    )


async def test_pdf_text_cap_is_enforced_in_the_child(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_MAX_CHARS", 10)
    assert await _code(_ingest(session, _pdf(["a" * 40]), "application/pdf")) == (
        "too_much_text"
    )


async def test_zero_width_characters_are_counted_and_offsets_stay_true(
    session: AsyncSession,
) -> None:
    raw = _pdf_with_zero_width([f"a{ZWSP}b{ZWSP}c{ZWSP} page one", "second page"])
    extracted, removed = await extract_pdf_sandboxed(raw)
    assert removed == 3
    assert ZWSP not in extracted.text
    assert extracted.page_of(extracted.text.index("second")) == 2
    assert extracted.text_sha256 == snapshot_digest(extracted.text.encode())

    row = await _ingest(session, raw, "application/pdf")
    assert row.invisible_chars_removed == 3


async def test_a_tampered_child_reply_is_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def lying_child(*_: object, **__: object) -> bytes:
        return json.dumps(
            {
                "ok": True,
                "text": "hello",
                "pages": [[1, 0, 99]],
                "source_sha256": "0" * 64,
                "text_sha256": snapshot_digest(b"hello"),
                "invisible_chars_removed": 0,
            }
        ).encode()

    monkeypatch.setattr(di, "_run_child", lying_child)
    with pytest.raises(DocumentIngestionError) as caught:
        await extract_pdf_sandboxed(_pdf(["x"]))
    assert caught.value.code == "malformed_pdf"


async def test_ingest_writes_no_ledger_row(session: AsyncSession) -> None:
    await _ingest(session, b"some text")
    assert (await session.execute(select(UsageLedger))).first() is None


async def test_ingest_fails_closed_without_a_key(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_ENCRYPTION_KEY", "")
    monkeypatch.setenv("MEMORY_ENCRYPTION_KEY", KEY)
    with pytest.raises(DocumentEncryptionUnavailable):
        await _ingest(session, b"some text")


@pytest.mark.parametrize("key", ["", "   "])
def test_startup_refuses_an_enabled_feature_with_a_blank_key(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    monkeypatch.setenv("MEMORY_ENCRYPTION_KEY", KEY)
    config = Settings(
        ENVIRONMENT="development",
        DOCUMENT_UPLOADS_ENABLED=True,
        DOCUMENT_ENCRYPTION_KEY=key,
    )
    with pytest.raises(DocumentEncryptionUnavailable):
        require_document_key(config)


def test_startup_refuses_similarity_checks_without_a_key() -> None:
    config = Settings(
        ENVIRONMENT="development",
        SIMILARITY_CHECKS_ENABLED=True,
        DOCUMENT_ENCRYPTION_KEY="",
    )
    with pytest.raises(DocumentEncryptionUnavailable):
        require_document_key(config)


def test_startup_allows_a_disabled_feature_or_a_set_key() -> None:
    require_document_key(Settings(DOCUMENT_UPLOADS_ENABLED=False))
    require_document_key(
        Settings(DOCUMENT_UPLOADS_ENABLED=True, DOCUMENT_ENCRYPTION_KEY=KEY)
    )


async def test_logs_never_contain_document_content(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    sentinel = "SENTINEL-7f3a9c-do-not-log"
    caplog.set_level(logging.DEBUG)
    with structlog.testing.capture_logs() as captured:
        await _ingest(session, f"fine {sentinel}".encode(), filename=f"{sentinel}.txt")
        await _code(
            _ingest(
                session,
                f"%PDF-1.7 {sentinel}".encode(),
                "application/pdf",
                filename=f"{sentinel}.pdf",
            )
        )
    assert captured, "ingest logged nothing, so this test proves nothing"
    assert sentinel not in caplog.text
    assert sentinel not in repr(captured)


async def test_non_ascii_pdf_text_fits_the_stdout_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_MAX_CHARS", 1000)
    monkeypatch.setattr(di, "_STDOUT_SLACK", 0)
    pdf = _pdf_with_zero_width(["é" * 900])
    extracted, _ = await extract_pdf_sandboxed(pdf)
    assert len(extracted.text) == 900


def test_text_that_mentions_the_pdf_header_is_still_text() -> None:
    raw = b"# Notes\nThe PDF header is %PDF-1.7 at file start.\n"
    assert sniff_media_type(raw, "text/markdown") == "markdown"


async def test_pdf_page_separators_count_toward_the_character_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_MAX_CHARS", 10)
    with pytest.raises(DocumentIngestionError) as caught:
        await extract_pdf_sandboxed(_pdf(["a" * 5, "a" * 5]))
    assert caught.value.code == "too_much_text"


async def test_stripping_then_normalizing_leaves_nfc_text(
    session: AsyncSession,
) -> None:
    row = await _ingest(session, f"e{ZWSP}́".encode())
    repo = UploadedDocumentRepository(session, di.document_cipher())
    extracted = await repo.get_text(row.id, organization_id=ORG, user_id=USER)
    assert extracted is not None and extracted.text == "é"


async def test_list_recent_clamps_a_non_positive_limit(
    session: AsyncSession,
) -> None:
    repo = UploadedDocumentRepository(session, di.document_cipher())
    for _ in range(3):
        await _ingest(session, b"text")
    rows = await repo.list_recent(organization_id=ORG, user_id=USER, limit=-1)
    assert len(rows) == 1


def _reply(**override: object) -> bytes:
    reply: dict[str, object] = {
        "ok": True,
        "text": "hello",
        "pages": [[1, 0, 5]],
        "source_sha256": "0" * 64,
        "text_sha256": snapshot_digest(b"hello"),
        "invisible_chars_removed": 0,
    }
    return json.dumps({**reply, **override}).encode()


@pytest.mark.parametrize(
    "raw",
    [
        _reply(text=["h"]),
        _reply(text=""),
        _reply(text="", pages=[[1, 0, 0]], text_sha256=snapshot_digest(b"")),
        _reply(pages=[[True, 0.0, 5.0]]),
        _reply(pages=[[1, 0, 5.5]]),
        b"[" * 100_000,
    ],
    ids=["text-list", "empty-text", "empty-span", "bool-float", "float", "deep"],
)
def test_hostile_child_replies_are_malformed_not_exceptions(raw: bytes) -> None:
    with pytest.raises(DocumentIngestionError) as caught:
        di._parse_reply(raw)
    assert caught.value.code == "malformed_pdf"


def test_a_reply_over_the_character_cap_is_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_MAX_CHARS", 3)
    with pytest.raises(DocumentIngestionError) as caught:
        di._parse_reply(_reply())
    assert caught.value.code == "malformed_pdf"


async def test_text_normalization_runs_off_the_event_loop(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    seen: list[int] = []
    real = di._extract_text

    def spy(raw: bytes) -> object:
        seen.append(threading.get_ident())
        return real(raw)

    monkeypatch.setattr(di, "_extract_text", spy)
    await _ingest(session, b"text")
    assert seen and seen[0] != threading.get_ident()


def test_a_reply_with_more_pages_than_allowed_or_unstripped_text_is_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_MAX_PAGES", 2)
    spans = [[i, 0, 0] for i in range(1, 4)]
    with pytest.raises(DocumentIngestionError) as many:
        di._parse_reply(_reply(pages=spans))
    assert many.value.code == "malformed_pdf"
    dirty = "a" + chr(0x202E) + "b"
    with pytest.raises(DocumentIngestionError) as hidden:
        di._parse_reply(
            _reply(
                text=dirty,
                pages=[[1, 0, 3]],
                text_sha256=snapshot_digest(dirty.encode()),
            )
        )
    assert hidden.value.code == "malformed_pdf"


@pytest.mark.parametrize("authors", [[5], [None], [["x"]], [{"k": "v"}]])
async def test_non_string_authors_are_refused(
    session: AsyncSession, authors: list[object]
) -> None:
    code = await _code(_ingest(session, b"text", authors=authors))  # type: ignore[arg-type]
    assert code == "invalid_authors"


async def test_author_names_are_stripped_of_invisible_characters(
    session: AsyncSession,
) -> None:
    row = await _ingest(session, b"text", authors=[f"Ad{RLO}a"])
    repo = UploadedDocumentRepository(session, di.document_cipher())
    meta = await repo.get_meta(row.id, organization_id=ORG, user_id=USER)
    assert meta is not None and meta.declared_authors == ["Ada"]


async def test_an_oversized_text_is_refused_before_any_per_character_work(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_MAX_CHARS", 5)

    def boom(_: str) -> None:
        raise AssertionError("stripped an oversized text")

    monkeypatch.setattr(di, "strip_invisible", boom)
    assert await _code(_ingest(session, b"abcdefghij")) == "too_much_text"


def _tampered(monkeypatch: pytest.MonkeyPatch, data: bytes, **override: object) -> None:
    async def fake_child(*_: object, **__: object) -> bytes:
        return _reply(source_sha256=snapshot_digest(data), **override)

    monkeypatch.setattr(di, "_run_child", fake_child)


@pytest.mark.parametrize(
    "override",
    [
        {"source_sha256": "0" * 64},
        {"text_sha256": snapshot_digest(b"other")},
        {"pages": [[2, 0, 5]]},
        {"pages": [[1, 0, 3], [2, 2, 5]]},
        {"pages": [[1, 0, 6]]},
        {"pages": [[1, 3, 2]]},
        {"pages": []},
        {"invisible_chars_removed": -1},
        {"invisible_chars_removed": True},
        {"ok": False, "error": "something_new"},
    ],
    ids=[
        "source-hash",
        "text-hash",
        "page-number",
        "overlap",
        "past-end",
        "inverted",
        "no-pages",
        "negative-count",
        "bool-count",
        "unknown-error",
    ],
)
async def test_every_check_on_the_child_reply_is_load_bearing(
    monkeypatch: pytest.MonkeyPatch, override: dict[str, object]
) -> None:
    data = b"%PDF-1.7 x"
    if "source_sha256" not in override:
        _tampered(monkeypatch, data, **override)
    else:
        _tampered(monkeypatch, data)

        async def fake_child(*_: object, **__: object) -> bytes:
            return _reply(**override)

        monkeypatch.setattr(di, "_run_child", fake_child)
    with pytest.raises(DocumentIngestionError) as caught:
        await extract_pdf_sandboxed(data)
    assert caught.value.code == "malformed_pdf"


async def test_a_known_child_error_code_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_child(*_: object, **__: object) -> bytes:
        return json.dumps({"ok": False, "error": "encrypted_pdf"}).encode()

    monkeypatch.setattr(di, "_run_child", fake_child)
    with pytest.raises(DocumentIngestionError) as caught:
        await extract_pdf_sandboxed(b"%PDF-1.7")
    assert caught.value.code == "encrypted_pdf"


async def test_the_child_is_given_the_configured_limits_and_stdout_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    async def fake_child(
        argv: list[str], data: bytes, *, timeout: float, max_out: int
    ) -> bytes:
        seen.update(argv=argv, timeout=timeout, max_out=max_out)
        return _reply(source_sha256=snapshot_digest(data))

    monkeypatch.setattr(di, "_run_child", fake_child)
    monkeypatch.setattr(settings, "DOCUMENT_MAX_CHARS", 1000)
    monkeypatch.setattr(settings, "DOCUMENT_MAX_PAGES", 7)
    monkeypatch.setattr(settings, "DOCUMENT_EXTRACT_TIMEOUT_S", 9)
    await extract_pdf_sandboxed(b"%PDF-1.7")
    assert seen["argv"][-2:] == ["7", "1000"]  # type: ignore[index]
    assert seen["argv"][:3] == ["-I", "-B", "-m"]  # type: ignore[index]
    assert seen["timeout"] == 9
    assert seen["max_out"] == 1000 * 6 + di._STDOUT_SLACK


async def test_text_exactly_at_the_character_limit_is_accepted(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_MAX_CHARS", 5)
    assert (await _ingest(session, b"abcde")).char_count == 5
    assert await _code(_ingest(session, b"abcdef")) == "too_much_text"


async def test_authors_at_the_limits_are_accepted(session: AsyncSession) -> None:
    row = await _ingest(session, b"t", authors=["a" * 100] * 20)
    assert row.id


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        ("a" * 300 + ".txt", ("a" * 255)),
        ("  spaced.txt  ", "spaced.txt"),
        ("", "document"),
        ("dir\\\\sub\\\\name.txt", "name.txt"),
        ("na\x00m\x07e.txt", "name.txt"),
        ("../../etc/x.txt", "x.txt"),
    ],
)
async def test_filenames_are_reduced_to_a_safe_basename(
    session: AsyncSession, given: str, stored: str
) -> None:
    row = await _ingest(session, b"t", filename=given)
    repo = UploadedDocumentRepository(session, di.document_cipher())
    meta = await repo.get_meta(row.id, organization_id=ORG, user_id=USER)
    assert meta is not None and meta.filename == stored


def test_the_pdf_header_may_sit_anywhere_in_the_first_1024_bytes() -> None:
    binary = b"\xff" * 1019 + b"%PDF-1.7"
    assert sniff_media_type(binary, "application/pdf") == "pdf"
    late = b"\xff" * 1100 + b"%PDF-1.7"
    with pytest.raises(DocumentIngestionError):
        sniff_media_type(late, "application/pdf")
    assert sniff_media_type(b"hi", "TEXT/Plain; charset=utf-8") == "plain"


async def test_expiry_is_the_retention_period_from_now(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_RETENTION_DAYS", 7)
    row = await _ingest(session, b"t")
    expires = row.expires_at.replace(tzinfo=UTC)
    delta = expires - datetime.now(UTC)
    assert timedelta(days=6, hours=23) < delta <= timedelta(days=7)


def test_startup_rejects_a_malformed_key() -> None:
    with pytest.raises(ValueError):
        require_document_key(
            Settings(DOCUMENT_UPLOADS_ENABLED=True, DOCUMENT_ENCRYPTION_KEY="not-a-key")
        )


async def test_page_and_extractor_metadata_are_stored(
    session: AsyncSession,
) -> None:
    row = await _ingest(session, _pdf(["alpha", "bravo"]), "application/pdf")
    assert row.page_count == 2 and row.extractor_version.startswith("pypdf-")
    text_row = await _ingest(session, b"t")
    assert text_row.page_count is None
    assert text_row.extractor_version.startswith("text-utf8/")


async def test_the_character_cap_applies_after_normalization(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_MAX_CHARS", 10)
    expands = "\u0958" * 8  # 8 characters that normalize to 16
    assert await _code(_ingest(session, expands.encode())) == "too_much_text"


@pytest.mark.parametrize("removed", [10**30, 21 << 20])
def test_an_absurd_removed_count_in_a_reply_is_malformed(removed: int) -> None:
    with pytest.raises(DocumentIngestionError) as caught:
        di._parse_reply(_reply(invisible_chars_removed=removed))
    assert caught.value.code == "malformed_pdf"


async def test_reply_verification_runs_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    seen: list[int] = []
    real = di._parse_reply

    def spy(raw: bytes) -> object:
        seen.append(threading.get_ident())
        return real(raw)

    async def fake_child(*_: object, **__: object) -> bytes:
        return _reply(source_sha256=snapshot_digest(b"%PDF-1.7"))

    monkeypatch.setattr(di, "_parse_reply", spy)
    monkeypatch.setattr(di, "_run_child", fake_child)
    await extract_pdf_sandboxed(b"%PDF-1.7")
    assert seen and seen[0] != threading.get_ident()
