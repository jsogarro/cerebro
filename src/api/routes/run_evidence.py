"""REST route for tenant-scoped run evidence metadata."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.services.evidence_service import (
    DEFAULT_EVIDENCE_LIMIT,
    MAX_EVIDENCE_LIMIT,
    InvalidEvidenceCursorError,
    RunEvidencePage,
    RunNotFoundError,
    list_run_evidence,
)
from src.middleware.auth_middleware import get_current_token
from src.middleware.tenant_context import TenantContext, get_tenant_context
from src.models.db.session import SessionDep

router = APIRouter(dependencies=[Depends(get_current_token)])


@router.get("/runs/{run_id}/evidence", response_model=RunEvidencePage)
async def get_run_evidence(
    run_id: str,
    cursor: str | None = Query(
        None, description="Opaque keyset cursor returned by a previous page"
    ),
    limit: int = Query(
        DEFAULT_EVIDENCE_LIMIT,
        ge=1,
        le=MAX_EVIDENCE_LIMIT,
        description="Maximum evidence rows to return (default 100, cap 500)",
    ),
    tenant_context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = SessionDep,
) -> RunEvidencePage:
    """Return metadata-only evidence rows for a run owned by the caller's tenant."""
    try:
        return await list_run_evidence(
            session,
            run_id,
            organization_id=tenant_context.organization_id,
            cursor=cursor,
            limit=limit,
        )
    except RunNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except InvalidEvidenceCursorError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


__all__ = ["router"]
