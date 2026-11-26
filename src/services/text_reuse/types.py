"""Types and limits for the verbatim text-reuse check."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from src.core.acquisition.sources import SourceLicense

TOKEN_POLICY_VERSION = "1"
MIN_MATCH_TOKENS = 12
SHINGLE_TOKENS = 8
MAX_POSITIONS_PER_SHINGLE = 64
MAX_TOTAL_CANDIDATE_CHARS = 10_000_000
# A 50k-token paper holds at most ~3.8k maximal runs even if every 13th token differs.
MAX_MATCHES_PER_CANDIDATE = 5_000
DECLARED_AUTHOR_NOTE = "author overlap (declared by the uploader, unverified)"

Origin = Literal["own_upload", "openalex", "arxiv"]
SkipReason = Literal[
    "too_long", "budget_exhausted", "deadline_reached", "too_many_matches"
]

_DOI = re.compile(r'10\.\d{4,9}/[^\s"<>\x00-\x1f\x7f]+', re.ASCII)
_ARXIV_NEW = r"\d{4}\.\d{4,5}(?:v\d+)?"
_ARXIV_OLD = r"[a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?"
_ARXIV = re.compile(rf"{_ARXIV_NEW}|{_ARXIV_OLD}", re.ASCII)
_OPENALEX = re.compile(r"W\d+", re.ASCII)
_MAX_TITLE = 300
_MAX_AUTHORS = 50
_MAX_AUTHOR_CHARS = 100


class Attribution(StrEnum):
    QUOTED_AND_CITED = "quoted_and_cited"
    QUOTED_UNCITED = "quoted_uncited"
    CITED_UNQUOTED = "cited_unquoted"
    UNATTRIBUTED = "unattributed"


def _valid_source_id(origin: str, source_id: str) -> bool:
    if origin == "openalex":
        return _OPENALEX.fullmatch(source_id) is not None
    if origin == "arxiv":
        return _ARXIV.fullmatch(source_id) is not None
    if origin == "own_upload":
        try:
            return str(uuid.UUID(source_id)) == source_id
        except ValueError:
            return False
    return False


def source_link(origin: str, source_id: str) -> str | None:
    """The only place a source URL is built; the id is re-validated first."""
    if not _valid_source_id(origin, source_id):
        raise ValueError(f"invalid {origin} source id")
    if origin == "openalex":
        return f"https://openalex.org/{source_id}"
    if origin == "arxiv":
        return f"https://arxiv.org/abs/{source_id}"
    return None


@dataclass(frozen=True)
class Candidate:
    source_id: str
    origin: Origin
    title: str
    authors: tuple[str, ...]
    year: int | None
    doi: str | None
    arxiv_id: str | None
    licence: SourceLicense
    text: str
    text_kind: Literal["full_text", "abstract"]

    def __post_init__(self) -> None:
        if not _valid_source_id(self.origin, self.source_id):
            raise ValueError(f"invalid {self.origin} source id")
        if self.doi is not None and _DOI.fullmatch(self.doi) is None:
            raise ValueError("invalid doi")
        if self.arxiv_id is not None and _ARXIV.fullmatch(self.arxiv_id) is None:
            raise ValueError("invalid arxiv id")
        if len(self.title) > _MAX_TITLE:
            raise ValueError("title too long")
        if len(self.authors) > _MAX_AUTHORS or any(
            len(a) > _MAX_AUTHOR_CHARS for a in self.authors
        ):
            raise ValueError("too many or too long author names")

    @property
    def link(self) -> str | None:
        return source_link(self.origin, self.source_id)


@dataclass(frozen=True)
class ReuseBudget:
    max_total_chars: int = MAX_TOTAL_CANDIDATE_CHARS
    max_candidate_chars: int = 2_000_000
    deadline_s: float = 120


@dataclass(frozen=True)
class ReuseMatch:
    candidate_source_id: str
    kind: Literal["verbatim", "normalized"]
    doc_start: int
    doc_end: int
    src_start: int
    src_end: int
    tokens: int
    attribution: Attribution
    section: Literal["body", "statement"]
    from_user_upload: bool
    declared_author_overlap: bool
    normalizations_applied: frozenset[str]


@dataclass(frozen=True)
class ExcludedRegion:
    kind: Literal["references"]
    start: int
    end: int
    tokens: int


@dataclass(frozen=True)
class VerbatimResult:
    matches: tuple[ReuseMatch, ...]
    excluded_regions: tuple[ExcludedRegion, ...]
    skipped: tuple[tuple[str, SkipReason], ...]
    skipped_common_shingles: int
    token_policy_version: str = TOKEN_POLICY_VERSION
    min_match_tokens: int = MIN_MATCH_TOKENS
