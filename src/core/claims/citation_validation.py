"""Deterministic citation checks against a captured registry corpus.

Four dimensions, each rolled up over all citations of an artifact:
``reference_resolution``, ``metadata_agreement`` (advisory),
``retraction_status`` and ``version_of_record``.

Honesty rules the tests pin:

* Absence from the corpus means "we do not know" and abstains. Only an entry
  explicitly marked ``known_absent`` may fail resolution, so an incomplete
  corpus cannot manufacture failures.
* Rollup is fail-closed: any FAILED, else any ABSTAINED, else PASSED. A draft
  with no citations is never PASSED, and exceeding the fan-out cap is an ERROR,
  not a truncated report.
* Disagreement in metadata is advisory and must not block a gate on its own.

No network, no clock, no model call. The corpus is loaded from text whose
SHA-256 is verified at load.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from src.core.contracts.citation import CitationMetadata
from src.core.contracts.outcomes import EvaluationStatus as S

VALIDATOR_VERSION = "1"
MAX_CITATIONS = 200
# Online-first versus issue year routinely differ by one.
YEAR_TOLERANCE = 1

REFERENCE_RESOLUTION = "citation.reference_resolution"
METADATA_AGREEMENT = "citation.metadata_agreement"
RETRACTION_STATUS = "citation.retraction_status"
VERSION_OF_RECORD = "citation.version_of_record"
DIMENSIONS = (
    REFERENCE_RESOLUTION,
    METADATA_AGREEMENT,
    RETRACTION_STATUS,
    VERSION_OF_RECORD,
)

_DOI = re.compile(r"10\.\d{4,9}/[\x21-\x7e]+", re.ASCII)
_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)


class CitationValidationError(ValueError):
    pass


@dataclass(frozen=True)
class CorpusEntry:
    first_author: str | None = None
    year: int | None = None
    retracted: bool = False
    version_of_record_doi: str | None = None
    known_absent: bool = False


def normalize_doi(raw: str) -> str | None:
    """Lowercased bare DOI, or ``None`` when ``raw`` is not a DOI."""
    doi = _DOI_PREFIX.sub("", raw.strip()).lower()
    return doi if _DOI.fullmatch(doi) else None


@dataclass(frozen=True)
class FixtureCorpus:
    entries: dict[str, CorpusEntry]

    @classmethod
    def from_json(cls, text: str, expected_sha256: str) -> FixtureCorpus:
        if hashlib.sha256(text.encode()).hexdigest() != expected_sha256:
            raise CitationValidationError("citation corpus hash mismatch")
        entries: dict[str, CorpusEntry] = {}
        for raw in json.loads(text):
            doi = normalize_doi(raw["doi"])
            if doi is None or doi in entries:
                raise CitationValidationError(
                    f"bad or duplicate corpus DOI {raw['doi']!r}"
                )
            entry = CorpusEntry(
                first_author=raw.get("first_author"),
                year=raw.get("year"),
                retracted=raw.get("retracted", False),
                version_of_record_doi=raw.get("version_of_record_doi"),
                known_absent=raw.get("known_absent", False),
            )
            if entry.known_absent and entry != CorpusEntry(known_absent=True):
                raise CitationValidationError(
                    f"known_absent entry {doi} carries metadata"
                )
            entries[doi] = entry
        return cls(entries)


@dataclass(frozen=True)
class DimensionResult:
    """One dimension over all citations; the caller wraps it in an EvaluationResult."""

    dimension: str
    status: S
    error_code: str | None
    advisory: bool
    details: dict[str, object] = field(default_factory=dict)


def _rollup(outcomes: list[tuple[S, str | None]]) -> tuple[S, str | None]:
    for status in (S.FAILED, S.ABSTAINED):
        codes = sorted({c for s, c in outcomes if s is status and c})
        if codes:
            return status, codes[0]
        if any(s is status for s, _ in outcomes):
            return status, None
    return S.PASSED, None


def _check(
    dimension: str, meta: CitationMetadata, corpus: FixtureCorpus
) -> tuple[S, str | None, str | None]:
    """Outcome for one citation: (status, error_code, subject DOI)."""
    if meta.doi is None:
        return S.ABSTAINED, "citation.no_identifier", None
    doi = normalize_doi(meta.doi.value)
    if doi is None:
        # Malformed is a defect in the artifact; only resolution says so.
        if dimension == REFERENCE_RESOLUTION:
            return S.FAILED, "citation.malformed_identifier", None
        return S.ABSTAINED, "citation.malformed_identifier", None
    entry = corpus.entries.get(doi)
    if entry is None:
        return S.ABSTAINED, "citation.unreachable", doi
    if entry.known_absent:
        if dimension == REFERENCE_RESOLUTION:
            return S.FAILED, "citation.unresolvable", doi
        return S.ABSTAINED, "citation.unresolvable", doi
    if dimension == REFERENCE_RESOLUTION:
        return S.PASSED, None, doi
    if dimension == RETRACTION_STATUS and entry.retracted:
        return S.FAILED, "citation.retracted", doi
    if dimension == VERSION_OF_RECORD and entry.version_of_record_doi:
        return S.FAILED, "citation.superseded_by_version_of_record", doi
    if dimension == METADATA_AGREEMENT and _disagrees(meta, entry):
        return S.FAILED, "citation.metadata_disagreement", doi
    return S.PASSED, None, doi


def _disagrees(meta: CitationMetadata, entry: CorpusEntry) -> bool:
    if (
        meta.authors
        and entry.first_author
        and meta.authors[0].value.casefold() != entry.first_author.casefold()
    ):
        return True
    if meta.year is not None and entry.year is not None:
        try:
            return abs(int(meta.year.value) - entry.year) > YEAR_TOLERANCE
        except ValueError:
            return True
    return False


def validate_citations(
    citations: Sequence[CitationMetadata], corpus: FixtureCorpus
) -> tuple[DimensionResult, ...]:
    total = len(citations)
    if total > MAX_CITATIONS:
        return tuple(
            DimensionResult(
                d,
                S.ERROR,
                "citation.too_many_citations",
                d == METADATA_AGREEMENT,
                {"citations_total": total, "max_citations": MAX_CITATIONS},
            )
            for d in DIMENSIONS
        )
    results = []
    for dim in DIMENSIONS:
        advisory = dim == METADATA_AGREEMENT
        if total == 0:
            results.append(
                DimensionResult(
                    dim,
                    S.ABSTAINED,
                    "citation.no_citations",
                    advisory,
                    {"citations_total": 0},
                )
            )
            continue
        checks = [_check(dim, c, corpus) for c in citations]
        status, code = _rollup([(s, c) for s, c, _ in checks])
        details: dict[str, object] = {
            "citations_total": total,
            "failed": sum(s is S.FAILED for s, _, _ in checks),
            "abstained": sum(s is S.ABSTAINED for s, _, _ in checks),
        }
        if dim == VERSION_OF_RECORD:
            vors = sorted(
                {
                    corpus.entries[d].version_of_record_doi  # type: ignore[misc]
                    for s, _, d in checks
                    if s is S.FAILED and d
                }
            )
            if len(vors) == 1:
                details["version_of_record"] = vors[0]
            elif vors:
                details["versions_of_record"] = vors
        results.append(DimensionResult(dim, status, code, advisory, details))
    return tuple(results)
