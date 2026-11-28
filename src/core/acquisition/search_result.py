"""The typed shape of one academic search hit and of what recording it did.

``RetrievedSource`` is the single reading of a provider record: which source it
names, what text it contributes, and what bibliographic identity it carries.
Nothing is synthesized; a field the provider did not supply stays absent.
``SourceRecordingOutcome`` is what the recorder reports per source, so a caller
can tell a recorded source from a skipped or failed one and, on replay, still
learns the stable evidence id.
"""

import hashlib
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, JsonValue

from src.core.acquisition.resolution import locate
from src.core.budget import estimate_prompt_tokens
from src.security.content_sanitizer import escape_for_delimited_prompt

__all__ = [
    "DEFAULT_MAX_PROMPT_SOURCES",
    "MAX_SOURCE_URI_LENGTH",
    "MIN_SOURCE_SHARE_TOKENS",
    "BoundedSelectionResult",
    "PromptSourceEntry",
    "RetrievedSource",
    "SelectionDiagnostics",
    "SelectionReason",
    "SourceDisposition",
    "SourceRecordingOutcome",
    "canonical_doi",
    "canonical_provider_id",
    "canonical_url",
    "select_bounded_sources",
    "source_ref",
    "source_text",
    "source_uri",
]

MAX_SOURCE_URI_LENGTH: Final[int] = 2048
DEFAULT_MAX_PROMPT_SOURCES: Final[int] = 10
MIN_SOURCE_SHARE_TOKENS: Final[int] = 80
_CHARS_PER_TOKEN: Final[int] = 3
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
_CANONICAL_DOI = re.compile(r"^10\.\d+/\S+$")
_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)\s*", re.IGNORECASE)
_ARXIV_VERSION = re.compile(r"v\d+$", re.IGNORECASE)
_YEAR = re.compile(r"^\d{4}$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "this",
        "to",
        "we",
        "with",
    }
)


def source_text(source: Mapping[str, Any]) -> str:
    """The text a search record contributes: its title and abstract."""

    abstract = str(source.get("abstract") or "").strip()
    if not abstract:
        return ""
    title = str(source.get("title") or "").strip()
    return f"{title}\n\n{abstract}" if title else abstract


def source_uri(source: Mapping[str, Any]) -> str | None:
    """Name the record's source from an http(s) URL or a DOI, else ``None``.

    Provider-supplied strings are untrusted: anything else, or anything over
    the length cap, is refused rather than normalized.
    """

    url = str(source.get("url") or "").strip()
    if url:
        parts = urlsplit(url)
        valid = (
            parts.scheme in ("http", "https")
            and bool(parts.netloc)
            and "@" not in parts.netloc  # userinfo can carry credentials
        )
        return url if valid and len(url) <= MAX_SOURCE_URI_LENGTH else None
    doi = str(source.get("doi") or "").strip()
    if _DOI.match(doi) and len(doi) <= MAX_SOURCE_URI_LENGTH - 16:
        return f"https://doi.org/{doi}"
    return None


def _year(value: Any) -> int | None:
    text = str(value or "").strip()
    return int(text) if _YEAR.match(text) else None


def _text(value: Any) -> str | None:
    return str(value).strip() or None if value is not None else None


class RetrievedSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str | None = None
    provider_id: str | None = None
    title: str = ""
    abstract: str = ""
    authors: tuple[str, ...] = ()
    year: int | None = None
    venue: str | None = None
    url: str | None = None
    doi: str | None = None

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "RetrievedSource":
        authors = record.get("authors")
        return cls(
            provider=_text(record.get("source")),
            provider_id=_text(record.get("id")),
            title=str(record.get("title") or "").strip(),
            abstract=str(record.get("abstract") or "").strip(),
            authors=tuple(
                str(a).strip()
                for a in (authors if isinstance(authors, Sequence) else ())
                if not isinstance(authors, str) and str(a).strip()
            ),
            year=_year(record.get("year")),
            venue=_text(record.get("venue") or record.get("journal")),
            url=_text(record.get("url")),
            doi=_text(record.get("doi")),
        )

    def text(self) -> str:
        return source_text(self.model_dump())

    def uri(self) -> str | None:
        return source_uri(self.model_dump())

    def bibliography(self) -> dict[str, JsonValue]:
        """Identity and citation fields kept with the snapshot, absent ones omitted."""

        fields: dict[str, JsonValue] = {
            "provider": self.provider,
            "provider_id": self.provider_id,
            "title": self.title or None,
            "authors": list(self.authors) or None,
            "year": self.year,
            "venue": self.venue,
            "url": self.url,
            "doi": self.doi,
        }
        return {k: v for k, v in fields.items() if v is not None}


class SourceDisposition(StrEnum):
    RECORDED = "recorded"
    ALREADY_RECORDED = "already_recorded"
    SKIPPED = "skipped"
    FAILED = "failed"


class SourceRecordingOutcome(BaseModel):
    """What happened to the source at ``index`` of the search result."""

    model_config = ConfigDict(frozen=True)

    index: int
    disposition: SourceDisposition
    source_uri: str | None = None
    provider: str | None = None
    provider_id: str | None = None
    doi: str | None = None
    year: int | None = None
    evidence_id: str | None = None
    reason: str | None = None


class SelectionReason(StrEnum):
    """Low-cardinality reason codes for source omission, dedupe, or truncation."""

    NOT_RECORDED = "not_recorded"
    MISSING_IDENTITY = "missing_identity"
    OUT_OF_YEAR_RANGE = "out_of_year_range"
    UNKNOWN_YEAR = "unknown_year"
    DUPLICATE_DOI = "duplicate_doi"
    DUPLICATE_PROVIDER_ID = "duplicate_provider_id"
    DUPLICATE_URL = "duplicate_url"
    SOURCE_CAP = "source_cap"
    CONTEXT_BUDGET = "context_budget"
    EXCERPT_TRUNCATED = "excerpt_truncated"


def canonical_doi(value: str | None) -> str | None:
    """Normalize a DOI string or doi.org URL to lowercase ``10.x/...`` form."""
    if not value:
        return None
    cleaned = _DOI_PREFIX.sub("", str(value).strip()).strip().rstrip("/").lower()
    if (
        not cleaned
        or len(cleaned) > MAX_SOURCE_URI_LENGTH - 16
        or not _CANONICAL_DOI.match(cleaned)
    ):
        return None
    return cleaned


def canonical_url(value: str | None) -> str | None:
    """Normalize an http(s) URL for deduplication without trusting credentials."""
    if not value:
        return None
    raw = str(value).strip()
    if not raw or len(raw) > MAX_SOURCE_URI_LENGTH:
        return None
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc or "@" in parts.netloc:
        return None
    host = parts.netloc.lower()
    if host.endswith(":80") and parts.scheme == "http":
        host = host[:-3]
    elif host.endswith(":443") and parts.scheme == "https":
        host = host[:-4]
    path = parts.path.rstrip("/")
    return urlunsplit(("https", host, path, parts.query, ""))


def canonical_provider_id(source: Mapping[str, Any]) -> tuple[str, str] | None:
    """Canonical ``(provider, provider_id)`` identity when both are present."""
    provider = _text(source.get("source"))
    provider_id = _text(source.get("id"))
    if not provider or not provider_id:
        return None
    norm_provider = provider.lower()
    norm_id = (
        _ARXIV_VERSION.sub("", provider_id) if norm_provider == "arxiv" else provider_id
    )
    return (norm_provider, norm_id.lower())


def _source_doi(source: Mapping[str, Any]) -> str | None:
    doi = canonical_doi(_text(source.get("doi")))
    if doi:
        return doi
    url = _text(source.get("url"))
    if url and _DOI_PREFIX.match(url):
        return canonical_doi(url)
    return None


def source_ref(source: Mapping[str, Any]) -> str | None:
    """A stable citation identity for a source without a recorded evidence id."""
    doi = _source_doi(source)
    if doi:
        return f"doi:{doi}"
    uri = source_uri(source)
    if uri:
        return f"url-sha256:{hashlib.sha256(uri.encode()).hexdigest()[:16]}"
    prov = canonical_provider_id(source)
    if prov:
        return f"{prov[0]}:{prov[1]}"
    return None


@dataclass(frozen=True, slots=True)
class PromptSourceEntry:
    """One bounded source block selected for the model prompt."""

    index: int
    ref: str
    evidence_id: str | None
    locator: str | None
    excerpt: str
    excerpt_sha256: str | None
    original_bytes: int
    excerpt_bytes: int
    truncated: bool
    query_score: float
    relevance_rank: int
    duplicate_count: int
    rendered_block: str
    tokens_used: int


@dataclass(frozen=True, slots=True)
class SelectionDiagnostics:
    """Body-free diagnostics describing source survival, truncation, and budget."""

    retrieved_count: int
    eligible_count: int
    deduped_count: int
    selected_count: int
    truncated_count: int
    omitted_count: int
    reason_counts: dict[str, int]
    context_window_tokens: int
    output_reserve_tokens: int
    instruction_tokens: int
    evidence_budget_tokens: int
    evidence_tokens_used: int
    prompt_tokens_estimated: int
    sources: tuple[dict[str, Any], ...]
    omitted_sources: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "retrieved_count": self.retrieved_count,
            "eligible_count": self.eligible_count,
            "deduped_count": self.deduped_count,
            "selected_count": self.selected_count,
            "truncated_count": self.truncated_count,
            "omitted_count": self.omitted_count,
            "reason_counts": dict(self.reason_counts),
            "context_window_tokens": self.context_window_tokens,
            "output_reserve_tokens": self.output_reserve_tokens,
            "instruction_tokens": self.instruction_tokens,
            "evidence_budget_tokens": self.evidence_budget_tokens,
            "evidence_tokens_used": self.evidence_tokens_used,
            "prompt_tokens_estimated": self.prompt_tokens_estimated,
            "sources": [dict(s) for s in self.sources],
            "omitted_sources": [dict(s) for s in self.omitted_sources],
        }


@dataclass(frozen=True, slots=True)
class BoundedSelectionResult:
    """Output of deduplication, relevance ranking, and prompt-budgeted selection."""

    eligible_sources: list[dict[str, Any]]
    prompt_entries: list[PromptSourceEntry]
    rendered_sources: str
    diagnostics: SelectionDiagnostics


def _provenance_entry(
    source: Mapping[str, Any],
    *,
    ref: str,
    evidence_id: str | None,
    duplicate_reason: SelectionReason | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "provider": _text(source.get("source")),
        "provider_id": _text(source.get("id")),
        "doi": _source_doi(source),
        "url": source_uri(source),
        "ref": ref,
        "evidence_id": evidence_id,
        "duplicate_reason": duplicate_reason.value if duplicate_reason else None,
    }
    return {k: v for k, v in entry.items() if v is not None}


def _tokenize(text: str) -> list[str]:
    raw = _TOKEN_RE.findall(text.lower())
    filtered = [tok for tok in raw if len(tok) >= 2 and tok not in _STOPWORDS]
    return filtered or raw


def _query_identifiers(query: str) -> set[str]:
    identifiers: set[str] = set()
    for raw_token in query.split():
        cleaned = raw_token.strip(".,;:()[]{}<>\"'")
        doi = canonical_doi(cleaned)
        if doi:
            identifiers.add(doi)
            continue
        url = canonical_url(cleaned)
        if url:
            identifiers.add(url)
            continue
        lowered = cleaned.lower()
        if any(ch.isdigit() for ch in lowered) and len(lowered) >= 4:
            identifiers.add(_ARXIV_VERSION.sub("", lowered))
            identifiers.add(lowered)
    return identifiers


def _source_matches_query_id(source: Mapping[str, Any], query_ids: set[str]) -> bool:
    if not query_ids:
        return False
    records: list[Mapping[str, Any]] = [source, *(source.get("provenance") or ())]
    for rec in records:
        doi = _source_doi(rec)
        if doi and doi in query_ids:
            return True
        url = canonical_url(_text(rec.get("url")))
        if url and url in query_ids:
            return True
        pid = _text(rec.get("id") or rec.get("provider_id"))
        if pid:
            norm = pid.lower()
            if norm in query_ids or _ARXIV_VERSION.sub("", norm) in query_ids:
                return True
    return False


def _idf_weights(
    query_terms: list[str], sources: Sequence[Mapping[str, Any]]
) -> dict[str, float]:
    unique_terms = list(dict.fromkeys(query_terms))
    if not unique_terms:
        return {}
    n_docs = len(sources)
    doc_freq: Counter[str] = Counter()
    for source in sources:
        doc_tokens = set(
            _tokenize(f"{source.get('title') or ''} {source.get('abstract') or ''}")
        )
        for term in unique_terms:
            if term in doc_tokens:
                doc_freq[term] += 1
    return {
        term: math.log(1.0 + (n_docs + 1.0) / (doc_freq[term] + 1.0))
        for term in unique_terms
    }


def _score_source(
    source: Mapping[str, Any],
    *,
    query_lower: str,
    query_terms: list[str],
    query_ids: set[str],
    idf: Mapping[str, float],
) -> float:
    if _source_matches_query_id(source, query_ids):
        return 1.0
    if not query_terms or not idf:
        return 0.0
    title = str(source.get("title") or "")
    abstract = str(source.get("abstract") or "")
    title_counts = Counter(_tokenize(title))
    abstract_counts = Counter(_tokenize(abstract))
    total_weight = sum(idf.values())
    if total_weight <= 0:
        return 0.0
    matched = 0.0
    for term, weight in idf.items():
        weighted_tf = 2.0 * title_counts.get(term, 0) + float(
            abstract_counts.get(term, 0)
        )
        if weighted_tf > 0:
            matched += weight * (weighted_tf / (weighted_tf + 1.0))
    base = matched / total_weight
    phrase_bonus = 0.0
    if len(query_terms) >= 2:
        phrase = " ".join(query_terms)
        combined = " ".join(_TOKEN_RE.findall(f"{title} {abstract}".lower()))
        if phrase in combined or query_lower in f"{title} {abstract}".lower():
            phrase_bonus = 0.15
    return round(min(base + phrase_bonus, 0.999), 6)


def _sentence_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    start = 0
    for match in _SENTENCE_SPLIT_RE.finditer(text):
        end = match.start()
        if end > start:
            spans.append((start, end, text[start:end]))
        start = match.end()
    if start < len(text):
        spans.append((start, len(text), text[start:]))
    return spans or [(0, len(text), text)]


def _score_span(
    span_text: str,
    *,
    query_terms: list[str],
    query_ids: set[str],
    idf: Mapping[str, float],
) -> float:
    lowered = span_text.lower()
    if any(qid in lowered for qid in query_ids):
        return 100.0
    counts = Counter(_tokenize(span_text))
    score = 0.0
    for term, weight in idf.items():
        tf = counts.get(term, 0)
        if tf:
            score += weight * (tf / (tf + 1.0))
    return score


def _word_aligned_window(
    text: str,
    *,
    max_chars: int,
    query_terms: list[str],
    query_ids: set[str],
) -> tuple[int, int]:
    if len(text) <= max_chars:
        return (0, len(text))
    lowered = text.lower()
    anchor = -1
    for target in (*query_ids, *query_terms):
        pos = lowered.find(target)
        if pos != -1 and (anchor == -1 or pos < anchor):
            anchor = pos
    if anchor == -1:
        anchor = 0
    start = max(0, anchor - max_chars // 3)
    end = min(len(text), start + max_chars)
    start = max(0, end - max_chars)
    if start > 0 and not text[start - 1].isspace():
        next_space = text.find(" ", start, end)
        if next_space != -1 and next_space + 1 < end:
            start = next_space + 1
    if end < len(text) and not text[end].isspace():
        prev_space = text.rfind(" ", start, end)
        if prev_space > start:
            end = prev_space
    if end <= start:
        end = min(len(text), start + max(1, max_chars))
    return (start, end)


def _extract_bounded_excerpt(
    abstract: str,
    *,
    max_chars: int,
    query_terms: list[str],
    query_ids: set[str],
    idf: Mapping[str, float],
) -> tuple[int, int]:
    """Select an intact, query-centered slice ``(start, end)`` within ``max_chars``."""
    if len(abstract) <= max_chars:
        return (0, len(abstract))
    spans = _sentence_spans(abstract)
    scores = [
        _score_span(s_text, query_terms=query_terms, query_ids=query_ids, idf=idf)
        for _, _, s_text in spans
    ]
    best_idx = max(range(len(spans)), key=lambda i: (scores[i], -i))
    best_start, _best_end, best_text = spans[best_idx]
    if len(best_text) > max_chars:
        rel_start, rel_end = _word_aligned_window(
            best_text,
            max_chars=max_chars,
            query_terms=query_terms,
            query_ids=query_ids,
        )
        return (best_start + rel_start, best_start + rel_end)

    left = right = best_idx
    while True:
        candidates: list[tuple[float, int, int, int]] = []
        if left > 0:
            new_start = spans[left - 1][0]
            if spans[right][1] - new_start <= max_chars:
                candidates.append((scores[left - 1], 0, left - 1, right))
        if right + 1 < len(spans):
            new_end = spans[right + 1][1]
            if new_end - spans[left][0] <= max_chars:
                # Prefer expanding right on tie so trailing figures/results stay intact.
                candidates.append((scores[right + 1], 1, left, right + 1))
        if not candidates:
            break
        _, _, left, right = max(candidates, key=lambda item: (item[0], item[1]))
    return (spans[left][0], spans[right][1])


def _escape_single_line(value: Any) -> str:
    # Collapsing whitespace stops a field forging a line of its own.
    return escape_for_delimited_prompt(" ".join(str(value).split()))


def _render_source_block(
    *,
    index: int,
    source: Mapping[str, Any],
    ref: str,
    evidence_id: str | None,
    locator: str | None,
    digest: str | None,
    excerpt: str,
) -> str:
    lines = [f'<SOURCE index="{index}">']
    lines.append(f"Title: {_escape_single_line(source.get('title', 'Unknown Title'))}")
    authors = source.get("authors")
    if authors:
        lines.append(f"Authors: {_escape_single_line(', '.join(authors[:3]))}")
    if source.get("year"):
        lines.append(f"Year: {_escape_single_line(source['year'])}")
    lines.append(f"Ref: {_escape_single_line(ref)}")
    if evidence_id and locator:
        lines.append(
            f"Evidence: {_escape_single_line(evidence_id)} @ {_escape_single_line(locator)}"
        )
    elif locator:
        lines.append(f"Locator: {_escape_single_line(locator)}")
    if digest:
        lines.append(f"Digest: sha256:{digest}")
    if excerpt:
        lines.append(f"Abstract: {_escape_single_line(excerpt)}")
    lines.append("</SOURCE>")
    return "\n".join(lines)


def _deduplicate_candidates(
    candidates: list[dict[str, Any]],
    *,
    reason_counts: Counter[str],
    omitted_sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    doi_index: dict[str, int] = {}
    prov_index: dict[tuple[str, str], int] = {}
    url_index: dict[str, int] = {}

    for candidate in candidates:
        doi = _source_doi(candidate)
        prov = canonical_provider_id(candidate)
        url = canonical_url(_text(candidate.get("url")))

        match_idx: int | None = None
        match_reason: SelectionReason | None = None
        if doi and doi in doi_index:
            match_idx = doi_index[doi]
            match_reason = SelectionReason.DUPLICATE_DOI
        elif prov and prov in prov_index:
            existing_doi = _source_doi(groups[prov_index[prov]])
            if not (doi and existing_doi and doi != existing_doi):
                match_idx = prov_index[prov]
                match_reason = SelectionReason.DUPLICATE_PROVIDER_ID
        if match_idx is None and url and url in url_index:
            existing_doi = _source_doi(groups[url_index[url]])
            if not (doi and existing_doi and doi != existing_doi):
                match_idx = url_index[url]
                match_reason = SelectionReason.DUPLICATE_URL

        if match_idx is None:
            idx = len(groups)
            entry = {
                **candidate,
                "provenance": [
                    _provenance_entry(
                        candidate,
                        ref=candidate["ref"],
                        evidence_id=candidate.get("evidence_id"),
                    )
                ],
                "_duplicate_count": 0,
            }
            groups.append(entry)
            if doi:
                doi_index[doi] = idx
            if prov:
                prov_index[prov] = idx
            if url:
                url_index[url] = idx
            continue

        assert match_reason is not None
        reason_counts[match_reason.value] += 1
        omitted_sources.append(
            {
                "ref": candidate["ref"],
                "provider": _text(candidate.get("source")),
                "reason": match_reason.value,
            }
        )
        existing = groups[match_idx]
        existing_prov = list(existing["provenance"])
        existing_prov.append(
            _provenance_entry(
                candidate,
                ref=candidate["ref"],
                evidence_id=candidate.get("evidence_id"),
                duplicate_reason=match_reason,
            )
        )
        new_dup_count = existing["_duplicate_count"] + 1
        # Keep the representative's text and evidence_id paired; prefer recorded
        # evidence, then the more complete abstract so late answer spans survive.
        existing_rank = (
            1 if existing.get("evidence_id") else 0,
            len(str(existing.get("abstract") or "").encode("utf-8")),
        )
        candidate_rank = (
            1 if candidate.get("evidence_id") else 0,
            len(str(candidate.get("abstract") or "").encode("utf-8")),
        )
        winner = candidate if candidate_rank > existing_rank else existing
        merged = {
            **winner,
            "provenance": existing_prov,
            "_duplicate_count": new_dup_count,
        }
        # Preserve canonical DOI if the non-winning duplicate carried it.
        if not _source_doi(merged) and (doi or _source_doi(existing)):
            merged["doi"] = doi or _source_doi(existing)
        groups[match_idx] = merged
        if doi and doi not in doi_index:
            doi_index[doi] = match_idx
        if prov and prov not in prov_index:
            prov_index[prov] = match_idx
        if url and url not in url_index:
            url_index[url] = match_idx

    return groups


def _build_prompt_entry(
    *,
    index: int,
    source: Mapping[str, Any],
    query_terms: list[str],
    query_ids: set[str],
    idf: Mapping[str, float],
    max_tokens: int,
) -> PromptSourceEntry | None:
    ref = str(source["ref"])
    evidence_id = source.get("evidence_id")
    title = str(source.get("title") or "").strip()
    abstract = str(source.get("abstract") or "").strip()
    full_text = source_text(source)
    full_bytes = full_text.encode("utf-8") if full_text else b""

    full_locator = locate("bytes", 0, len(full_bytes)) if full_bytes else None
    full_digest = hashlib.sha256(full_bytes).hexdigest() if full_bytes else None
    full_block = _render_source_block(
        index=index,
        source=source,
        ref=ref,
        evidence_id=evidence_id,
        locator=full_locator,
        digest=full_digest,
        excerpt=abstract,
    )
    full_tokens = estimate_prompt_tokens(full_block)
    if full_tokens <= max_tokens:
        return PromptSourceEntry(
            index=index,
            ref=ref,
            evidence_id=evidence_id,
            locator=full_locator,
            excerpt=abstract,
            excerpt_sha256=full_digest,
            original_bytes=len(full_bytes),
            excerpt_bytes=len(full_bytes),
            truncated=False,
            query_score=float(source.get("selection_score", 0.0)),
            relevance_rank=int(source.get("selection_rank", index)),
            duplicate_count=int(source.get("_duplicate_count", 0)),
            rendered_block=full_block,
            tokens_used=full_tokens,
        )

    if not abstract:
        return None

    # Measure fixed header/metadata overhead using a representative locator/digest.
    placeholder_locator = locate("bytes", 0, len(full_bytes)) if full_bytes else None
    skeleton = _render_source_block(
        index=index,
        source=source,
        ref=ref,
        evidence_id=evidence_id,
        locator=placeholder_locator,
        digest="0" * 64,
        excerpt="",
    )
    overhead_tokens = estimate_prompt_tokens(skeleton)
    available_for_excerpt = max_tokens - overhead_tokens - 2
    if available_for_excerpt <= 0:
        return None

    prefix_bytes = len(f"{title}\n\n".encode()) if title else 0
    max_chars = max(16, available_for_excerpt * _CHARS_PER_TOKEN)

    for _ in range(8):
        start_char, end_char = _extract_bounded_excerpt(
            abstract,
            max_chars=max_chars,
            query_terms=query_terms,
            query_ids=query_ids,
            idf=idf,
        )
        excerpt = abstract[start_char:end_char]
        start_byte = prefix_bytes + len(abstract[:start_char].encode("utf-8"))
        end_byte = prefix_bytes + len(abstract[:end_char].encode("utf-8"))
        if end_byte <= start_byte:
            return None
        locator = locate("bytes", start_byte, end_byte)
        excerpt_bytes = full_bytes[start_byte:end_byte]
        digest = hashlib.sha256(excerpt_bytes).hexdigest()
        block = _render_source_block(
            index=index,
            source=source,
            ref=ref,
            evidence_id=evidence_id,
            locator=locator,
            digest=digest,
            excerpt=excerpt,
        )
        block_tokens = estimate_prompt_tokens(block)
        if block_tokens <= max_tokens:
            return PromptSourceEntry(
                index=index,
                ref=ref,
                evidence_id=evidence_id,
                locator=locator,
                excerpt=excerpt,
                excerpt_sha256=digest,
                original_bytes=len(full_bytes),
                excerpt_bytes=len(excerpt_bytes),
                truncated=True,
                query_score=float(source.get("selection_score", 0.0)),
                relevance_rank=int(source.get("selection_rank", index)),
                duplicate_count=int(source.get("_duplicate_count", 0)),
                rendered_block=block,
                tokens_used=block_tokens,
            )
        # Escaped characters can push token estimates slightly over; tighten and retry.
        overshoot_chars = (block_tokens - max_tokens + 1) * _CHARS_PER_TOKEN
        max_chars = max(0, len(excerpt) - overshoot_chars)
        if max_chars < 16:
            return None
    return None


def select_bounded_sources(
    sources: Sequence[Mapping[str, Any]],
    *,
    query: str,
    evidence_budget_tokens: int,
    source_evidence: Mapping[str, Any] | None = None,
    max_prompt_sources: int = DEFAULT_MAX_PROMPT_SOURCES,
    year_start: int | None = None,
    year_end: int | None = None,
    context_window_tokens: int = 0,
    output_reserve_tokens: int = 0,
    instruction_tokens: int = 0,
) -> BoundedSelectionResult:
    """Filter, deduplicate, rank, and budget sources for grounded analysis."""
    recording = source_evidence or {}
    recorded_mode = "recorded" in recording
    evidence_by_index = {
        outcome["index"]: outcome["evidence_id"]
        for outcome in recording.get("sources", [])
        if outcome.get("evidence_id")
    }
    year_bounded = year_start is not None or year_end is not None

    reason_counts: Counter[str] = Counter()
    omitted_sources: list[dict[str, Any]] = []
    eligible_raw: list[dict[str, Any]] = []

    for index, raw in enumerate(sources):
        source = dict(raw)
        if recorded_mode:
            if index not in evidence_by_index:
                reason_counts[SelectionReason.NOT_RECORDED.value] += 1
                omitted_sources.append(
                    {
                        "ref": source_ref(source),
                        "provider": _text(source.get("source")),
                        "reason": SelectionReason.NOT_RECORDED.value,
                    }
                )
                continue
            ev_id = str(evidence_by_index[index])
            full_len = len(source_text(source).encode("utf-8"))
            source["ref"] = ev_id
            source["evidence_id"] = ev_id
            if full_len > 0:
                source["locator"] = locate("bytes", 0, full_len)
        else:
            ref = source_ref(source)
            if not ref:
                reason_counts[SelectionReason.MISSING_IDENTITY.value] += 1
                omitted_sources.append(
                    {
                        "ref": None,
                        "provider": _text(source.get("source")),
                        "reason": SelectionReason.MISSING_IDENTITY.value,
                    }
                )
                continue
            source["ref"] = ref

        if year_bounded:
            parsed_year = _year(source.get("year"))
            if parsed_year is None:
                reason_counts[SelectionReason.UNKNOWN_YEAR.value] += 1
                omitted_sources.append(
                    {
                        "ref": source["ref"],
                        "provider": _text(source.get("source")),
                        "reason": SelectionReason.UNKNOWN_YEAR.value,
                    }
                )
                continue
            if (year_start is not None and parsed_year < year_start) or (
                year_end is not None and parsed_year > year_end
            ):
                reason_counts[SelectionReason.OUT_OF_YEAR_RANGE.value] += 1
                omitted_sources.append(
                    {
                        "ref": source["ref"],
                        "provider": _text(source.get("source")),
                        "reason": SelectionReason.OUT_OF_YEAR_RANGE.value,
                    }
                )
                continue
            source["year"] = parsed_year

        eligible_raw.append(source)

    deduped = _deduplicate_candidates(
        eligible_raw,
        reason_counts=reason_counts,
        omitted_sources=omitted_sources,
    )

    query_lower = query.strip().lower()
    query_terms = _tokenize(query)
    query_ids = _query_identifiers(query)
    idf = _idf_weights(query_terms, deduped)

    scored: list[tuple[int, float, dict[str, Any]]] = []
    for orig_idx, src in enumerate(deduped):
        q_score = _score_source(
            src,
            query_lower=query_lower,
            query_terms=query_terms,
            query_ids=query_ids,
            idf=idf,
        )
        scored.append((orig_idx, q_score, src))

    scored.sort(
        key=lambda item: (
            -item[1],
            -float(item[2].get("relevance_score", 0.0)),
            item[0],
        )
    )

    ranked_sources: list[dict[str, Any]] = []
    for rank, (_, q_score, src) in enumerate(scored, start=1):
        enriched = {
            **src,
            "selection_score": q_score,
            "selection_rank": rank,
        }
        if "relevance_score" not in enriched:
            enriched["relevance_score"] = q_score
        ranked_sources.append(enriched)

    prompt_candidates = ranked_sources[:max_prompt_sources]
    capped_sources = ranked_sources[max_prompt_sources:]

    prompt_entries: list[PromptSourceEntry] = []
    source_diags: list[dict[str, Any]] = []

    def _omit_ranked(src: Mapping[str, Any], reason: SelectionReason) -> None:
        reason_counts[reason.value] += 1
        omitted_sources.append(
            {
                "ref": src["ref"],
                "provider": _text(src.get("source")),
                "reason": reason.value,
            }
        )
        source_diags.append(
            {
                "ref": src["ref"],
                "evidence_id": src.get("evidence_id"),
                "status": "omitted",
                "reason": reason.value,
                "locator": None,
                "excerpt_sha256": None,
                "original_bytes": len(source_text(src).encode("utf-8")),
                "excerpt_bytes": 0,
                "relevance_rank": src["selection_rank"],
                "query_score": src["selection_score"],
                "duplicate_count": int(src.get("_duplicate_count", 0)),
            }
        )

    if prompt_candidates and evidence_budget_tokens > 0:
        # Try fitting all prompt candidates intact first before truncating any.
        intact_entries: list[PromptSourceEntry] = []
        for idx, src in enumerate(prompt_candidates, start=1):
            full_entry = _build_prompt_entry(
                index=idx,
                source=src,
                query_terms=query_terms,
                query_ids=query_ids,
                idf=idf,
                max_tokens=evidence_budget_tokens,
            )
            if full_entry is None or full_entry.truncated:
                intact_entries = []
                break
            intact_entries.append(full_entry)

        if (
            intact_entries
            and estimate_prompt_tokens(
                "\n\n".join(e.rendered_block for e in intact_entries)
            )
            <= evidence_budget_tokens
        ):
            prompt_entries = intact_entries
        else:
            remaining = evidence_budget_tokens
            total_candidates = len(prompt_candidates)
            for idx, src in enumerate(prompt_candidates):
                sources_left = total_candidates - idx
                separator_tokens = 1 if prompt_entries else 0
                usable = remaining - separator_tokens
                if usable <= 0:
                    break
                target = min(
                    usable,
                    max(MIN_SOURCE_SHARE_TOKENS, usable // sources_left),
                )
                entry = _build_prompt_entry(
                    index=len(prompt_entries) + 1,
                    source=src,
                    query_terms=query_terms,
                    query_ids=query_ids,
                    idf=idf,
                    max_tokens=target,
                )
                if entry is None:
                    # Fall back to full remaining budget when one source needs more than a share.
                    entry = _build_prompt_entry(
                        index=len(prompt_entries) + 1,
                        source=src,
                        query_terms=query_terms,
                        query_ids=query_ids,
                        idf=idf,
                        max_tokens=usable,
                    )
                if entry is None:
                    _omit_ranked(src, SelectionReason.CONTEXT_BUDGET)
                    continue
                candidate_rendered = "\n\n".join(
                    [*(e.rendered_block for e in prompt_entries), entry.rendered_block]
                )
                if estimate_prompt_tokens(candidate_rendered) > evidence_budget_tokens:
                    _omit_ranked(src, SelectionReason.CONTEXT_BUDGET)
                    continue
                prompt_entries.append(entry)
                remaining = evidence_budget_tokens - estimate_prompt_tokens(
                    candidate_rendered
                )

            included_ranks = {e.relevance_rank for e in prompt_entries}
            already_recorded_ranks = {d["relevance_rank"] for d in source_diags}
            for src in prompt_candidates:
                if (
                    src["selection_rank"] not in included_ranks
                    and src["selection_rank"] not in already_recorded_ranks
                ):
                    _omit_ranked(src, SelectionReason.CONTEXT_BUDGET)
    elif prompt_candidates:
        for src in prompt_candidates:
            _omit_ranked(src, SelectionReason.CONTEXT_BUDGET)

    for src in capped_sources:
        _omit_ranked(src, SelectionReason.SOURCE_CAP)

    for entry in prompt_entries:
        if entry.truncated:
            reason_counts[SelectionReason.EXCERPT_TRUNCATED.value] += 1
        source_diags.append(
            {
                "ref": entry.ref,
                "evidence_id": entry.evidence_id,
                "status": "truncated" if entry.truncated else "included",
                "reason": SelectionReason.EXCERPT_TRUNCATED.value
                if entry.truncated
                else None,
                "locator": entry.locator,
                "excerpt_sha256": entry.excerpt_sha256,
                "original_bytes": entry.original_bytes,
                "excerpt_bytes": entry.excerpt_bytes,
                "relevance_rank": entry.relevance_rank,
                "query_score": entry.query_score,
                "duplicate_count": entry.duplicate_count,
            }
        )

    source_diags.sort(key=lambda d: d["relevance_rank"])

    rendered_sources = (
        "\n\n".join(entry.rendered_block for entry in prompt_entries)
        if prompt_entries
        else "No sources found."
    )
    evidence_tokens_used = (
        estimate_prompt_tokens(rendered_sources) if prompt_entries else 0
    )

    # Strip internal counters before exposing sources to downstream steps.
    clean_eligible = [
        {k: v for k, v in src.items() if not k.startswith("_")}
        for src in ranked_sources
    ]

    diagnostics = SelectionDiagnostics(
        retrieved_count=len(sources),
        eligible_count=len(eligible_raw),
        deduped_count=len(ranked_sources),
        selected_count=len(prompt_entries),
        truncated_count=sum(1 for e in prompt_entries if e.truncated),
        omitted_count=len(omitted_sources),
        reason_counts=dict(reason_counts),
        context_window_tokens=context_window_tokens,
        output_reserve_tokens=output_reserve_tokens,
        instruction_tokens=instruction_tokens,
        evidence_budget_tokens=evidence_budget_tokens,
        evidence_tokens_used=evidence_tokens_used,
        prompt_tokens_estimated=instruction_tokens + evidence_tokens_used,
        sources=tuple(source_diags),
        omitted_sources=tuple(omitted_sources),
    )

    return BoundedSelectionResult(
        eligible_sources=clean_eligible,
        prompt_entries=prompt_entries,
        rendered_sources=rendered_sources,
        diagnostics=diagnostics,
    )
