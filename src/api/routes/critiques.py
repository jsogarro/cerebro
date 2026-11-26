"""Start, read, report on and delete critiques of the caller's own documents.

Nothing here accepts document text: a request body carries consent fields only,
so the 422 handler's echo of the input cannot repeat a manuscript. Every read
is owner-scoped and a miss is always 404. The report is served as plain text.
"""

import asyncio
import uuid
from collections.abc import Callable
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.critique.model import ReviewModel
from src.core.config import settings
from src.core.memory_encryption import DocumentEncryptionUnavailable, document_cipher
from src.middleware.tenant_context import (
    TenantContext,
    get_tenant_context,
    get_tenant_identity,
    owner_ids,
)
from src.models.db.document_critique import DOCUMENT_CRITIQUES
from src.models.db.session import get_session, get_session_factory
from src.repositories.critique_repository import (
    CritiqueRepository,
    CritiqueView,
    FindingView,
)
from src.repositories.uploaded_document_repository import UploadedDocumentRepository
from src.services import critique_service as service
from src.services.document_jobs import QuotaExceeded, admit, run_job

router = APIRouter(tags=["critiques"])

ModelFactory = Callable[[], ReviewModel]


class CritiqueRequest(BaseModel):
    model_id: str = Field(max_length=100)
    hosts: list[str] = Field(max_length=5)
    consent_text_version: str = Field(max_length=50)
    acknowledged: bool


class FindingOut(BaseModel):
    role: str
    category: str
    severity: str
    quote: str
    raw_start: int
    raw_end: int
    page: int
    match_kind: str
    problem: str
    fix: str
    second_quote: str | None
    second_raw_start: int | None
    second_raw_end: int | None
    second_page: int | None

    @classmethod
    def of(cls, finding: FindingView) -> "FindingOut":
        return cls(**vars(finding))


class CritiqueOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    status: str
    status_reason: str | None
    model_id: str
    temperature: float
    role_outcomes: dict[str, dict[str, Any]]
    created_at: str
    started_at: str | None
    completed_at: str | None
    findings: list[FindingOut]

    @classmethod
    def of(cls, view: CritiqueView) -> "CritiqueOut":
        return cls(
            id=view.id,
            document_id=view.document_id,
            status=view.status,
            status_reason=view.status_reason,
            model_id=view.model_id,
            temperature=view.temperature,
            role_outcomes=view.role_outcomes,
            created_at=view.created_at.isoformat(),
            started_at=view.started_at.isoformat() if view.started_at else None,
            completed_at=view.completed_at.isoformat() if view.completed_at else None,
            findings=[FindingOut.of(f) for f in view.findings],
        )


class CritiqueAccepted(BaseModel):
    id: uuid.UUID
    status: str


def get_review_model() -> ModelFactory:
    """A factory, not a model, so the checks that must precede provider setup run first."""
    return service.build_review_model


def get_job_session_factory() -> Callable[[], AsyncSession]:
    return get_session_factory()


def _error(code: int, name: str, details: Any = None) -> HTTPException:
    detail: dict[str, Any] = {"code": name}
    if details is not None:
        detail["details"] = details
    return HTTPException(status_code=code, detail=detail)


def _require_enabled() -> None:
    if not settings.DOCUMENT_CRITIQUE_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


def _terms_verified() -> bool:
    try:
        date.fromisoformat(settings.PROVIDER_TERMS_VERIFIED_ON)
    except ValueError:
        return False
    return True


def _repository(session: AsyncSession) -> CritiqueRepository:
    try:
        return CritiqueRepository(session, document_cipher())
    except DocumentEncryptionUnavailable:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "document_storage_unavailable"
        ) from None


@router.get("/critique-config")
async def critique_config(
    _: TenantContext = Depends(get_tenant_identity),
) -> dict[str, Any]:
    return {
        "enabled": bool(
            settings.DOCUMENT_CRITIQUE_ENABLED
            and _terms_verified()
            and settings.GEMINI_API_KEY
        ),
        "terms_verified_on": settings.PROVIDER_TERMS_VERIFIED_ON or None,
        "provider": service.PROVIDER,
        "model_id": service.CRITIQUE_MODEL_ID,
        "temperature": service.CRITIQUE_TEMPERATURE,
        "hosts": service.PROVIDER_HOSTS,
        "max_chars": service.CRITIQUE_MAX_CHARS,
        "calls_per_run_max": service.CALLS_PER_RUN_MAX,
        "per_user_daily": service.CRITIQUE_PER_USER_DAILY,
        "consents": [
            {"version": c.version, "sha256": c.sha256, "text": c.text}
            for c in service.CONSENTS
        ],
    }


@router.post(
    "/documents/{document_id}/critiques",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CritiqueAccepted,
)
async def start_critique(
    document_id: uuid.UUID,
    body: CritiqueRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    tenant: TenantContext = Depends(get_tenant_context),
    model_factory: ModelFactory = Depends(get_review_model),
    session_factory: Callable[[], AsyncSession] = Depends(get_job_session_factory),
) -> CritiqueAccepted:
    _require_enabled()
    ids = owner_ids(tenant)
    document = await UploadedDocumentRepository(session, document_cipher()).get_meta(
        document_id, organization_id=ids.organization_id, user_id=ids.user_id
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if not _terms_verified():
        raise _error(status.HTTP_409_CONFLICT, "provider_terms_unverified")
    if not settings.GEMINI_API_KEY:
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE, "provider_not_configured")
    consent = next(
        (c for c in service.CONSENTS if c.version == body.consent_text_version), None
    )
    if (
        consent is None
        or body.model_id != service.CRITIQUE_MODEL_ID
        or body.hosts != service.PROVIDER_HOSTS
    ):
        raise _error(
            status.HTTP_409_CONFLICT,
            "consent_mismatch",
            {
                "model_id": service.CRITIQUE_MODEL_ID,
                "hosts": service.PROVIDER_HOSTS,
                "consent_text_versions": [c.version for c in service.CONSENTS],
            },
        )
    if body.acknowledged is not True:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "consent_not_acknowledged")
    if document.char_count > service.CRITIQUE_MAX_CHARS:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "too_long_for_critique")

    # Built before anything is reserved, so a setup failure leaves no row and no spent quota.
    model = model_factory()
    units = service.reserved_units(document.char_count)
    try:
        await admit(
            session,
            kind="critique",
            organization_id=ids.organization_id,
            user_id=ids.user_id,
            units=units,
            limits=service.CRITIQUE_LIMITS,
            count_active=service.active_model_jobs,
            lock=service.MODEL_ADMIT_LOCK,
            budget_kinds=service.MODEL_JOB_KINDS,
        )
    except QuotaExceeded as exc:
        raise _error(status.HTTP_429_TOO_MANY_REQUESTS, exc.code) from None
    row = await _repository(session).create(
        document_id=document_id,
        organization_id=ids.organization_id,
        user_id=ids.user_id,
        model_id=service.CRITIQUE_MODEL_ID,
        temperature=service.CRITIQUE_TEMPERATURE,
        prompt_versions=service.prompt_versions(),
        consent_text_version=consent.version,
        consent_text_sha256=consent.sha256,
        reserved_units=units,
    )
    critique_id, created_at = row.id, row.created_at
    # The task must find the committed row, so commit before scheduling it.
    await session.commit()

    state = request.app.state
    jobs: set[asyncio.Task[None]] | None = getattr(state, "document_jobs", None)
    if jobs is None:
        jobs = state.document_jobs = set()
    task = asyncio.create_task(
        run_job(
            table=DOCUMENT_CRITIQUES,
            job_id=critique_id,
            organization_id=ids.organization_id,
            created_at=created_at,
            deadline_s=service.CRITIQUE_DEADLINE_S,
            session_factory=session_factory,
            semaphore=getattr(state, "critique_semaphore", None)
            or asyncio.Semaphore(1),
            work=service.make_work(
                session_factory=session_factory,
                document_id=document_id,
                organization_id=ids.organization_id,
                user_id=ids.user_id,
                model=model,
            ),
            persist=service.make_persist(
                critique_id=critique_id,
                organization_id=ids.organization_id,
                user_id=ids.user_id,
            ),
        )
    )
    jobs.add(task)
    task.add_done_callback(jobs.discard)
    return CritiqueAccepted(id=critique_id, status="queued")


@router.get("/documents/{document_id}/critiques", response_model=list[CritiqueOut])
async def list_critiques(
    document_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
    session: AsyncSession = Depends(get_session),
    tenant: TenantContext = Depends(get_tenant_context),
) -> list[CritiqueOut]:
    _require_enabled()
    ids = owner_ids(tenant)
    document = await UploadedDocumentRepository(session, document_cipher()).get_meta(
        document_id, organization_id=ids.organization_id, user_id=ids.user_id
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    rows = await _repository(session).list_for_document(
        document_id,
        organization_id=ids.organization_id,
        user_id=ids.user_id,
        limit=limit,
    )
    return [CritiqueOut.of(view) for view in rows]


async def _owned_critique(
    critique_id: uuid.UUID, session: AsyncSession, tenant: TenantContext
) -> CritiqueView:
    _require_enabled()
    ids = owner_ids(tenant)
    view = await _repository(session).get(
        critique_id, organization_id=ids.organization_id, user_id=ids.user_id
    )
    if view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return view


@router.get("/critiques/{critique_id}", response_model=CritiqueOut)
async def get_critique(
    critique_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: TenantContext = Depends(get_tenant_context),
) -> CritiqueOut:
    return CritiqueOut.of(await _owned_critique(critique_id, session, tenant))


@router.get("/critiques/{critique_id}/report")
async def get_report(
    critique_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: TenantContext = Depends(get_tenant_context),
) -> Response:
    view = await _owned_critique(critique_id, session, tenant)
    if view.status in ("queued", "running"):
        raise _error(status.HTTP_409_CONFLICT, "not_finished")
    ids = owner_ids(tenant)
    document = await UploadedDocumentRepository(session, document_cipher()).get_meta(
        view.document_id, organization_id=ids.organization_id, user_id=ids.user_id
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return Response(
        service.render_report(view, document.text_sha256),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="critique-{critique_id}.txt"'
        },
    )


@router.delete("/critiques/{critique_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_critique(
    critique_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: TenantContext = Depends(get_tenant_context),
) -> Response:
    _require_enabled()
    ids = owner_ids(tenant)
    if not await _repository(session).delete(
        critique_id, organization_id=ids.organization_id, user_id=ids.user_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
