"""Turn an untrusted upload into an encrypted, owner-private document row.

Size check, magic-byte sniff, extraction (PDFs in a killable child with a
scrubbed environment), invisible-character stripping, encryption, storage.
It writes no usage-ledger row: the caller reserves quota before parsing.
"""

import asyncio
import json
import sys
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final, cast

from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.core.acquisition import extract_child as _child
from src.core.acquisition.pdf_text import (
    NORMALIZER_VERSION,
    ExtractedText,
    PageSpan,
    _normalize,
    extractor_pin,
)
from src.core.config import Settings, settings
from src.core.contracts.redaction import snapshot_digest
from src.core.memory_encryption import (
    DocumentEncryptionUnavailable,
    MemoryEncryption,
    document_cipher,
)
from src.core.untrusted_text import strip_invisible
from src.models.db.uploaded_document import UploadedDocument
from src.repositories.uploaded_document_repository import UploadedDocumentRepository

logger = get_logger()

__all__ = [
    "DocumentEncryptionUnavailable",
    "DocumentIngestionError",
    "document_cipher",
    "extract_pdf_sandboxed",
    "ingest",
    "require_document_key",
    "sniff_media_type",
]

_CHILD_ENV: Final = {"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0", "LANG": "C.UTF-8"}
_STDOUT_SLACK = 1 << 20
_EXTRACT_SLOTS = asyncio.Semaphore(2)
_DECLARED_TYPES = {
    "application/pdf": "pdf",
    "text/markdown": "markdown",
    "text/plain": "plain",
}
_MAX_AUTHORS = 20
_MAX_AUTHOR_CHARS = 100
_MAX_FILENAME_CHARS = 255
_CHILD_CODES = frozenset(
    {
        "too_many_pages",
        "too_much_text",
        "encrypted_pdf",
        "no_text",
        "unsupported_type",
        "malformed_pdf",
    }
)


class DocumentIngestionError(Exception):
    """An upload was refused; ``code`` is stable and safe to show the caller."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def require_document_key(config: Settings) -> None:
    """Refuse to start with an upload feature on and no usable key."""
    if config.DOCUMENT_UPLOADS_ENABLED:
        key = config.DOCUMENT_ENCRYPTION_KEY.strip()
        if not key:
            raise DocumentEncryptionUnavailable(
                "DOCUMENT_UPLOADS_ENABLED needs DOCUMENT_ENCRYPTION_KEY"
            )
        MemoryEncryption(key, env_fallback=False)


def _is_text(raw: bytes) -> bool:
    if b"\x00" in raw:
        return False
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def sniff_media_type(raw: bytes, declared: str) -> str:
    """Return ``pdf``, ``markdown`` or ``plain``; the declared type must agree."""
    wanted = _DECLARED_TYPES.get(declared.partition(";")[0].strip().lower())
    if wanted is None:
        raise DocumentIngestionError("unsupported_type")
    is_text = _is_text(raw)
    if raw.startswith(b"%PDF-") or (b"%PDF-" in raw[:1024] and not is_text):
        found = "pdf"
    elif is_text:
        found = "plain"
    else:
        raise DocumentIngestionError("unsupported_type")
    if (found == "pdf") != (wanted == "pdf"):
        raise DocumentIngestionError("type_mismatch")
    return wanted


def _child_env(test_env: dict[str, str] | None) -> dict[str, str]:
    return {**_CHILD_ENV, **(test_env or {})}


async def _feed(stdin: asyncio.StreamWriter, data: bytes) -> None:
    try:
        stdin.write(data)
        await stdin.drain()
        stdin.close()
    except (BrokenPipeError, ConnectionResetError):
        pass


async def _run_child(data: bytes, env: dict[str, str], args: list[str]) -> bytes:
    async with _EXTRACT_SLOTS:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-m",
            _child.__name__,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        try:
            return await asyncio.wait_for(
                _exchange(proc, data), settings.DOCUMENT_EXTRACT_TIMEOUT_S
            )
        except TimeoutError:
            raise DocumentIngestionError("extraction_timeout") from None
        finally:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()


async def _exchange(proc: asyncio.subprocess.Process, data: bytes) -> bytes:
    stdout = cast(asyncio.StreamReader, proc.stdout)
    cap = settings.DOCUMENT_MAX_CHARS * 6 + _STDOUT_SLACK
    feeder = asyncio.create_task(_feed(cast(asyncio.StreamWriter, proc.stdin), data))
    out = bytearray()
    try:
        while chunk := await stdout.read(65536):
            out += chunk
            if len(out) > cap:
                raise DocumentIngestionError("malformed_pdf")
    finally:
        feeder.cancel()
    return bytes(out)


def _verify_reply(reply: Any) -> tuple[ExtractedText, int]:
    if not reply["ok"]:
        code = reply["error"]
        raise DocumentIngestionError(code if code in _CHILD_CODES else "malformed_pdf")
    text, removed = reply["text"], reply["invisible_chars_removed"]
    if (
        type(text) is not str
        or not 0 < len(text) <= settings.DOCUMENT_MAX_CHARS
        or type(removed) is not int
        or removed < 0
        or reply["text_sha256"] != snapshot_digest(text.encode("utf-8"))
    ):
        raise ValueError("child reply failed verification")
    spans: list[PageSpan] = []
    previous_end = 0
    for number, span in enumerate(reply["pages"], start=1):
        page, start, end = span
        if (
            any(type(value) is not int for value in span)
            or page != number
            or not previous_end <= start <= end <= len(text)
        ):
            raise ValueError("page span outside the text")
        spans.append(PageSpan(page, start, end))
        previous_end = end
    if not spans:
        raise ValueError("no pages")
    return (
        ExtractedText(
            text=text,
            pages=tuple(spans),
            text_sha256=reply["text_sha256"],
            source_sha256=reply["source_sha256"],
        ),
        removed,
    )


def _parse_reply(raw_reply: bytes) -> tuple[ExtractedText, int]:
    """Treat the child's reply as hostile: any bad shape is ``malformed_pdf``."""
    try:
        return _verify_reply(json.loads(raw_reply))
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        raise DocumentIngestionError("malformed_pdf") from None


async def extract_pdf_sandboxed(
    data: bytes, *, _test_child_env: dict[str, str] | None = None
) -> tuple[ExtractedText, int]:
    """Extract a PDF in a child process; return the text and removed-char count."""
    raw_reply = await _run_child(
        data,
        _child_env(_test_child_env),
        [str(settings.DOCUMENT_MAX_PAGES), str(settings.DOCUMENT_MAX_CHARS)],
    )
    extracted, removed = _parse_reply(raw_reply)
    if extracted.source_sha256 != snapshot_digest(data):
        raise DocumentIngestionError("malformed_pdf")
    return extracted, removed


def _extract_text(raw: bytes) -> tuple[ExtractedText, int]:
    text, removed = strip_invisible(raw.decode("utf-8"))
    text = _normalize(text)
    if len(text) > settings.DOCUMENT_MAX_CHARS:
        raise DocumentIngestionError("too_much_text")
    if not text:
        raise DocumentIngestionError("no_text")
    return (
        ExtractedText(
            text=text,
            pages=(PageSpan(1, 0, len(text)),),
            text_sha256=snapshot_digest(text.encode("utf-8")),
            source_sha256=snapshot_digest(raw),
        ),
        removed,
    )


def _clean_filename(filename: str) -> str:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if unicodedata.category(c)[0] != "C")
    return name.strip()[:_MAX_FILENAME_CHARS] or "document"


def _check_authors(authors: list[str]) -> None:
    if len(authors) > _MAX_AUTHORS or any(len(a) > _MAX_AUTHOR_CHARS for a in authors):
        raise DocumentIngestionError("invalid_authors")


async def _extract(
    raw: bytes,
    declared_media_type: str,
    declared_authors: list[str],
    test_child_env: dict[str, str] | None,
) -> tuple[str, ExtractedText, int, str]:
    if len(raw) > settings.DOCUMENT_MAX_BYTES:
        raise DocumentIngestionError("too_large")
    media_type = sniff_media_type(raw, declared_media_type)
    _check_authors(declared_authors)
    if media_type == "pdf":
        extracted, removed = await extract_pdf_sandboxed(
            raw, _test_child_env=test_child_env
        )
        return media_type, extracted, removed, extractor_pin().version
    extracted, removed = await asyncio.to_thread(_extract_text, raw)
    return media_type, extracted, removed, f"text-utf8/normalizer-{NORMALIZER_VERSION}"


async def ingest(
    raw: bytes,
    declared_media_type: str,
    filename: str,
    declared_authors: list[str],
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    session: AsyncSession,
    _test_child_env: dict[str, str] | None = None,
) -> UploadedDocument:
    """Validate, extract, encrypt and store one upload for its owner."""
    cipher = document_cipher()
    started = datetime.now(UTC)
    try:
        media_type, extracted, removed, version = await _extract(
            raw, declared_media_type, declared_authors, _test_child_env
        )
    except DocumentIngestionError as error:
        logger.warning("document_rejected", code=error.code, size=len(raw))
        raise

    row = await UploadedDocumentRepository(session, cipher).create(
        organization_id=organization_id,
        user_id=user_id,
        filename=_clean_filename(filename),
        media_type=media_type,
        extracted=extracted,
        extractor_version=version,
        invisible_chars_removed=removed,
        declared_authors=declared_authors,
        expires_at=started + timedelta(days=settings.DOCUMENT_RETENTION_DAYS),
        page_count=len(extracted.pages) if media_type == "pdf" else None,
    )
    logger.info(
        "document_ingested",
        document_id=str(row.id),
        media_type=media_type,
        size=len(raw),
        chars=row.char_count,
        invisible_chars_removed=removed,
        duration_ms=int((datetime.now(UTC) - started).total_seconds() * 1000),
    )
    return row
