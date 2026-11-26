"""Pinned PDF text extraction producing an ``extracted_text`` artifact.

No node reads a PDF. A PDF snapshot is converted once, by one pinned
extractor, into an immutable ``text/plain`` artifact with its own
``content_sha256``; every later reader consumes that artifact.

The text is only as reproducible as the tuple that produced it:
``(extractor_id, extractor_version, source_content_sha256,
unicodedata.unidata_version, normalizer_version)``. All five are recorded in
the artifact's metadata. ``PINNED_PYPDF_VERSION`` must equal both the exact pin
in ``pyproject.toml`` and the installed library; tests fail if either drifts,
because a silently different extractor would change the text (and every offset
into it) while the recorded identity still claimed the old one.

Failures are typed rather than empty. A PDF that yields no text at all (for
example a scanned image) raises :class:`NoExtractableTextError` instead of
producing a zero-length artifact that downstream checks would read as "the
source says nothing".
"""

import io
import unicodedata
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from pydantic import JsonValue
from pypdf import PdfReader
from pypdf import __version__ as _installed_pypdf_version
from pypdf.errors import PyPdfError

from src.core.acquisition.snapshots import SnapshotStore
from src.core.contracts import (
    Artifact,
    ArtifactStatus,
    ProducerKind,
    TrustClassification,
)
from src.core.contracts.base import ContentSha256
from src.core.contracts.pinning import PinnedComponentKind, PinnedComponentVersion
from src.core.contracts.redaction import snapshot_digest

__all__ = [
    "EXTRACTOR_ID",
    "NORMALIZER_VERSION",
    "PINNED_PYPDF_VERSION",
    "EncryptedPdfError",
    "ExtractedText",
    "MalformedPdfError",
    "NoExtractableTextError",
    "NotAPdfError",
    "PageSpan",
    "PdfExtractionError",
    "TooManyPagesError",
    "TooMuchTextError",
    "build_extracted_text_artifact",
    "extract_pdf_text",
    "extractor_pin",
]

PINNED_PYPDF_VERSION: Final = "7.1.1"
EXTRACTOR_ID: Final = "pypdf"
NORMALIZER_VERSION: Final = "1"
_COMPONENT_ID: Final = "text_extractor"
_PAGE_SEPARATOR: Final = "\n"


class PdfExtractionError(ValueError):
    """Base for every refusal to turn a PDF into text."""


class NotAPdfError(PdfExtractionError):
    """The bytes do not begin like a PDF."""


class EncryptedPdfError(PdfExtractionError):
    """The PDF is encrypted; no password is ever supplied."""


class MalformedPdfError(PdfExtractionError):
    """The PDF is structurally broken or a page could not be read."""


class NoExtractableTextError(PdfExtractionError):
    """No page produced any text (e.g. a scanned, image-only document)."""


class TooManyPagesError(PdfExtractionError):
    """The PDF has more pages than the caller allows."""


class TooMuchTextError(PdfExtractionError):
    """The extracted text passed the caller's character cap."""


@dataclass(frozen=True)
class PageSpan:
    """Half-open ``[start, end)`` code-point range of one page in the text."""

    page: int
    start: int
    end: int


@dataclass(frozen=True)
class ExtractedText:
    text: str
    pages: tuple[PageSpan, ...]
    text_sha256: ContentSha256
    source_sha256: ContentSha256

    @property
    def empty_pages(self) -> tuple[int, ...]:
        return tuple(span.page for span in self.pages if span.start == span.end)

    def page_of(self, offset: int) -> int:
        """Return the page whose span contains ``offset``.

        Separator characters between pages belong to no page.
        """

        if not 0 <= offset < len(self.text):
            raise IndexError(f"offset {offset} is outside the extracted text")
        index = bisect_right([span.start for span in self.pages], offset) - 1
        span = self.pages[index] if index >= 0 else None
        if span is None or offset >= span.end:
            raise IndexError(f"offset {offset} falls between pages")
        return span.page


def extractor_pin() -> PinnedComponentVersion:
    """The pin recorded on a run for this extractor."""

    return PinnedComponentVersion(
        kind=PinnedComponentKind.TOOL,
        component_id=_COMPONENT_ID,
        version=f"{EXTRACTOR_ID}-{PINNED_PYPDF_VERSION}/normalizer-{NORMALIZER_VERSION}",
    )


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))


def _page_texts(
    reader: PdfReader,
    max_pages: int | None,
    max_chars: int | None,
    clean_page: Callable[[str], str] | None,
) -> list[str]:
    if max_pages is not None and len(reader.pages) > max_pages:
        raise TooManyPagesError(f"PDF has more than {max_pages} pages")
    page_texts: list[str] = []
    total = 0
    for page in reader.pages:
        page_text = _normalize(page.extract_text() or "")
        if clean_page is not None:
            page_text = _normalize(clean_page(page_text))
        total += len(page_text) + bool(page_texts)
        if max_chars is not None and total > max_chars:
            raise TooMuchTextError(f"PDF text is longer than {max_chars} characters")
        page_texts.append(page_text)
    return page_texts


def extract_pdf_text(
    data: bytes,
    *,
    max_pages: int | None = None,
    max_chars: int | None = None,
    clean_page: Callable[[str], str] | None = None,
) -> ExtractedText:
    """Extract normalized text and a page offset map from PDF bytes.

    ``max_pages`` is checked before any page is read and ``max_chars`` as soon
    as the running total passes it. ``clean_page`` runs on each page before
    the spans and hash are computed, so a cleanup never shifts an offset.
    """

    if _installed_pypdf_version != PINNED_PYPDF_VERSION:
        raise RuntimeError(
            f"pypdf {_installed_pypdf_version} is installed but the extractor "
            f"is pinned to {PINNED_PYPDF_VERSION}"
        )
    if b"%PDF-" not in data[:1024]:
        raise NotAPdfError("input does not carry a %PDF- header")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise EncryptedPdfError("PDF is encrypted")
        page_texts = _page_texts(reader, max_pages, max_chars, clean_page)
    except PdfExtractionError:
        raise
    except (PyPdfError, ValueError, KeyError, TypeError, RecursionError) as error:
        raise MalformedPdfError(f"PDF could not be read: {error}") from error

    if not any(page_texts):
        raise NoExtractableTextError("no page of the PDF produced any text")

    spans: list[PageSpan] = []
    cursor = 0
    for number, page_text in enumerate(page_texts, start=1):
        spans.append(PageSpan(number, cursor, cursor + len(page_text)))
        cursor += len(page_text) + len(_PAGE_SEPARATOR)
    text = _PAGE_SEPARATOR.join(page_texts)
    return ExtractedText(
        text=text,
        pages=tuple(spans),
        text_sha256=snapshot_digest(text.encode("utf-8")),
        source_sha256=snapshot_digest(data),
    )


def build_extracted_text_artifact(
    extraction: ExtractedText,
    *,
    store: SnapshotStore,
    artifact_id: str,
    run_id: str,
    created_at: datetime,
    task_id: str | None = None,
    attempt_id: str | None = None,
) -> Artifact:
    """Store the text immutably and describe it as an ``extracted_text`` artifact."""

    storage_uri = store.put(extraction.text.encode("utf-8"))
    pages: list[JsonValue] = [
        {"page": span.page, "start": span.start, "end": span.end}
        for span in extraction.pages
    ]
    return Artifact(
        artifact_id=artifact_id,
        run_id=run_id,
        task_id=task_id,
        attempt_id=attempt_id,
        kind="extracted_text",
        media_type="text/plain",
        storage_uri=storage_uri,
        content_sha256=extraction.text_sha256,
        status=ArtifactStatus.FINAL,
        trust=TrustClassification.DERIVED_UNTRUSTED,
        producer=_COMPONENT_ID,
        producer_kind=ProducerKind.SYSTEM,
        created_at=created_at,
        metadata={
            "extractor_id": EXTRACTOR_ID,
            "extractor_version": PINNED_PYPDF_VERSION,
            "source_content_sha256": extraction.source_sha256,
            "unidata_version": unicodedata.unidata_version,
            "normalizer_version": NORMALIZER_VERSION,
            "pages": pages,
        },
    )
