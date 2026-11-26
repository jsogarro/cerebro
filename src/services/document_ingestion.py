"""Turn an untrusted upload into an encrypted, owner-private document row.

Size check, magic-byte sniff, extraction (PDFs in a killable child with a
scrubbed environment), invisible-character stripping, encryption, storage.
It writes no usage-ledger row: the caller reserves quota before parsing.
"""

import asyncio
import json
import signal
import sys
import unicodedata
import uuid
from dataclasses import dataclass
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
    "extract_upload",
    "ingest",
    "require_document_key",
    "sniff_media_type",
    "store_document",
]

_CHILD_ENV: Final = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
_STDOUT_SLACK = 1 << 20
# One extraction per process: the API runs a single worker, so at most one
# parser child is alive at a time (sandbox plan SO1).
_EXTRACT_SLOTS = asyncio.Semaphore(1)
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


async def _feed(stdin: asyncio.StreamWriter, data: bytes) -> None:
    try:
        stdin.write(data)
        await stdin.drain()
        stdin.close()
    except (BrokenPipeError, ConnectionResetError):
        pass


async def _exchange(
    proc: asyncio.subprocess.Process, data: bytes, max_out: int
) -> bytes:
    stdout = cast(asyncio.StreamReader, proc.stdout)
    feeder = asyncio.create_task(_feed(cast(asyncio.StreamWriter, proc.stdin), data))
    out = bytearray()
    try:
        while chunk := await stdout.read(65536):
            out += chunk
            if len(out) > max_out:
                raise DocumentIngestionError("output_too_large")
    finally:
        feeder.cancel()
    return bytes(out)


def _raise_for_exit(returncode: int | None) -> None:
    if returncode == _child.EXIT_MEMORY:
        raise DocumentIngestionError("extraction_resource_limit")
    if returncode == -signal.SIGXCPU:
        raise DocumentIngestionError("extraction_cpu_limit")


async def _run_child(
    argv_tail: list[str], data: bytes, *, timeout: float, max_out: int
) -> bytes:
    """Run one child to completion; always kill and reap it.

    The deadline covers the wait for the extraction slot too, so a queue of
    slow documents is refused as ``extraction_busy`` instead of waiting
    unbounded.
    """
    proc: asyncio.subprocess.Process | None = None
    try:
        async with asyncio.timeout(timeout), _EXTRACT_SLOTS:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                *argv_tail,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=_CHILD_ENV,
            )
            try:
                out = await _exchange(proc, data, max_out)
                await proc.wait()
            finally:
                if proc.returncode is None:
                    proc.kill()
                await asyncio.shield(proc.wait())
    except TimeoutError:
        raise DocumentIngestionError(
            "extraction_busy" if proc is None else "extraction_timeout"
        ) from None
    _raise_for_exit(proc.returncode)
    return out


def _verify_reply(reply: Any) -> tuple[ExtractedText, int]:
    if not reply["ok"]:
        code = reply["error"]
        raise DocumentIngestionError(code if code in _CHILD_CODES else "malformed_pdf")
    text, removed = reply["text"], reply["invisible_chars_removed"]
    if (
        type(text) is not str
        or not 0 < len(text) <= settings.DOCUMENT_MAX_CHARS
        or type(removed) is not int
        or not 0 <= removed <= settings.DOCUMENT_MAX_BYTES
        or reply["text_sha256"] != snapshot_digest(text.encode("utf-8"))
    ):
        raise ValueError("child reply failed verification")
    if (
        len(reply["pages"]) > settings.DOCUMENT_MAX_PAGES
        or strip_invisible(text)[0] != text
    ):
        raise ValueError("child reply broke the extraction limits")
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


async def extract_pdf_sandboxed(data: bytes) -> tuple[ExtractedText, int]:
    """Extract a PDF in a child process; return the text and removed-char count."""
    raw_reply = await _run_child(
        [
            "-I",
            "-B",
            "-m",
            _child.__name__,
            str(settings.DOCUMENT_MAX_PAGES),
            str(settings.DOCUMENT_MAX_CHARS),
        ],
        data,
        timeout=settings.DOCUMENT_EXTRACT_TIMEOUT_S,
        max_out=settings.DOCUMENT_MAX_CHARS * 6 + _STDOUT_SLACK,
    )
    extracted, removed = await asyncio.to_thread(_parse_reply, raw_reply)
    if extracted.source_sha256 != snapshot_digest(data):
        raise DocumentIngestionError("malformed_pdf")
    return extracted, removed


def _extract_text(raw: bytes) -> tuple[ExtractedText, int]:
    decoded = raw.decode("utf-8")
    if len(decoded) > settings.DOCUMENT_MAX_CHARS:
        raise DocumentIngestionError("too_much_text")
    text, removed = strip_invisible(decoded)
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


def _clean_authors(authors: list[str]) -> list[str]:
    if len(authors) > _MAX_AUTHORS or not all(
        isinstance(a, str) and len(a) <= _MAX_AUTHOR_CHARS for a in authors
    ):
        raise DocumentIngestionError("invalid_authors")
    return [strip_invisible(a)[0] for a in authors]


async def _extract(
    raw: bytes,
    declared_media_type: str,
) -> tuple[str, ExtractedText, int, str]:
    if len(raw) > settings.DOCUMENT_MAX_BYTES:
        raise DocumentIngestionError("too_large")
    media_type = sniff_media_type(raw, declared_media_type)
    if media_type == "pdf":
        extracted, removed = await extract_pdf_sandboxed(raw)
        return media_type, extracted, removed, extractor_pin().version
    extracted, removed = await asyncio.to_thread(_extract_text, raw)
    return media_type, extracted, removed, f"text-utf8/normalizer-{NORMALIZER_VERSION}"


@dataclass(frozen=True)
class PreparedDocument:
    """A validated, extracted upload that has not touched the database."""

    media_type: str
    extracted: ExtractedText
    removed: int
    version: str
    filename: str
    authors: list[str]
    size: int


async def extract_upload(
    raw: bytes,
    declared_media_type: str,
    filename: str,
    declared_authors: list[str],
) -> PreparedDocument:
    """Validate and extract an upload; needs no database connection."""
    document_cipher()
    try:
        authors = _clean_authors(declared_authors)
        media_type, extracted, removed, version = await _extract(
            raw, declared_media_type
        )
    except DocumentIngestionError as error:
        logger.warning("document_rejected", code=error.code, size=len(raw))
        raise
    return PreparedDocument(
        media_type,
        extracted,
        removed,
        version,
        _clean_filename(filename),
        authors,
        len(raw),
    )


async def store_document(
    session: AsyncSession,
    prepared: PreparedDocument,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
) -> UploadedDocument:
    """Encrypt and persist a prepared upload for its owner."""
    row = await UploadedDocumentRepository(session, document_cipher()).create(
        organization_id=organization_id,
        user_id=user_id,
        filename=prepared.filename,
        media_type=prepared.media_type,
        extracted=prepared.extracted,
        extractor_version=prepared.version,
        invisible_chars_removed=prepared.removed,
        declared_authors=prepared.authors,
        expires_at=datetime.now(UTC) + timedelta(days=settings.DOCUMENT_RETENTION_DAYS),
        page_count=len(prepared.extracted.pages)
        if prepared.media_type == "pdf"
        else None,
    )
    logger.info(
        "document_ingested",
        document_id=str(row.id),
        media_type=prepared.media_type,
        size=prepared.size,
        chars=row.char_count,
        invisible_chars_removed=prepared.removed,
    )
    return row


async def ingest(
    raw: bytes,
    declared_media_type: str,
    filename: str,
    declared_authors: list[str],
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    session: AsyncSession,
) -> UploadedDocument:
    """Validate, extract, encrypt and store one upload for its owner."""
    prepared = await extract_upload(
        raw, declared_media_type, filename, declared_authors
    )
    return await store_document(
        session, prepared, organization_id=organization_id, user_id=user_id
    )
