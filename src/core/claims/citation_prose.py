"""Detect citation-shaped prose that the citation renderer did not emit.

Citations are meant to be rendered from run-owned evidence metadata, never
typed by a model. A model that has seen a well-known paper will still write
``(Vaswani et al., 2017)`` from parametric recall, and a prompt telling it not
to is a request, not an enforcement. This deterministic scan is the
enforcement: any author-year, bracketed-numeric, DOI, or arXiv reference
outside the spans the renderer produced is a finding, and a stage with
findings must not report a clean outcome.

False positives are preferred to false negatives: a flagged phrase costs a
review, a missed fabricated citation reaches a reader as evidence.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

DETECTOR_VERSION = "1"


class CitationProseKind(StrEnum):
    AUTHOR_YEAR = "author_year"
    NUMERIC = "numeric"
    DOI = "doi"
    ARXIV = "arxiv"


@dataclass(frozen=True)
class CitationProseFinding:
    kind: CitationProseKind
    start: int
    end: int
    text: str


_SURNAME = r"[A-Z][\w'\-]*"
_AUTHORS = rf"{_SURNAME}(?: et al\.?| (?:and|&) {_SURNAME})?"
_YEAR = r"(?:1[5-9]|20)\d{2}[a-z]?"
# Capitalized function words that precede a parenthesized year in ordinary
# prose ("In (2019) terms") and are never author surnames.
_NOT_SURNAMES = frozenset(
    {"In", "The", "See", "As", "At", "By", "For", "From", "Of", "On", "To"}
    | {"Since", "Until", "During", "Before", "After", "And", "Or", "FY"}
)

_PATTERNS: tuple[tuple[CitationProseKind, re.Pattern[str]], ...] = (
    (CitationProseKind.AUTHOR_YEAR, re.compile(rf"\({_AUTHORS},? {_YEAR}\)")),
    (
        CitationProseKind.AUTHOR_YEAR,
        re.compile(
            rf"\b(?P<surname>{_SURNAME})(?: et al\.?| (?:and|&) {_SURNAME})? \({_YEAR}\)"
        ),
    ),
    (
        CitationProseKind.NUMERIC,
        re.compile(r"(?<![\w\]])\[\d{1,3}(?:\s*[,\u2013-]\s*\d{1,3})*\]"),
    ),
    (
        CitationProseKind.DOI,
        re.compile(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?\b10\.\d{4,9}/[^\s\"<>]+"),
    ),
    (CitationProseKind.ARXIV, re.compile(r"\b(?i:arxiv):\s?\d{4}\.\d{4,5}(?:v\d+)?")),
)
_TRAILING_PUNCTUATION = ".,;:)]"


def find_unrendered_citation_prose(
    text: str, rendered_spans: Iterable[tuple[int, int]] = ()
) -> tuple[CitationProseFinding, ...]:
    """Return citation-shaped spans of ``text`` outside ``rendered_spans``.

    ``rendered_spans`` are the half-open offsets the renderer wrote. Findings
    are non-overlapping and ordered by position.
    """
    candidates = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if match.groupdict().get("surname") in _NOT_SURNAMES:
                continue
            start, end = match.span()
            if kind is CitationProseKind.DOI:
                end = start + len(match.group().rstrip(_TRAILING_PUNCTUATION))
            candidates.append((start, -end, kind))

    excluded = list(rendered_spans)
    findings: list[CitationProseFinding] = []
    for start, negative_end, kind in sorted(candidates):
        end = -negative_end
        if any(start < e and s < end for s, e in excluded):
            continue
        excluded.append((start, end))
        findings.append(CitationProseFinding(kind, start, end, text[start:end]))
    return tuple(sorted(findings, key=lambda finding: finding.start))
