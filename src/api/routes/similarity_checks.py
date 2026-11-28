"""Start, read, report on and delete similarity checks of the caller's own documents.

Nothing here accepts document text: a request body carries ids, booleans and the
consent block only, so the 422 handler's echo of the input cannot repeat a
manuscript. Every read is owner-scoped and a miss is always 404. The report is
served as plain text.
"""

import asyncio
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.memory_encryption import (
    DocumentEncryptionUnavailable,
    MemoryEncryption,
    document_cipher,
)
from src.middleware.tenant_context import (
    OwnerIds,
    TenantContext,
    get_tenant_context,
    get_tenant_identity,
    owner_ids,
)
from src.models.db.job_constants import ACTIVE_STATUSES
from src.models.db.session import SessionDep, get_session_factory
from src.models.db.similarity_check import SIMILARITY_CHECKS
from src.repositories.similarity_repository import (
    MAX_LIST,
    MAX_MATCH_PAGE,
    MAX_REPORT_MATCHES,
    SIMILARITY_DEADLINE_S,
    CheckView,
    MatchView,
    NewCheck,
    SimilarityRepository,
    active_similarity_checks,
)
from src.repositories.uploaded_document_repository import (
    DocumentMeta,
    UploadedDocumentRepository,
)
from src.services import similarity_service as service
from src.services.document_jobs import QuotaExceeded, admit, cancel_all, run_job
from src.services.scholarly_retrieval import CONSENT_RECIPIENTS
from src.services.text_reuse.report import render_report

router = APIRouter(tags=["similarity"])

NO_STORE = "no-store"
# One check can use most of its deadline (retrieval plus matching), so a longer queue
# behind the one-at-a-time semaphore would usually fail with deadline_exceeded before it
# ran; the deadline counts queueing time.
MAX_QUEUED_PER_WORKER = 2


class ConsentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipients: list[Annotated[str, Field(max_length=253)]] = Field(max_length=5)
    consent_text_version: str = Field(max_length=50)


class OpenSourcesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool
    consent: ConsentBody | None = None


class SimilarityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    own_uploads: StrictBool = False
    comparison_document_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=service.MAX_COMPARISON_IDS
    )
    open_sources: OpenSourcesBody | None = None
    semantic: StrictBool = False


class MatchOut(BaseModel):
    kind: str
    attribution: str
    from_user_upload: bool
    doc_start: int
    doc_end: int
    tokens: int
    cosine: float | None
    source_origin: str
    source_id: str
    source_title: str
    source_year: int | None
    source_licence: dict[str, Any]
    source_text_kind: str
    src_start: int
    src_end: int
    source_excerpt: str | None
    normalizations_applied: list[str]
    declared_author_overlap: bool
    section: str

    @classmethod
    def of(cls, match: MatchView) -> "MatchOut":
        data = {
            k: v
            for k, v in vars(match).items()
            if k not in ("id", "source_document_id")
        }
        return cls(
            **{**data, "normalizations_applied": list(match.normalizations_applied)}
        )


class CheckSummaryOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    status: str
    status_reason: str | None
    options: dict[str, Any]
    consent_text_version: str | None
    coverage: dict[str, Any]
    policy_versions: dict[str, Any]
    created_at: str
    started_at: str | None
    completed_at: str | None

    @classmethod
    def summary_of(cls, view: CheckView) -> dict[str, Any]:
        return {
            "id": view.id,
            "document_id": view.document_id,
            "status": view.status,
            "status_reason": view.status_reason,
            "options": view.options,
            "consent_text_version": view.consent_text_version,
            "coverage": view.coverage,
            "policy_versions": view.policy_versions,
            "created_at": view.created_at.isoformat(),
            "started_at": view.started_at.isoformat() if view.started_at else None,
            "completed_at": view.completed_at.isoformat()
            if view.completed_at
            else None,
        }


class CheckOut(CheckSummaryOut):
    match_total: int
    matches: list[MatchOut]

    @classmethod
    def of(cls, view: CheckView) -> "CheckOut":
        return cls(
            **cls.summary_of(view),
            match_total=view.match_total,
            matches=[MatchOut.of(m) for m in view.matches],
        )


class CheckAccepted(BaseModel):
    id: uuid.UUID
    status: str


def get_retriever() -> service.Retriever:
    return service.live_retrieve


def get_job_session_factory() -> Callable[[], AsyncSession]:
    return get_session_factory()


def _error(code: int, name: str, details: Any = None) -> HTTPException:
    detail: dict[str, Any] = {"code": name}
    if details is not None:
        detail["details"] = details
    return HTTPException(
        status_code=code, detail=detail, headers={"Cache-Control": NO_STORE}
    )


def _missing() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, headers={"Cache-Control": NO_STORE}
    )


TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
IdentityDep = Annotated[TenantContext, Depends(get_tenant_identity)]
SessionDep = Annotated[AsyncSession, SessionDep]


@dataclass(frozen=True)
class Scope:
    """The request's session and the caller it is scoped to."""

    session: AsyncSession
    ids: OwnerIds


async def get_scope(session: SessionDep, tenant: TenantDep) -> Scope:
    return Scope(session, owner_ids(tenant))


ScopeDep = Annotated[Scope, Depends(get_scope)]


@dataclass(frozen=True)
class Runner:
    """What a started check needs from this worker."""

    state: Any
    retrieve: service.Retriever
    session_factory: Callable[[], AsyncSession]


async def get_runner(
    request: Request,
    retrieve: Annotated[service.Retriever, Depends(get_retriever)],
    session_factory: Annotated[
        Callable[[], AsyncSession], Depends(get_job_session_factory)
    ],
) -> Runner:
    return Runner(request.app.state, retrieve, session_factory)


RunnerDep = Annotated[Runner, Depends(get_runner)]


async def _enabled(response: Response, _: TenantDep) -> None:
    """Runs after authentication and before the body is parsed, so a dark feature is a 404."""
    if not settings.SIMILARITY_CHECKS_ENABLED:
        raise _missing()
    response.headers["Cache-Control"] = NO_STORE


def _cipher() -> MemoryEncryption:
    try:
        return document_cipher()
    except DocumentEncryptionUnavailable:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "document_storage_unavailable"
        ) from None


def _repository(session: AsyncSession) -> SimilarityRepository:
    return SimilarityRepository(session, _cipher())


async def _owned_document(scope: Scope, document_id: uuid.UUID) -> DocumentMeta:
    document = await UploadedDocumentRepository(scope.session, _cipher()).get_meta(
        document_id,
        organization_id=scope.ids.organization_id,
        user_id=scope.ids.user_id,
    )
    if document is None:
        raise _missing()
    return document


@router.get("/similarity-config")
async def similarity_config(_: IdentityDep) -> dict[str, Any]:
    return {
        "enabled": settings.SIMILARITY_CHECKS_ENABLED,
        "open_sources_enabled": bool(
            settings.SIMILARITY_CHECKS_ENABLED
            and settings.SIMILARITY_OPEN_SOURCES_ENABLED
            and service.terms_verified()
        ),
        "terms_verified_on": settings.PROVIDER_TERMS_VERIFIED_ON or None,
        "version": service.SIMILARITY_CONSENT_TEXT_VERSION,
        "sha256": service.consent_sha256(),
        "text": service.SIMILARITY_CONSENT_TEXT,
        "recipients": list(CONSENT_RECIPIENTS),
        "query_preview_rule": service.QUERY_PREVIEW_RULE,
        "per_user_daily": service.SIMILARITY_PER_USER_DAILY,
        "max_comparison_documents": service.MAX_COMPARISON_IDS,
        "max_own_uploads": service.MAX_OWN_UPLOADS,
    }


async def _check_comparison_documents(
    scope: Scope, request: SimilarityRequest, own_id: uuid.UUID
) -> None:
    repository = UploadedDocumentRepository(scope.session, _cipher())
    chars = 0
    if own_id in request.comparison_document_ids:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "comparison_includes_the_document"
        )
    for document_id in dict.fromkeys(request.comparison_document_ids):
        meta = await repository.get_meta(
            document_id,
            organization_id=scope.ids.organization_id,
            user_id=scope.ids.user_id,
        )
        if meta is None:
            raise _missing()
        chars += meta.char_count
    if chars > service.CANDIDATE_CHAR_BUDGET:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "candidate_budget_exceeded")


async def _check_open_sources(
    scope: Scope, body: OpenSourcesBody, document_id: uuid.UUID
) -> None:
    if not settings.SIMILARITY_OPEN_SOURCES_ENABLED:
        raise _error(status.HTTP_409_CONFLICT, "open_sources_disabled")
    if not service.terms_verified():
        raise _error(status.HTTP_409_CONFLICT, "provider_terms_unverified")
    if body.consent is None:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "consent_required")
    if (
        set(body.consent.recipients) != set(CONSENT_RECIPIENTS)
        or body.consent.consent_text_version != service.SIMILARITY_CONSENT_TEXT_VERSION
    ):
        document = await UploadedDocumentRepository(scope.session, _cipher()).get_text(
            document_id,
            organization_id=scope.ids.organization_id,
            user_id=scope.ids.user_id,
        )
        raise _error(
            status.HTTP_409_CONFLICT,
            "consent_mismatch",
            {
                "recipients": list(CONSENT_RECIPIENTS),
                "consent_text_version": service.SIMILARITY_CONSENT_TEXT_VERSION,
                "query_preview": await asyncio.to_thread(
                    service.query_preview, document.text if document else ""
                ),
            },
        )


@contextmanager
def _reserve_worker_slot(state: Any) -> Iterator[None]:
    """Count a start as load from the check until its task is registered.

    Between the check and ``document_jobs.add`` the handler awaits, so concurrent
    starts would otherwise all pass the same check.
    """
    running = sum(t.get_name().startswith("similarity:") for t in state.document_jobs)
    reserved = state.similarity_reserved
    if running + reserved >= MAX_QUEUED_PER_WORKER:
        raise _error(status.HTTP_429_TOO_MANY_REQUESTS, "worker_busy")
    state.similarity_reserved = reserved + 1
    try:
        yield
    finally:
        state.similarity_reserved -= 1


async def _admit(
    scope: Scope, *, open_sources: bool
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """Quota, only through the shared guard; a refusal raises so nothing is written."""
    ids = scope.ids
    try:
        ledger_id = await admit(
            scope.session,
            kind="similarity_check",
            organization_id=ids.organization_id,
            user_id=ids.user_id,
            units=1,
            limits=service.SIMILARITY_LIMITS,
            count_active=active_similarity_checks,
        )
        open_sources_id = None
        if open_sources:
            open_sources_id = await admit(
                scope.session,
                kind="similarity_open_sources",
                organization_id=ids.organization_id,
                user_id=ids.user_id,
                units=1,
                limits=service.OPEN_SOURCE_LIMITS,
                count_active=active_similarity_checks,
            )
    except QuotaExceeded as exc:
        raise _error(status.HTTP_429_TOO_MANY_REQUESTS, exc.code) from None
    return ledger_id, open_sources_id


@router.post(
    "/documents/{document_id}/similarity-checks",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CheckAccepted,
    dependencies=[Depends(_enabled)],
)
async def start_check(
    document_id: uuid.UUID,
    body: SimilarityRequest,
    scope: ScopeDep,
    runner: RunnerDep,
) -> CheckAccepted:
    await _owned_document(scope, document_id)
    await _check_comparison_documents(scope, body, document_id)
    consented = (
        body.open_sources if body.open_sources and body.open_sources.enabled else None
    )
    if consented:
        await _check_open_sources(scope, consented, document_id)
    with _reserve_worker_slot(runner.state):
        return await _schedule(scope, runner, document_id, body, consented is not None)


async def _schedule(
    scope: Scope,
    runner: Runner,
    document_id: uuid.UUID,
    body: SimilarityRequest,
    open_sources: bool,
) -> CheckAccepted:
    ids = scope.ids
    ledger_id, open_sources_ledger_id = await _admit(scope, open_sources=open_sources)
    options = {
        "own_uploads": body.own_uploads,
        "comparison_document_ids": [
            str(i) for i in dict.fromkeys(body.comparison_document_ids)
        ],
        "open_sources": open_sources,
        "semantic": body.semantic,
    }
    new_check = NewCheck(
        document_id=document_id,
        organization_id=ids.organization_id,
        user_id=ids.user_id,
        options=options,
        policy_versions=service.policy_versions(),
        consent_text_version=(
            service.SIMILARITY_CONSENT_TEXT_VERSION if open_sources else None
        ),
        consent_text_sha256=service.consent_sha256() if open_sources else None,
        ledger_id=ledger_id,
        open_sources_ledger_id=open_sources_ledger_id,
    )
    try:
        row = await _repository(scope.session).create(new_check)
    except IntegrityError:
        # The document was deleted after the ownership check; nothing was committed.
        raise _missing() from None
    job = service.CheckJob(
        row.id, document_id, ids.organization_id, ids.user_id, options
    )
    created_at = row.created_at

    def schedule() -> None:
        task = asyncio.create_task(
            run_job(
                table=SIMILARITY_CHECKS,
                job_id=job.check_id,
                organization_id=ids.organization_id,
                created_at=created_at,
                deadline_s=SIMILARITY_DEADLINE_S,
                session_factory=runner.session_factory,
                semaphore=runner.state.similarity_semaphore,
                work=service.make_work(
                    job,
                    session_factory=runner.session_factory,
                    retrieve=runner.retrieve,
                ),
                persist=service.make_persist(
                    check_id=job.check_id,
                    organization_id=ids.organization_id,
                    user_id=ids.user_id,
                ),
            ),
            name=f"similarity:{job.check_id}",
        )
        runner.state.document_jobs.add(task)
        task.add_done_callback(runner.state.document_jobs.discard)

    # The task must find the committed row, so it is scheduled when the commit finishes: a
    # callback on the shielded commit, not code after the await. A client that disconnects
    # mid-commit cancels this handler, but the commit still completes, and a committed row
    # is never left without a task.
    commit = asyncio.ensure_future(scope.session.commit())
    commit.add_done_callback(
        lambda done: None if done.cancelled() or done.exception() else schedule()
    )
    await asyncio.shield(commit)
    return CheckAccepted(id=job.check_id, status="queued")


@router.get(
    "/documents/{document_id}/similarity-checks",
    response_model=list[CheckSummaryOut],
    dependencies=[Depends(_enabled)],
)
async def list_checks(
    document_id: uuid.UUID,
    scope: ScopeDep,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST)] = MAX_LIST,
) -> list[CheckSummaryOut]:
    await _owned_document(scope, document_id)
    rows = await _repository(scope.session).list_for_document(
        document_id,
        organization_id=scope.ids.organization_id,
        user_id=scope.ids.user_id,
        limit=limit,
    )
    return [CheckSummaryOut(**CheckSummaryOut.summary_of(v)) for v in rows]


async def _owned_check(
    check_id: uuid.UUID,
    scope: Scope,
    *,
    offset: int = 0,
    limit: int = MAX_MATCH_PAGE,
) -> CheckView:
    """A check is readable only while its document is: an expired one reads as missing."""
    view = await _repository(scope.session).get(
        check_id,
        organization_id=scope.ids.organization_id,
        user_id=scope.ids.user_id,
        offset=offset,
        limit=limit,
    )
    if view is None:
        raise _missing()
    await _owned_document(scope, view.document_id)
    return view


@router.get(
    "/similarity-checks/{check_id}",
    response_model=CheckOut,
    dependencies=[Depends(_enabled)],
)
async def get_check(
    check_id: uuid.UUID,
    scope: ScopeDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_MATCH_PAGE)] = MAX_MATCH_PAGE,
) -> CheckOut:
    return CheckOut.of(await _owned_check(check_id, scope, offset=offset, limit=limit))


@router.get("/similarity-checks/{check_id}/report", dependencies=[Depends(_enabled)])
async def get_report(check_id: uuid.UUID, scope: ScopeDep) -> Response:
    view = await _owned_check(check_id, scope, limit=MAX_REPORT_MATCHES)
    if view.status in ACTIVE_STATUSES:
        raise _error(status.HTTP_409_CONFLICT, "not_finished")
    document = await UploadedDocumentRepository(scope.session, _cipher()).get_text(
        view.document_id,
        organization_id=scope.ids.organization_id,
        user_id=scope.ids.user_id,
    )
    if document is None:
        raise _missing()
    return Response(
        await asyncio.to_thread(render_report, view, document.text),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="similarity-{check_id}.txt"',
            "Cache-Control": NO_STORE,
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete(
    "/similarity-checks/{check_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_enabled)],
)
async def delete_check(
    check_id: uuid.UUID, request: Request, scope: ScopeDep
) -> Response:
    if not await _repository(scope.session).delete(
        check_id,
        organization_id=scope.ids.organization_id,
        user_id=scope.ids.user_id,
    ):
        raise _missing()
    await scope.session.commit()
    # Stop the work too; its quota stays spent. Only this worker's tasks are visible
    # here, so a run on another worker finishes and discards its result.
    running = {
        task
        for task in request.app.state.document_jobs
        if task.get_name() == f"similarity:{check_id}"
    }
    await cancel_all(running)
    return Response(
        status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": NO_STORE}
    )
