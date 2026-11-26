"""Start, read, report on and delete similarity checks of the caller's own documents.

Nothing here accepts document text: a request body carries ids, booleans and the
consent block only, so the 422 handler's echo of the input cannot repeat a
manuscript. Every read is owner-scoped and a miss is always 404. The report is
served as plain text.
"""

import asyncio
import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool
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
from src.models.db.session import get_session, get_session_factory
from src.models.db.similarity_check import SIMILARITY_CHECKS
from src.repositories.similarity_repository import (
    MAX_LIST,
    MAX_MATCH_PAGE,
    SIMILARITY_DEADLINE_S,
    CheckView,
    MatchView,
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
# Queued tasks each wait on one slot per worker.
MAX_QUEUED_PER_WORKER = 4


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
    return HTTPException(status_code=code, detail=detail)


async def _enabled(
    response: Response, _: TenantContext = Depends(get_tenant_context)
) -> None:
    """Runs after authentication and before the body is parsed, so a dark feature is a 404."""
    if not settings.SIMILARITY_CHECKS_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
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


async def _owned_document(
    session: AsyncSession, document_id: uuid.UUID, ids: OwnerIds
) -> DocumentMeta:
    document = await UploadedDocumentRepository(session, _cipher()).get_meta(
        document_id, organization_id=ids.organization_id, user_id=ids.user_id
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return document


@router.get("/similarity-config")
async def similarity_config(
    _: TenantContext = Depends(get_tenant_identity),
) -> dict[str, Any]:
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
    session: AsyncSession, request: SimilarityRequest, ids: OwnerIds
) -> None:
    repository = UploadedDocumentRepository(session, _cipher())
    chars = 0
    for document_id in dict.fromkeys(request.comparison_document_ids):
        meta = await repository.get_meta(
            document_id, organization_id=ids.organization_id, user_id=ids.user_id
        )
        if meta is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        chars += meta.char_count
    if chars > service.CANDIDATE_CHAR_BUDGET:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "candidate_budget_exceeded")


async def _check_open_sources(
    session: AsyncSession, body: OpenSourcesBody, document_id: uuid.UUID, ids: OwnerIds
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
        document = await UploadedDocumentRepository(session, _cipher()).get_text(
            document_id, organization_id=ids.organization_id, user_id=ids.user_id
        )
        raise _error(
            status.HTTP_409_CONFLICT,
            "consent_mismatch",
            {
                "recipients": list(CONSENT_RECIPIENTS),
                "consent_text_version": service.SIMILARITY_CONSENT_TEXT_VERSION,
                "query_preview": service.query_preview(
                    document.text if document else ""
                ),
            },
        )


@router.post(
    "/documents/{document_id}/similarity-checks",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CheckAccepted,
    dependencies=[Depends(_enabled)],
)
async def start_check(
    document_id: uuid.UUID,
    body: SimilarityRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    tenant: TenantContext = Depends(get_tenant_context),
    retrieve: service.Retriever = Depends(get_retriever),
    session_factory: Callable[[], AsyncSession] = Depends(get_job_session_factory),
) -> CheckAccepted:
    ids = owner_ids(tenant)
    await _owned_document(session, document_id, ids)
    await _check_comparison_documents(session, body, ids)
    consented = (
        body.open_sources if body.open_sources and body.open_sources.enabled else None
    )
    if consented:
        await _check_open_sources(session, consented, document_id, ids)

    state = request.app.state
    if len(state.document_jobs) >= MAX_QUEUED_PER_WORKER:
        raise _error(status.HTTP_429_TOO_MANY_REQUESTS, "worker_busy")
    try:
        ledger_id = await admit(
            session,
            kind="similarity_check",
            organization_id=ids.organization_id,
            user_id=ids.user_id,
            units=1,
            limits=service.SIMILARITY_LIMITS,
            count_active=active_similarity_checks,
        )
        if consented:
            await admit(
                session,
                kind="similarity_open_sources",
                organization_id=ids.organization_id,
                user_id=ids.user_id,
                units=1,
                limits=service.OPEN_SOURCE_LIMITS,
                count_active=active_similarity_checks,
            )
    except QuotaExceeded as exc:
        raise _error(status.HTTP_429_TOO_MANY_REQUESTS, exc.code) from None
    options = {
        "own_uploads": body.own_uploads,
        "comparison_document_ids": [
            str(i) for i in dict.fromkeys(body.comparison_document_ids)
        ],
        "open_sources": consented is not None,
        "semantic": body.semantic,
    }
    row = await _repository(session).create(
        document_id=document_id,
        organization_id=ids.organization_id,
        user_id=ids.user_id,
        options=options,
        policy_versions=service.policy_versions(),
        consent_text_version=(
            service.SIMILARITY_CONSENT_TEXT_VERSION if consented else None
        ),
        consent_text_sha256=service.consent_sha256() if consented else None,
        ledger_id=ledger_id,
    )
    check_id, created_at = row.id, row.created_at
    # The task must find the committed row, so commit before scheduling it.
    await session.commit()

    task = asyncio.create_task(
        run_job(
            table=SIMILARITY_CHECKS,
            job_id=check_id,
            organization_id=ids.organization_id,
            created_at=created_at,
            deadline_s=SIMILARITY_DEADLINE_S,
            session_factory=session_factory,
            semaphore=state.similarity_semaphore,
            work=service.make_work(
                session_factory=session_factory,
                check_id=check_id,
                document_id=document_id,
                organization_id=ids.organization_id,
                user_id=ids.user_id,
                options=options,
                retrieve=retrieve,
            ),
            persist=service.make_persist(
                check_id=check_id,
                organization_id=ids.organization_id,
                user_id=ids.user_id,
            ),
        ),
        name=f"similarity:{check_id}",
    )
    state.document_jobs.add(task)
    task.add_done_callback(state.document_jobs.discard)
    return CheckAccepted(id=check_id, status="queued")


@router.get(
    "/documents/{document_id}/similarity-checks",
    response_model=list[CheckSummaryOut],
    dependencies=[Depends(_enabled)],
)
async def list_checks(
    document_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST)] = MAX_LIST,
    session: AsyncSession = Depends(get_session),
    tenant: TenantContext = Depends(get_tenant_context),
) -> list[CheckSummaryOut]:
    ids = owner_ids(tenant)
    await _owned_document(session, document_id, ids)
    rows = await _repository(session).list_for_document(
        document_id,
        organization_id=ids.organization_id,
        user_id=ids.user_id,
        limit=limit,
    )
    return [CheckSummaryOut(**CheckSummaryOut.summary_of(v)) for v in rows]


async def _owned_check(
    check_id: uuid.UUID,
    session: AsyncSession,
    tenant: TenantContext,
    *,
    offset: int = 0,
    limit: int = MAX_MATCH_PAGE,
) -> tuple[CheckView, DocumentMeta]:
    """A check is readable only while its document is: an expired one reads as missing."""
    ids = owner_ids(tenant)
    view = await _repository(session).get(
        check_id,
        organization_id=ids.organization_id,
        user_id=ids.user_id,
        offset=offset,
        limit=limit,
    )
    if view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return view, await _owned_document(session, view.document_id, ids)


@router.get(
    "/similarity-checks/{check_id}",
    response_model=CheckOut,
    dependencies=[Depends(_enabled)],
)
async def get_check(
    check_id: uuid.UUID,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_MATCH_PAGE)] = MAX_MATCH_PAGE,
    session: AsyncSession = Depends(get_session),
    tenant: TenantContext = Depends(get_tenant_context),
) -> CheckOut:
    view, _ = await _owned_check(check_id, session, tenant, offset=offset, limit=limit)
    return CheckOut.of(view)


@router.get("/similarity-checks/{check_id}/report", dependencies=[Depends(_enabled)])
async def get_report(
    check_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: TenantContext = Depends(get_tenant_context),
) -> Response:
    ids = owner_ids(tenant)
    view, _ = await _owned_check(check_id, session, tenant, limit=1000)
    if view.status in ACTIVE_STATUSES:
        raise _error(status.HTTP_409_CONFLICT, "not_finished")
    document = await UploadedDocumentRepository(session, _cipher()).get_text(
        view.document_id, organization_id=ids.organization_id, user_id=ids.user_id
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return Response(
        render_report(view, document.text),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="similarity-{check_id}.txt"',
            "Cache-Control": NO_STORE,
        },
    )


@router.delete(
    "/similarity-checks/{check_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_enabled)],
)
async def delete_check(
    check_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    tenant: TenantContext = Depends(get_tenant_context),
) -> Response:
    ids = owner_ids(tenant)
    if not await _repository(session).delete(
        check_id, organization_id=ids.organization_id, user_id=ids.user_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await session.commit()
    # Stop the work too; its quota stays spent. Only this worker's tasks are visible
    # here, so a run on another worker finishes and discards its result.
    running = {
        task
        for task in request.app.state.document_jobs
        if task.get_name() == f"similarity:{check_id}"
    }
    await cancel_all(running)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
