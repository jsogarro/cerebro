"""Start, read, report on and delete critiques of the caller's own documents.

Nothing here accepts document text: a request body carries consent fields only,
so the 422 handler's echo of the input cannot repeat a manuscript. Every read
is owner-scoped and a miss is always 404. The report is served as plain text.
"""

import asyncio
import contextlib
import uuid
from collections.abc import Callable, Iterator
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, StrictBool
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.critique.model import ReviewModel
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
from src.models.db.document_critique import DOCUMENT_CRITIQUES
from src.models.db.job_constants import ACTIVE_STATUSES
from src.models.db.session import get_session, get_session_factory
from src.repositories.critique_repository import (
    CRITIQUE_DEADLINE_S,
    CritiqueRepository,
    CritiqueView,
    FindingView,
    active_model_jobs,
)
from src.repositories.uploaded_document_repository import (
    DocumentMeta,
    UploadedDocumentRepository,
)
from src.services import critique_service as service
from src.services.document_jobs import (
    MODEL_ADMIT_LOCK,
    MODEL_JOB_KINDS,
    QuotaExceeded,
    admit,
    cancel_all,
    run_job,
)

router = APIRouter(tags=["critiques"])

ModelFactory = Callable[[], ReviewModel]
NO_STORE = "no-store"
# Queued tasks each hold a built model and wait on one slot per worker.
MAX_QUEUED_PER_WORKER = 4


class CritiqueRequest(BaseModel):
    model_id: str = Field(max_length=100)
    hosts: list[Annotated[str, Field(max_length=253)]] = Field(max_length=5)
    consent_text_version: str = Field(max_length=50)
    acknowledged: StrictBool


class FindingOut(BaseModel):
    role: str
    category: str
    severity: str
    quote: str = Field(
        description=(
            "Untrusted document text, verbatim: render it as plain text, never as "
            "markdown or HTML."
        )
    )
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


class CritiqueSummaryOut(BaseModel):
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

    @classmethod
    def summary_of(cls, view: CritiqueView) -> dict[str, Any]:
        return {
            "id": view.id,
            "document_id": view.document_id,
            "status": view.status,
            "status_reason": view.status_reason,
            "model_id": view.model_id,
            "temperature": view.temperature,
            "role_outcomes": view.role_outcomes,
            "created_at": view.created_at.isoformat(),
            "started_at": view.started_at.isoformat() if view.started_at else None,
            "completed_at": view.completed_at.isoformat()
            if view.completed_at
            else None,
        }


class CritiqueOut(CritiqueSummaryOut):
    findings: list[FindingOut]

    @classmethod
    def of(cls, view: CritiqueView) -> "CritiqueOut":
        return cls(
            **cls.summary_of(view),
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


async def _enabled(
    response: Response, _: TenantContext = Depends(get_tenant_context)
) -> None:
    """Runs after authentication and before the body is parsed, so a dark feature is a 404."""
    if not settings.DOCUMENT_CRITIQUE_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    response.headers["Cache-Control"] = NO_STORE


def _terms_verified() -> bool:
    try:
        return date.fromisoformat(settings.PROVIDER_TERMS_VERIFIED_ON) <= date.today()
    except ValueError:
        return False


def _cipher() -> MemoryEncryption:
    try:
        return document_cipher()
    except DocumentEncryptionUnavailable:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, "document_storage_unavailable"
        ) from None


def _repository(session: AsyncSession) -> CritiqueRepository:
    return CritiqueRepository(session, _cipher())


async def _owned_document(
    session: AsyncSession, document_id: uuid.UUID, ids: OwnerIds
) -> DocumentMeta:
    document = await UploadedDocumentRepository(session, _cipher()).get_meta(
        document_id, organization_id=ids.organization_id, user_id=ids.user_id
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return document


@contextlib.contextmanager
def _queue_slot(state: Any) -> Iterator[None]:
    """Reserve one of this worker's queue places before any await, so concurrent requests
    cannot all pass the check; released once the task is in the job set (or on failure)."""
    if len(state.document_jobs) + state.critique_reserved >= MAX_QUEUED_PER_WORKER:
        raise _error(status.HTTP_429_TOO_MANY_REQUESTS, "worker_busy")
    state.critique_reserved += 1
    try:
        yield
    finally:
        state.critique_reserved -= 1


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
    dependencies=[Depends(_enabled)],
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
    ids = owner_ids(tenant)
    document = await _owned_document(session, document_id, ids)
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
    if not body.acknowledged:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "consent_not_acknowledged")
    if document.char_count > service.CRITIQUE_MAX_CHARS:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "too_long_for_critique")

    state = request.app.state
    with _queue_slot(state):
        # Built before anything is reserved, so a setup failure leaves no row and no spent quota.
        model = model_factory()
        units = service.reserved_units(document.char_count)
        try:
            ledger_id = await admit(
                session,
                kind="critique",
                organization_id=ids.organization_id,
                user_id=ids.user_id,
                units=units,
                limits=service.CRITIQUE_LIMITS,
                count_active=active_model_jobs,
                lock=MODEL_ADMIT_LOCK,
                budget_kinds=MODEL_JOB_KINDS,
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
            ledger_id=ledger_id,
        )
        critique_id, created_at = row.id, row.created_at

        def schedule() -> None:
            task = asyncio.create_task(
                run_job(
                    table=DOCUMENT_CRITIQUES,
                    job_id=critique_id,
                    organization_id=ids.organization_id,
                    created_at=created_at,
                    deadline_s=CRITIQUE_DEADLINE_S,
                    session_factory=session_factory,
                    semaphore=state.critique_semaphore,
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
                ),
                name=f"critique:{critique_id}",
            )
            state.document_jobs.add(task)
            task.add_done_callback(state.document_jobs.discard)

        # The task must find the committed row, so it is scheduled when the commit finishes.
        # That is a callback on the shielded commit, not code after the await: a client that
        # disconnects mid-commit cancels this handler (repeatedly, under anyio), but the
        # commit still completes and a committed row is never left without a task.
        commit = asyncio.ensure_future(session.commit())
        commit.add_done_callback(
            lambda done: None if done.cancelled() or done.exception() else schedule()
        )
        await asyncio.shield(commit)
    return CritiqueAccepted(id=critique_id, status="queued")


@router.get(
    "/documents/{document_id}/critiques",
    response_model=list[CritiqueSummaryOut],
    dependencies=[Depends(_enabled)],
)
async def list_critiques(
    document_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
    session: AsyncSession = Depends(get_session),
    tenant: TenantContext = Depends(get_tenant_context),
) -> list[CritiqueSummaryOut]:
    ids = owner_ids(tenant)
    await _owned_document(session, document_id, ids)
    rows = await _repository(session).list_for_document(
        document_id,
        organization_id=ids.organization_id,
        user_id=ids.user_id,
        limit=limit,
    )
    return [CritiqueSummaryOut(**CritiqueSummaryOut.summary_of(v)) for v in rows]


async def _owned_critique(
    critique_id: uuid.UUID, session: AsyncSession, tenant: TenantContext
) -> tuple[CritiqueView, DocumentMeta]:
    """A critique is readable only while its document is: an expired one reads as missing."""
    ids = owner_ids(tenant)
    view = await _repository(session).get(
        critique_id, organization_id=ids.organization_id, user_id=ids.user_id
    )
    if view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return view, await _owned_document(session, view.document_id, ids)


@router.get(
    "/critiques/{critique_id}",
    response_model=CritiqueOut,
    dependencies=[Depends(_enabled)],
)
async def get_critique(
    critique_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: TenantContext = Depends(get_tenant_context),
) -> CritiqueOut:
    view, _ = await _owned_critique(critique_id, session, tenant)
    return CritiqueOut.of(view)


@router.get("/critiques/{critique_id}/report", dependencies=[Depends(_enabled)])
async def get_report(
    critique_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: TenantContext = Depends(get_tenant_context),
) -> Response:
    view, document = await _owned_critique(critique_id, session, tenant)
    if view.status in ACTIVE_STATUSES:
        raise _error(status.HTTP_409_CONFLICT, "not_finished")
    return Response(
        service.render_report(view, document.text_sha256),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="critique-{critique_id}.txt"',
            "Cache-Control": NO_STORE,
        },
    )


@router.delete(
    "/critiques/{critique_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_enabled)],
)
async def delete_critique(
    critique_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    tenant: TenantContext = Depends(get_tenant_context),
) -> Response:
    ids = owner_ids(tenant)
    if not await _repository(session).delete(
        critique_id, organization_id=ids.organization_id, user_id=ids.user_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await session.commit()
    # Stop the model call too; its quota stays spent.
    # ponytail: a delete and re-create that lands on another worker briefly frees an org
    # slot while the old run still calls the provider; the daily ledger caps still hold. Only this worker's tasks are
    # visible here, so a run on another worker finishes and discards its result.
    running = {
        task
        for task in request.app.state.document_jobs
        if task.get_name() == f"critique:{critique_id}"
    }
    await cancel_all(running)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
