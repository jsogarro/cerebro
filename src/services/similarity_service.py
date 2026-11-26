"""Run a similarity check over a stored document and persist what it keeps.

Verbatim matching (``src.services.text_reuse``) is pure and always runs. Open-source
candidates come from ``scholarly_retrieval`` and only with the caller's consent. This
module owns the constants, the consent text, the cross-worker egress pacer, the runner
and the coverage record that makes a check with no matches readable as "none found in
the sources searched" rather than as a verdict.
"""

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.acquisition.sources import LicenseDeclaration, SourceLicense
from src.core.config import settings
from src.core.memory_encryption import document_cipher
from src.core.untrusted_text import sanitize_model_field, strip_invisible
from src.middleware.tenant_context import set_postgres_tenant_context
from src.repositories.similarity_repository import (
    EgressRecord,
    MatchRecord,
    SimilarityRepository,
)
from src.repositories.uploaded_document_repository import UploadedDocumentRepository
from src.services import scholarly_retrieval
from src.services.document_jobs import JobLimits, Outcome, SessionFactory
from src.services.scholarly_retrieval import (
    EgressEvent,
    RealTime,
    RetrievalBudget,
    RetrievalResult,
    build_queries,
)
from src.services.text_alignment.alignment import NORMALIZER_VERSION, UNICODE_VERSION
from src.services.text_reuse.types import (
    MAX_TOTAL_CANDIDATE_CHARS,
    MIN_MATCH_TOKENS,
    SHINGLE_TOKENS,
    TOKEN_POLICY_VERSION,
    Candidate,
    ReuseBudget,
)
from src.services.text_reuse.verbatim import author_key, find_verbatim_reuse

SIMILARITY_PER_WORKER_CONCURRENCY = 1
SIMILARITY_PER_USER_DAILY = 10
SIMILARITY_PER_ORG_CONCURRENT = 2
SIMILARITY_OPEN_SOURCE_GLOBAL_DAILY = 100
# 04's total, shared so admission and the runner cannot disagree.
CANDIDATE_CHAR_BUDGET = MAX_TOTAL_CANDIDATE_CHARS
MAX_COMPARISON_IDS = 10
MAX_OWN_UPLOADS = 10
MAX_TITLE_CHARS = 200
MAX_EXCERPT_CHARS = 1000
EXCERPT_LICENCE_ALLOWLIST = frozenset({"cc0", "cc-by", "cc-by-sa", "public-domain"})
RETRIEVAL_DEADLINE_S = 180
MATCHING_DEADLINE_S = 120

SIMILARITY_LIMITS = JobLimits(
    per_user_daily=SIMILARITY_PER_USER_DAILY,
    per_org_concurrent=SIMILARITY_PER_ORG_CONCURRENT,
)
OPEN_SOURCE_LIMITS = JobLimits(global_daily_count=SIMILARITY_OPEN_SOURCE_GLOBAL_DAILY)

SIMILARITY_CONSENT_TEXT_VERSION = "2026-11-25.1"
SIMILARITY_CONSENT_TEXT = (
    "Keyword queries built from rare words of this document will be sent to "
    "api.openalex.org, and the arXiv ids of the best matches will be fetched from "
    "arxiv.org. No document text is sent beyond those query terms. OpenAlex logs "
    "request URLs, with the service's key and IP address, for 90 days. Matching "
    "happens on this server. Do not use open sources for material you may not "
    "disclose the topic of."
)
QUERY_PREVIEW_RULE = (
    "Each query is at most 6 lowercase words of 5 to 40 characters, sorted "
    "alphabetically, taken from words that occur in at least 2 paragraphs; at most "
    "20 queries; the title and any sentence are never sent."
)
NOT_REQUESTED = "not_requested"
UNAVAILABLE = "not_run (unavailable)"

Retriever = Callable[..., Awaitable[RetrievalResult]]
ORIGINS = ("own_upload", "comparison", "openalex", "arxiv")


def consent_sha256() -> str:
    return hashlib.sha256(SIMILARITY_CONSENT_TEXT.encode()).hexdigest()


def terms_verified() -> bool:
    try:
        return date.fromisoformat(settings.PROVIDER_TERMS_VERIFIED_ON) <= date.today()
    except ValueError:
        return False


def query_preview(document_text: str) -> list[list[str]]:
    return [list(q) for q in build_queries(document_text)]


def policy_versions() -> dict[str, Any]:
    return {
        "TOKEN_POLICY_VERSION": TOKEN_POLICY_VERSION,
        "MIN_MATCH_TOKENS": MIN_MATCH_TOKENS,
        "SHINGLE_TOKENS": SHINGLE_TOKENS,
        "normalizer_version": NORMALIZER_VERSION,
        "unicode_version": UNICODE_VERSION,
    }


class PgHostPacer(RealTime):
    """At most one request per host in flight across every worker.

    The advisory lock is held for the request and its spacing sleep, so consecutive
    requests to a host are at least its spacing apart. A waiter polls with a short
    transaction and holds no pooled connection while it waits.
    """

    def __init__(self, session_factory: SessionFactory, poll_s: float = 0.25) -> None:
        self._session_factory = session_factory
        self._poll_s = poll_s

    @asynccontextmanager
    async def slot(self, host: str) -> AsyncIterator[None]:
        while True:
            async with self._session_factory() as session:
                acquired = await session.scalar(
                    text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                    {"key": f"egress:{host}"},
                )
                if acquired:
                    yield
                    await session.commit()
                    return
            await asyncio.sleep(self._poll_s)


async def live_retrieve(document_text: str, **kwargs: Any) -> RetrievalResult:
    async with scholarly_retrieval.make_client() as http:
        return await scholarly_retrieval.retrieve_candidates(
            document_text, http=http, **kwargs
        )


@dataclass(frozen=True)
class CheckJob:
    check_id: uuid.UUID
    document_id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID
    options: dict[str, Any]


@dataclass
class RunResult:
    matches: list[MatchRecord]
    coverage: dict[str, Any]
    status: str


@dataclass
class _Sources:
    candidates: list[Candidate] = field(default_factory=list)
    comparison_ids: set[str] = field(default_factory=set)
    total_chars: int = 0
    skipped_budget: dict[str, int] = field(default_factory=dict)
    unavailable: int = 0
    loaded: dict[str, int] = field(default_factory=dict)

    def fits(self, chars: int) -> bool:
        return self.total_chars + chars <= CANDIDATE_CHAR_BUDGET

    def add(self, candidate: Candidate, origin: str | None = None) -> None:
        self.candidates.append(candidate)
        self.total_chars += len(candidate.text)
        if origin:
            self.loaded[origin] = self.loaded.get(origin, 0) + 1

    def skip(self, origin: str) -> None:
        self.skipped_budget[origin] = self.skipped_budget.get(origin, 0) + 1


def _clean_title(title: str) -> str:
    return sanitize_model_field(strip_invisible(title)[0], MAX_TITLE_CHARS)[0].strip()


def _upload_candidate(document_id: uuid.UUID, filename: str, body: str) -> Candidate:
    return Candidate(
        source_id=str(document_id),
        origin="own_upload",
        title=_clean_title(filename),
        authors=(),
        year=None,
        doi=None,
        arxiv_id=None,
        licence=SourceLicense(None, LicenseDeclaration.UNDECLARED),
        text=body,
        text_kind="full_text",
    )


async def _load_uploads(
    session_factory: SessionFactory, job: CheckJob
) -> tuple[str, list[str], _Sources]:
    """The document, its declared authors and the caller's own comparison corpora.

    One short session, closed before any long wait, so no pooled connection is held
    while retrieval or matching runs.
    """
    owner = {"organization_id": job.organization_id, "user_id": job.user_id}
    sources = _Sources()
    async with session_factory() as session:
        await set_postgres_tenant_context(session, str(job.organization_id))
        repository = UploadedDocumentRepository(session, document_cipher())
        extracted = await repository.get_text(job.document_id, **owner)
        meta = await repository.get_meta(job.document_id, **owner)
        if extracted is None or meta is None:
            raise LookupError("document unavailable")
        wanted = [
            ("comparison", i)
            for i in map(uuid.UUID, job.options["comparison_document_ids"])
            if i != job.document_id
        ]
        if job.options["own_uploads"]:
            recent = await repository.list_recent(
                limit=MAX_OWN_UPLOADS + MAX_COMPARISON_IDS + 1, **owner
            )
            skip = {job.document_id, *(i for _, i in wanted)}
            others = [m.id for m in recent if m.id not in skip]
            wanted += [("own_upload", i) for i in others[:MAX_OWN_UPLOADS]]
        for origin, other_id in wanted:
            other = await repository.get_meta(other_id, **owner)
            if other is None:
                sources.unavailable += 1
            elif not sources.fits(other.char_count):
                sources.skip(origin)
            elif (body := await repository.get_text(other_id, **owner)) is None:
                sources.unavailable += 1
            else:
                sources.add(
                    _upload_candidate(other_id, other.filename, body.text), origin
                )
                if origin == "comparison":
                    sources.comparison_ids.add(str(other_id))
    return extracted.text, meta.declared_authors, sources


def _origin_of(candidate: Candidate, comparison_ids: set[str]) -> str:
    if candidate.origin == "own_upload":
        return "comparison" if candidate.source_id in comparison_ids else "own_upload"
    return (
        "arxiv"
        if candidate.arxiv_id and candidate.text_kind == "full_text"
        else "openalex"
    )


def _allows_excerpt(candidate: Candidate) -> bool:
    return (
        candidate.origin == "own_upload"
        or candidate.licence.identifier in EXCERPT_LICENCE_ALLOWLIST
    )


def _source_id(candidate: Candidate, origin: str) -> str:
    return (
        candidate.arxiv_id
        if origin == "arxiv" and candidate.arxiv_id
        else candidate.source_id
    )


def _licence_metadata(candidate: Candidate) -> dict[str, Any]:
    """The stored licence, bounded: its identifier and statement come from a third party."""
    metadata = dict(candidate.licence.as_metadata())
    if metadata.get("identifier"):
        metadata["identifier"] = str(metadata["identifier"])[:64]
    metadata["statement"] = None
    return metadata


def _record(match: Any, candidate: Candidate, origin: str) -> MatchRecord:
    excerpt = (
        candidate.text[match.src_start : match.src_end][:MAX_EXCERPT_CHARS]
        if _allows_excerpt(candidate)
        else None
    )
    return MatchRecord(
        kind=match.kind,
        attribution=match.attribution.value,
        from_user_upload=match.from_user_upload,
        doc_start=match.doc_start,
        doc_end=match.doc_end,
        tokens=match.tokens,
        source_origin=origin,
        source_document_id=(
            uuid.UUID(candidate.source_id) if candidate.origin == "own_upload" else None
        ),
        source_id=_source_id(candidate, origin),
        source_title=_clean_title(candidate.title),
        source_year=candidate.year,
        source_licence=_licence_metadata(candidate),
        source_text_kind=candidate.text_kind,
        src_start=match.src_start,
        src_end=match.src_end,
        source_excerpt=excerpt,
        normalizations_applied=tuple(sorted(match.normalizations_applied)),
        declared_author_overlap=match.declared_author_overlap,
        section=match.section,
    )


def _blank_row() -> dict[str, Any]:
    return {
        "candidates": 0,
        "full_text": 0,
        "abstract_only": 0,
        "skipped_budget": 0,
        "failures": {},
        "queries_sent": 0,
    }


def _retrieval_rows(retrieved: RetrievalResult) -> dict[str, dict[str, Any]]:
    oa, ax = retrieved.openalex, retrieved.arxiv
    return {
        "openalex": {
            "candidates": oa.candidates,
            "full_text": ax.full_text_count,
            "abstract_only": oa.abstract_only_count,
            "skipped_budget": 0,
            "failures": dict(oa.failures),
            "queries_sent": oa.queries_sent,
        },
        "arxiv": {
            "candidates": ax.candidates,
            "full_text": ax.full_text_count,
            "abstract_only": 0,
            "skipped_budget": 0,
            "failures": dict(ax.failures),
            "queries_sent": ax.queries_sent,
        },
    }


def _partial_reasons(
    options: dict[str, Any],
    rows: dict[str, dict[str, Any]],
    skipped: dict[str, int],
    sources: _Sources,
    deadline: bool,
) -> list[str]:
    reasons = {
        reason
        for reason in ("budget_exhausted", "deadline_reached", "too_many_matches")
        if skipped.get(reason)
    }
    flags = {
        "failed_sources": any(row["failures"] for row in rows.values()),
        "skipped_budget": any(row["skipped_budget"] for row in rows.values()),
        "deadline_reached": deadline,
        "source_unavailable": bool(sources.unavailable),
        "semantic_not_run": bool(options["semantic"]),
    }
    reasons |= {name for name, raised in flags.items() if raised}
    return sorted(reasons)


@dataclass
class _Retrieved:
    rows: dict[str, dict[str, Any]]
    terms_dropped: int = 0
    deadline: bool = False


class _Run:
    """One check's work: load, retrieve, match, and describe what was covered."""

    def __init__(
        self, job: CheckJob, session_factory: SessionFactory, retrieve: Retriever
    ) -> None:
        self._job = job
        self._session_factory = session_factory
        self._retrieve = retrieve

    async def record_egress(self, event: EgressEvent) -> None:
        job = self._job
        async with self._session_factory() as session:
            await set_postgres_tenant_context(session, str(job.organization_id))
            await SimilarityRepository(session, document_cipher()).add_egress(
                EgressRecord(
                    event.host,
                    event.request_class,
                    event.term_count,
                    event.status,
                    event.bytes_received,
                    event.duration_ms,
                ),
                check_id=job.check_id,
                organization_id=job.organization_id,
                user_id=job.user_id,
            )
            await session.commit()

    async def _open_sources(self, body: str, sources: _Sources) -> _Retrieved:
        rows = {origin: _blank_row() for origin in ORIGINS}
        if not self._job.options["open_sources"]:
            return _Retrieved(rows)
        retrieved = await self._retrieve(
            body,
            budget=RetrievalBudget(deadline_s=RETRIEVAL_DEADLINE_S),
            record_egress=self.record_egress,
            pacer=PgHostPacer(self._session_factory),
        )
        rows.update(_retrieval_rows(retrieved))
        for candidate in retrieved.candidates:
            if sources.fits(len(candidate.text)):
                sources.add(candidate)
            else:
                rows["openalex"]["skipped_budget"] += 1
        return _Retrieved(
            rows,
            retrieved.openalex.terms_dropped_non_ascii,
            retrieved.openalex.deadline_reached or retrieved.arxiv.deadline_reached,
        )

    async def __call__(self) -> RunResult:
        options = self._job.options
        body, declared, sources = await _load_uploads(self._session_factory, self._job)
        retrieved = await self._open_sources(body, sources)
        rows = retrieved.rows
        for origin, count in sources.loaded.items():
            rows[origin].update(candidates=count, full_text=count)
        for origin, count in sources.skipped_budget.items():
            rows[origin]["skipped_budget"] += count

        result = await asyncio.to_thread(
            find_verbatim_reuse,
            body,
            sources.candidates,
            declared_authors=[author_key(a) for a in declared],
            budget=ReuseBudget(deadline_s=MATCHING_DEADLINE_S),
        )
        by_id = {c.source_id: c for c in sources.candidates}
        matches = []
        for m in result.matches:
            cand = by_id[m.candidate_source_id]
            matches.append(_record(m, cand, _origin_of(cand, sources.comparison_ids)))
        skipped: dict[str, int] = {}
        for source_id, reason in result.skipped:
            skipped[reason] = skipped.get(reason, 0) + 1
            if reason in ("too_long", "budget_exhausted"):
                origin = _origin_of(by_id[source_id], sources.comparison_ids)
                rows[origin]["skipped_budget"] += 1
        reasons = _partial_reasons(options, rows, skipped, sources, retrieved.deadline)
        semantic = UNAVAILABLE if options["semantic"] else NOT_REQUESTED
        coverage = {
            "sources_searched": {
                o: r for o, r in rows.items() if _searched(o, r, options)
            },
            "comparisons_run": {
                "verbatim": "run",
                "paraphrase": semantic,
                "translation": semantic,
            },
            "excluded_regions": [
                {"kind": r.kind, "start": r.start, "end": r.end, "tokens": r.tokens}
                for r in result.excluded_regions
            ],
            "reference_heading_found": bool(result.excluded_regions),
            "skipped": skipped,
            "unavailable_sources": sources.unavailable,
            "partial_reasons": reasons,
            "terms_dropped_non_ascii": retrieved.terms_dropped,
        }
        return RunResult(matches, coverage, "partial" if reasons else "succeeded")


def make_work(
    job: CheckJob,
    *,
    session_factory: SessionFactory,
    retrieve: Retriever = live_retrieve,
) -> Callable[[], Awaitable[RunResult]]:
    return _Run(job, session_factory, retrieve)


def _searched(origin: str, row: dict[str, Any], options: dict[str, Any]) -> bool:
    if origin in ("openalex", "arxiv"):
        return bool(options["open_sources"])
    return bool(row["candidates"] or row["skipped_budget"])


def make_persist(
    *, check_id: uuid.UUID, organization_id: uuid.UUID, user_id: uuid.UUID
) -> Callable[[AsyncSession, RunResult], Awaitable[Outcome]]:
    async def persist(session: AsyncSession, result: RunResult) -> Outcome:
        await SimilarityRepository(session, document_cipher()).add_matches(
            check_id=check_id,
            organization_id=organization_id,
            user_id=user_id,
            matches=result.matches,
        )
        return result.status, {"coverage": result.coverage}

    return persist
