"""Candidate retrieval from open scholarly sources for the text-reuse check.

OpenAlex answers keyword searches; arXiv supplies open full text for the best
hits. This is the only module in the feature that imports an HTTP client, so
the two allowed hosts, the audit trail and the per-host breaker live here and
nowhere else.

Nothing sensitive is logged: no query term, URL, key or response body. Audit
rows (``EgressEvent``) carry the host, request class, term count, status, byte
count and duration only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import string
import unicodedata
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from importlib.resources import files
from itertools import combinations
from typing import Any, Protocol

import httpx

from src.core.acquisition.sources import LicenseDeclaration, SourceLicense
from src.core.config import settings
from src.core.tools.circuit import BreakerRegistry, CircuitBreaker
from src.core.untrusted_text import sanitize_model_field, strip_invisible
from src.services.document_ingestion import (
    DocumentIngestionError,
    extract_pdf_sandboxed,
)
from src.services.text_reuse.types import ARXIV_ID_PATTERN, Candidate
from src.services.text_reuse.verbatim import reference_region

# The default INFO line of httpx carries the full request URL, search terms included.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

OPENALEX_HOST = "api.openalex.org"
ARXIV_HOST = "arxiv.org"
ALLOWED_HOSTS = frozenset({OPENALEX_HOST, ARXIV_HOST})
CONSENT_RECIPIENTS = tuple(sorted(ALLOWED_HOSTS))

MAX_CANDIDATES = 20
MAX_FULLTEXT = 10
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_ABSTRACT_POSITION = 10_000
MAX_ABSTRACT_WORDS = 20_000
MAX_TITLE_CHARS = 300
MAX_AUTHORS = 50
MAX_AUTHOR_CHARS = 100
QUERY_TERMS = 6
MIN_QUERY_TERMS = 3
MIN_TERM_PARAGRAPHS = 2
TERM_RE = re.compile(r"[a-z0-9-]{5,40}")

_TITLE_WORDS = 25
_SPACING_S = {ARXIV_HOST: 3.0, OPENALEX_HOST: 0.5}
_PER_PAGE = 10
_SELECT = (
    "id,doi,title,authorships,abstract_inverted_index,best_oa_location,"
    "ids,locations,language,publication_year"
)
_ARXIV_ABS = re.compile(rf"https://arxiv\.org/abs/({ARXIV_ID_PATTERN})")
_DOI_PREFIX = re.compile(r"^https?://(?:dx\.)?doi\.org/", re.I)
_PARAGRAPHS = re.compile(r"\n[ \t\r]*\n")
_STOPWORDS = frozenset(
    files("src.services.text_reuse").joinpath("stopwords_en.txt").read_text().split()
)
_SAFE_DOI = re.compile(r"10\.\d{4,9}/[A-Za-z0-9._;()/:-]{1,200}")
_USER_AGENT = "cerebro-similarity-check (open-source research workbench)"


class HostNotAllowedError(ValueError):
    """A request targeted a host or scheme outside the allowlist."""


@dataclass(frozen=True)
class RetrievalBudget:
    max_queries: int = MAX_CANDIDATES
    max_fulltext: int = MAX_FULLTEXT
    deadline_s: float = 180

    def __post_init__(self) -> None:
        if min(self.max_queries, self.max_fulltext, self.deadline_s) < 0:
            raise ValueError("budget values cannot be negative")


@dataclass(frozen=True)
class EgressEvent:
    host: str
    request_class: str
    term_count: int
    status: int | str
    bytes_received: int
    duration_ms: int


class Pacer(Protocol):
    """Cross-worker request spacing plus the clock the spacing is measured on."""

    def slot(self, host: str) -> AbstractAsyncContextManager[None]: ...

    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class RealTime:
    """Wall-clock ``now`` and ``sleep`` for a production pacer to inherit."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


@dataclass
class SourceCoverage:
    queries_sent: int = 0
    responses_ok: int = 0
    failures: dict[str, int] = field(default_factory=dict)
    candidates: int = 0
    full_text_count: int = 0
    abstract_only_count: int = 0
    terms_dropped_non_ascii: int = 0
    deadline_reached: bool = False

    def fail(self, reason: str) -> None:
        self.failures[reason] = self.failures.get(reason, 0) + 1


@dataclass(frozen=True)
class RetrievalResult:
    candidates: tuple[Candidate, ...]
    openalex: SourceCoverage
    arxiv: SourceCoverage


@dataclass(frozen=True)
class QueryPlan:
    queries: tuple[tuple[str, ...], ...]
    terms_dropped_non_ascii: int


# -- queries -----------------------------------------------------------------


def _without_phrases(terms: list[str], phrases: set[frozenset[str]]) -> list[str]:
    """Drop terms until no three of them are three adjacent words of the document.

    Order is ignored: the search API ignores it too, so a reordered phrase leaks as much.
    """
    for trio in combinations(terms, 3):
        if frozenset(trio) in phrases:
            return _without_phrases([t for t in terms if t != trio[-1]], phrases)
    return terms


def _strip_edge_punctuation(token: str) -> str:
    lo, hi = 0, len(token)
    while lo < hi and unicodedata.category(token[lo]).startswith("P"):
        lo += 1
    while hi > lo and unicodedata.category(token[hi - 1]).startswith("P"):
        hi -= 1
    return token[lo:hi]


def _sendable(term: str) -> bool:
    if term in _STOPWORDS or not TERM_RE.fullmatch(term) or term.isdigit():
        return False
    # Letters mixed with digits look like identifiers or secrets, not vocabulary.
    return not (any(c.isdigit() for c in term) and any(c.isalpha() for c in term))


def plan_queries(text: str, limit: int = MAX_CANDIDATES) -> QueryPlan:
    region = reference_region(text)
    body = text[: region[0]] + text[region[1] :] if region else text
    paragraphs = [p for p in _PARAGRAPHS.split(body) if p.strip()]
    dropped = 0
    per_paragraph: list[set[str]] = []
    all_words: list[list[str]] = []
    for paragraph in paragraphs:
        terms: set[str] = set()
        words: list[str] = []
        for raw_token in paragraph.split():
            token = _strip_edge_punctuation(raw_token)
            if not token.isascii():
                dropped += 1
                words.append("")
                continue
            term = token.casefold().strip(string.punctuation)
            words.append(term)
            if _sendable(term):
                terms.add(term)
        per_paragraph.append(terms)
        all_words.append(words)

    frequency = Counter(t for terms in per_paragraph for t in terms)
    eligible = {t for t, n in frequency.items() if n >= MIN_TERM_PARAGRAPHS}
    phrases = _document_phrases(all_words, eligible)
    scored: list[tuple[int, int, tuple[str, ...]]] = []
    for index, terms in enumerate(per_paragraph):
        weights = {t: frequency[t] * min(len(t), 12) for t in terms if t in eligible}
        top = sorted(weights, key=lambda t: (-weights[t], t))[:QUERY_TERMS]
        query = _without_phrases(sorted(top), phrases)
        if len(query) >= MIN_QUERY_TERMS:
            scored.append((-sum(weights[t] for t in query), index, tuple(query)))
    queries: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for _, _, ranked_query in sorted(scored):
        if ranked_query not in seen:
            seen.add(ranked_query)
            queries.append(ranked_query)
    return QueryPlan(tuple(queries[:limit]), dropped)


def _document_phrases(
    all_words: list[list[str]], eligible: set[str]
) -> set[frozenset[str]]:
    """Every three adjacent words of the document that a query could reproduce, plus the
    title's words: a query may never hold three of them, in any order."""
    phrases = {
        frozenset(words[i : i + 3])
        for words in all_words
        for i in range(len(words) - 2)
        if all(w in eligible for w in words[i : i + 3])
    }
    first = all_words[0] if all_words else []
    title = {w for w in first if w in eligible} if len(first) <= _TITLE_WORDS else set()
    phrases.update(frozenset(trio) for trio in combinations(sorted(title), 3))
    return phrases


def build_queries(text: str, limit: int = MAX_CANDIDATES) -> list[tuple[str, ...]]:
    return list(plan_queries(text, limit).queries)


# -- HTTP --------------------------------------------------------------------


async def _enforce_host_policy(request: httpx.Request) -> None:
    if (
        request.url.scheme != "https"
        or request.url.host not in ALLOWED_HOSTS
        or request.url.port is not None
    ):
        raise HostNotAllowedError("request refused: host or scheme not allowed")


def make_client() -> httpx.AsyncClient:
    """The only way to build the client: no redirects, https and two hosts."""
    return httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(15.0),
        headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "identity"},
        event_hooks={"request": [_enforce_host_policy]},
    )


_BREAKERS = BreakerRegistry(
    lambda: CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
)


@dataclass
class _Reply:
    body: bytes | None
    failure: str | None = None
    status: int | str = ""

    def __post_init__(self) -> None:
        if self.status == "":
            self.status = self.failure or 0


@dataclass(frozen=True)
class _Request:
    host: str
    url: str
    params: dict[str, Any] | None = field(repr=False)
    cap: int
    request_class: str
    term_count: int
    # Credentials ride in a header: OpenAlex retains full request URLs.
    headers: dict[str, str] | None = field(default=None, repr=False)


class _Session:
    def __init__(
        self,
        http: httpx.AsyncClient,
        pacer: Pacer,
        record_egress: Callable[[EgressEvent], Awaitable[None]],
        deadline_s: float,
    ) -> None:
        self.http, self.pacer, self.record_egress = http, pacer, record_egress
        self.started = pacer.now()
        self.deadline_s = deadline_s

    def remaining(self) -> float:
        return self.deadline_s - (self.pacer.now() - self.started).total_seconds()

    async def get(self, request: _Request) -> _Reply:
        breaker = _BREAKERS.for_tool(request.host)
        if self.remaining() <= 0:
            return _Reply(None, "deadline_reached")
        if not breaker.allows(self.pacer.now()):
            await self._emit(request, "breaker_open", 0, 0)
            return _Reply(None, "breaker_open")
        judged = False
        try:
            reply = await self._paced_fetch(request, breaker)
            judged = True
        finally:
            if not judged:
                breaker.record_abandoned()
        return reply

    async def _paced_fetch(self, request: _Request, breaker: CircuitBreaker) -> _Reply:
        async with AsyncExitStack() as stack:
            try:
                await asyncio.wait_for(
                    stack.enter_async_context(self.pacer.slot(request.host)),
                    self.remaining(),
                )
            except TimeoutError:
                breaker.record_abandoned()
                return _Reply(None, "deadline_reached")
            started = self.pacer.now()
            received = 0
            try:
                reply, received = await asyncio.wait_for(
                    self._fetch(request, breaker), max(self.remaining(), 0.001)
                )
            except TimeoutError:
                breaker.record_failure(self.pacer.now())
                reply = _Reply(None, "deadline_reached", "timeout")
            except asyncio.CancelledError:
                # The request may already have left; the audit row must exist anyway.
                await asyncio.shield(self._emit(request, "cancelled", 0, 0))
                raise
            elapsed = int((self.pacer.now() - started).total_seconds() * 1000)
            # Before the spacing sleep, so a cancellation there cannot lose the row.
            await asyncio.shield(self._emit(request, reply.status, received, elapsed))
            await self.pacer.sleep(_SPACING_S[request.host])
        return reply

    async def _fetch(
        self, request: _Request, breaker: CircuitBreaker
    ) -> tuple[_Reply, int]:
        received = 0
        try:
            async with self.http.stream(
                "GET", request.url, params=request.params, headers=request.headers
            ) as response:
                status = response.status_code
                if status >= 300:
                    return self._refusal(status, breaker), 0
                if (
                    response.headers.get("content-encoding", "identity").lower()
                    != "identity"
                ):
                    # Decoding is what a compression bomb needs; the cap counts decoded bytes.
                    breaker.record_abandoned()
                    return _Reply(
                        None, "unsupported_encoding", "unsupported_encoding"
                    ), 0
                chunks: list[bytes] = []
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > request.cap:
                        breaker.record_abandoned()
                        return _Reply(None, "oversize"), received
                    chunks.append(chunk)
        except httpx.TimeoutException:
            breaker.record_failure(self.pacer.now())
            return _Reply(None, "timeout"), received
        except httpx.RequestError:
            breaker.record_failure(self.pacer.now())
            return _Reply(None, "network_error"), received
        breaker.record_success()
        return _Reply(b"".join(chunks), None, status), received

    def _refusal(self, status: int, breaker: CircuitBreaker) -> _Reply:
        if status < 400:
            breaker.record_abandoned()
            return _Reply(None, "redirect")
        if status == 429 or status >= 500:
            breaker.record_failure(self.pacer.now())
        else:
            breaker.record_abandoned()
        return _Reply(None, f"http_{status}", status)

    async def _emit(
        self, request: _Request, status: int | str, received: int, duration_ms: int
    ) -> None:
        await self.record_egress(
            EgressEvent(
                request.host,
                request.request_class,
                request.term_count,
                status,
                received,
                duration_ms,
            )
        )


# -- parsing -----------------------------------------------------------------


class _ParseError(ValueError):
    pass


def _abstract(index: Any) -> str:
    if not index:
        return ""
    if not isinstance(index, dict):
        raise _ParseError("inverted index is not an object")
    words: dict[int, str] = {}
    total = 0
    for word, positions in index.items():
        if not isinstance(positions, list):
            raise _ParseError("positions are not a list")
        total += len(positions)
        if total > MAX_ABSTRACT_WORDS:
            raise _ParseError("abstract too long")
        for position in positions:
            if (
                not isinstance(position, int)
                or isinstance(position, bool)
                or not 0 <= position <= MAX_ABSTRACT_POSITION
            ):
                raise _ParseError("position out of range")
            words[position] = word
    return " ".join(words[p] for p in sorted(words))


def _clean(text: Any, limit: int) -> str:
    return sanitize_model_field(strip_invisible(str(text or ""))[0], limit)[0].strip()


def _arxiv_id(record: dict[str, Any]) -> str | None:
    urls = [loc.get("landing_page_url") for loc in record.get("locations") or []]
    urls += list((record.get("ids") or {}).values())
    for url in urls:
        match = _ARXIV_ABS.fullmatch(url) if isinstance(url, str) else None
        if match:
            return match.group(1)
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _licence(record: dict[str, Any]) -> SourceLicense:
    identifier = _as_dict(record.get("best_oa_location")).get("license")
    if isinstance(identifier, str) and identifier:
        return SourceLicense(identifier, LicenseDeclaration.API_FIELD)
    return SourceLicense(None, LicenseDeclaration.UNDECLARED)


def _plausible_year(year: Any) -> bool:
    return isinstance(year, int) and not isinstance(year, bool) and 1000 <= year <= 2100


def _safe_doi(doi: Any) -> str | None:
    """A DOI with markup characters is dropped; the rest of the record is kept."""
    bare = _DOI_PREFIX.sub("", doi) if isinstance(doi, str) else ""
    return bare if _SAFE_DOI.fullmatch(bare) else None


def _candidate(record: Any) -> Candidate:
    if not isinstance(record, dict):
        raise _ParseError("record is not an object")
    doi = record.get("doi")
    year = record.get("publication_year")
    names = [
        _clean(
            _as_dict(_as_dict(a).get("author")).get("display_name"), MAX_AUTHOR_CHARS
        )
        for a in (record.get("authorships") or [])[:MAX_AUTHORS]
    ]
    return Candidate(
        source_id=str(record.get("id", "")).rsplit("/", 1)[-1],
        origin="openalex",
        title=_clean(record.get("title"), MAX_TITLE_CHARS),
        authors=tuple(n for n in names if n),
        year=year if _plausible_year(year) else None,
        doi=_safe_doi(doi),
        arxiv_id=_arxiv_id(record),
        licence=_licence(record),
        text=strip_invisible(_abstract(record.get("abstract_inverted_index")))[0],
        text_kind="abstract",
    )


def _keys(c: Candidate) -> list[tuple[str, str]]:
    """Identifiers of one work, strongest first; any shared one means the same work."""
    keys = [("openalex", c.source_id)]
    if c.arxiv_id:
        keys.insert(0, ("arxiv", c.arxiv_id))
    if c.doi:
        keys.insert(0, ("doi", c.doi.lower()))
    return keys


# -- retrieval ---------------------------------------------------------------


class _Pool:
    """Candidates deduplicated across DOI, arXiv id and OpenAlex id, with hit counts."""

    def __init__(self) -> None:
        self.found: dict[tuple[str, str], Candidate] = {}
        self.hits: Counter[tuple[str, str]] = Counter()
        self.alias: dict[tuple[str, str], tuple[str, str]] = {}

    def add(self, candidate: Candidate) -> None:
        keys = _keys(candidate)
        primary = next((self.alias[k] for k in keys if k in self.alias), keys[0])
        existing = self.found.setdefault(primary, candidate)
        if candidate.arxiv_id and not existing.arxiv_id:
            self.found[primary] = replace(existing, arxiv_id=candidate.arxiv_id)
        self.alias.update({k: primary for k in keys if k not in self.alias})
        self.hits[primary] += 1

    def top(self) -> list[Candidate]:
        ranked = sorted(
            self.found,
            key=lambda k: (-self.hits[k], -(self.found[k].year or 0), k),
        )
        return [self.found[k] for k in ranked[:MAX_CANDIDATES]]


async def _search(
    session: _Session, terms: tuple[str, ...], coverage: SourceCoverage
) -> list[Any] | None:
    """Run one OpenAlex query; None means stop (deadline), [] means nothing usable."""
    params: dict[str, Any] = {
        "search": " ".join(terms),
        "per-page": _PER_PAGE,
        "select": _SELECT,
    }
    headers = (
        {"Authorization": f"Bearer {settings.OPENALEX_API_KEY}"}
        if settings.OPENALEX_API_KEY
        else None
    )
    reply = await session.get(
        _Request(
            OPENALEX_HOST,
            f"https://{OPENALEX_HOST}/works",
            params,
            MAX_JSON_BYTES,
            "search",
            len(terms),
            headers,
        )
    )
    if reply.failure == "deadline_reached":
        coverage.deadline_reached = True
        return None
    coverage.queries_sent += reply.failure != "breaker_open"
    if reply.body is None:
        coverage.fail(reply.failure or "unknown")
        return []
    try:
        results = json.loads(reply.body)["results"]
        if not isinstance(results, list):
            raise _ParseError("results is not a list")
    except (ValueError, KeyError, TypeError, RecursionError):
        coverage.fail("parse_error")
        return []
    coverage.responses_ok += 1
    return results


async def retrieve_candidates(
    document_text: str,
    *,
    budget: RetrievalBudget,
    http: httpx.AsyncClient,
    record_egress: Callable[[EgressEvent], Awaitable[None]],
    pacer: Pacer,
) -> RetrievalResult:
    if _enforce_host_policy not in http.event_hooks["request"] or http.follow_redirects:
        raise ValueError("http client must come from make_client()")
    session = _Session(http, pacer, record_egress, budget.deadline_s)
    openalex, arxiv = SourceCoverage(), SourceCoverage()
    plan = await asyncio.to_thread(
        plan_queries, document_text, min(budget.max_queries, MAX_CANDIDATES)
    )
    openalex.terms_dropped_non_ascii = plan.terms_dropped_non_ascii

    pool = _Pool()
    for terms in plan.queries:
        records = await _search(session, terms, openalex)
        if records is None:
            break
        for record in records:
            try:
                pool.add(_candidate(record))
            except (ValueError, AttributeError, TypeError):
                openalex.fail("parse_error")

    chosen = pool.top()
    chosen = await _upgrade_to_full_text(chosen, budget, session, arxiv)
    openalex.candidates = len(chosen)
    openalex.abstract_only_count = sum(c.text_kind == "abstract" for c in chosen)
    return RetrievalResult(tuple(chosen), openalex, arxiv)


async def _upgrade_to_full_text(
    chosen: list[Candidate],
    budget: RetrievalBudget,
    session: _Session,
    coverage: SourceCoverage,
) -> list[Candidate]:
    eligible = [i for i, c in enumerate(chosen) if c.arxiv_id][
        : min(budget.max_fulltext, MAX_FULLTEXT)
    ]
    coverage.candidates = len(eligible)
    for index in eligible:
        candidate = chosen[index]
        reply = await session.get(
            _Request(
                ARXIV_HOST,
                f"https://{ARXIV_HOST}/pdf/{candidate.arxiv_id}",
                None,
                MAX_PDF_BYTES,
                "pdf",
                0,
            )
        )
        if reply.failure == "deadline_reached":
            coverage.deadline_reached = True
            break
        coverage.queries_sent += reply.failure != "breaker_open"
        if reply.body is None:
            coverage.fail(reply.failure or "unknown")
            continue
        coverage.responses_ok += 1
        try:
            extracted, _ = await asyncio.wait_for(
                extract_pdf_sandboxed(reply.body), max(session.remaining(), 0.001)
            )
        except TimeoutError:
            coverage.deadline_reached = True
            break
        except DocumentIngestionError:
            coverage.fail("extract_error")
            continue
        # The sandbox has already removed invisible characters.
        text = extracted.text
        # The licence OpenAlex reported describes its best open copy, not this arXiv PDF.
        chosen[index] = replace(
            candidate,
            text=text,
            text_kind="full_text",
            licence=SourceLicense(None, LicenseDeclaration.UNDECLARED),
        )
        coverage.full_text_count += 1
    return chosen
