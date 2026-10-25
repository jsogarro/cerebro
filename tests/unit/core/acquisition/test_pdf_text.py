"""The pinned PDF text extractor and its ``extracted_text`` artifact.

Spec: no node reads a PDF; every node reads an ``extracted_text`` artifact
produced once by a pinned extractor. These tests pin what makes that true:
identical bytes on repeat, page offsets that resolve back to the right page,
an extractor identity that cannot drift from the installed library, and typed
refusals instead of empty text.
"""

import importlib.metadata
import io
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fpdf import FPDF
from pypdf import PdfWriter

from src.core.acquisition.pdf_text import (
    NORMALIZER_VERSION,
    PINNED_PYPDF_VERSION,
    EncryptedPdfError,
    MalformedPdfError,
    NoExtractableTextError,
    NotAPdfError,
    _normalize,
    build_extracted_text_artifact,
    extract_pdf_text,
    extractor_pin,
)
from src.core.acquisition.snapshots import FilesystemSnapshotStore
from src.core.contracts import ArtifactStatus, TrustClassification
from src.core.contracts.pinning import PinnedComponentKind
from src.core.contracts.redaction import snapshot_digest


def _pdf(pages: list[str]) -> bytes:
    doc = FPDF()
    doc.set_creation_date(datetime(2026, 1, 1, tzinfo=UTC))
    for text in pages:
        doc.add_page()
        doc.set_font("Helvetica", size=12)
        if text:
            doc.cell(text=text)
    return bytes(doc.output())


@pytest.fixture(name="pdf_bytes")
def pdf_bytes_fixture() -> bytes:
    return _pdf(["alpha page one", "bravo page two", "charlie page three"])


def test_repeat_extraction_is_byte_identical(pdf_bytes: bytes) -> None:
    first = extract_pdf_text(pdf_bytes)
    second = extract_pdf_text(pdf_bytes)
    assert first.text.encode() == second.text.encode()
    assert first == second
    assert first.text_sha256 == snapshot_digest(first.text.encode("utf-8"))


def test_page_offsets_resolve_to_the_right_page(pdf_bytes: bytes) -> None:
    result = extract_pdf_text(pdf_bytes)
    assert [span.page for span in result.pages] == [1, 2, 3]
    expected = ["alpha page one", "bravo page two", "charlie page three"]
    for span, words in zip(result.pages, expected, strict=True):
        assert result.text[span.start : span.end].strip() == words
    assert result.page_of(result.text.index("bravo")) == 2
    with pytest.raises(IndexError):
        result.page_of(len(result.text) + 10)


def test_empty_page_is_explicit_not_dropped() -> None:
    result = extract_pdf_text(_pdf(["first", "", "third"]))
    assert [span.page for span in result.pages] == [1, 2, 3]
    middle = result.pages[1]
    assert middle.start == middle.end
    assert result.empty_pages == (2,)


def test_pdf_with_no_text_at_all_is_an_error() -> None:
    with pytest.raises(NoExtractableTextError):
        extract_pdf_text(_pdf(["", ""]))


def test_non_pdf_input_is_a_typed_error() -> None:
    with pytest.raises(NotAPdfError):
        extract_pdf_text(b"just some text, not a pdf")
    with pytest.raises(NotAPdfError):
        extract_pdf_text(b"")


def test_truncated_pdf_is_a_typed_error(pdf_bytes: bytes) -> None:
    with pytest.raises(MalformedPdfError):
        extract_pdf_text(pdf_bytes[: len(pdf_bytes) // 3])


def test_encrypted_pdf_is_a_typed_error(pdf_bytes: bytes) -> None:
    from pypdf import PdfReader

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes)))
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(EncryptedPdfError):
        extract_pdf_text(buffer.getvalue())


def test_normalizer_composes_and_unifies_newlines() -> None:
    # The base-14 fonts fpdf2 offers cannot emit a combining mark, so the
    # normalizer is exercised directly.
    assert _normalize("cafe\u0301\r\nx\ry") == "caf\u00e9\nx\ny"


def test_installed_pypdf_matches_the_pin() -> None:
    assert importlib.metadata.version("pypdf") == PINNED_PYPDF_VERSION


def test_pin_constant_matches_pyproject() -> None:
    pyproject = Path(__file__).parents[4] / "pyproject.toml"
    declared = re.findall(r'"pypdf==([^"]+)"', pyproject.read_text())
    assert declared == [PINNED_PYPDF_VERSION]


def test_extractor_pin_identity() -> None:
    pin = extractor_pin()
    assert pin.kind is PinnedComponentKind.TOOL
    assert pin.component_id == "text_extractor"
    assert PINNED_PYPDF_VERSION in pin.version
    assert NORMALIZER_VERSION in pin.version


def test_artifact_carries_identity_and_resolves_through_the_store(
    tmp_path: Path, pdf_bytes: bytes
) -> None:
    store = FilesystemSnapshotStore(root=tmp_path / "snaps")
    source_sha = snapshot_digest(pdf_bytes)
    result = extract_pdf_text(pdf_bytes)
    artifact = build_extracted_text_artifact(
        result,
        source_content_sha256=source_sha,
        store=store,
        artifact_id="art-1",
        run_id="run-1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert artifact.kind == "extracted_text"
    assert artifact.media_type == "text/plain"
    assert artifact.status is ArtifactStatus.FINAL
    assert artifact.trust is TrustClassification.DERIVED_UNTRUSTED
    assert artifact.content_sha256 == result.text_sha256
    assert artifact.content_sha256 != source_sha
    assert store.get(artifact.storage_uri) == result.text.encode("utf-8")
    meta = artifact.metadata
    assert meta["extractor_id"] == "pypdf"
    assert meta["extractor_version"] == PINNED_PYPDF_VERSION
    assert meta["source_content_sha256"] == source_sha
    assert meta["unidata_version"] == unicodedata.unidata_version
    assert meta["normalizer_version"] == NORMALIZER_VERSION
    assert [p["page"] for p in meta["pages"]] == [1, 2, 3]  # type: ignore[union-attr]
