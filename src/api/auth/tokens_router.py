"""JWT-authenticated endpoints for creating, listing, and revoking PATs."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import (
    PersonalAccessTokenCreateRequest,
    PersonalAccessTokenCreateResponse,
    PersonalAccessTokenResponse,
    TokenPayload,
)
from src.auth.pat_service import generate_personal_access_token
from src.middleware.auth_middleware import get_current_token
from src.middleware.tenant_context import TenantContext, get_tenant_context, owner_ids
from src.models.db.audit_log import AuditEventType
from src.models.db.session import SessionDep
from src.repositories.personal_access_token_repository import (
    PersonalAccessTokenRepository,
)
from src.repositories.user_repository import UserRepository

router = APIRouter(prefix="/tokens", tags=["Authentication"])


async def _audit_pat_event(
    request: Request,
    *,
    event_type: AuditEventType,
    action: str,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    pat_id: uuid.UUID,
    metadata: dict[str, object] | None = None,
) -> None:
    audit_logger = getattr(request.app.state, "audit_logger", None)
    log_event = getattr(audit_logger, "log_event", None)
    if not callable(log_event):
        return
    details: dict[str, object] = {
        "pat_id": str(pat_id),
        "organization_id": str(organization_id),
    }
    if metadata:
        details.update(metadata)
    await log_event(
        event_type=event_type,
        action=action,
        request=request,
        user_id=user_id,
        resource_type="personal_access_token",
        resource_id=str(pat_id),
        result="success",
        metadata=details,
    )


@router.post(
    "",
    response_model=PersonalAccessTokenCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_personal_access_token(
    body: PersonalAccessTokenCreateRequest,
    request: Request,
    _token_payload: TokenPayload = Depends(get_current_token),
    tenant: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = SessionDep,
) -> PersonalAccessTokenCreateResponse:
    """Create a personal access token; plaintext is returned once."""
    ids = owner_ids(tenant)
    user = await UserRepository(db).get(str(ids.user_id))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled"
        )
    if user.organization_id is None or user.organization_id != ids.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant organization claim is required",
        )

    now = datetime.now(UTC)
    plaintext, digest, prefix = generate_personal_access_token()
    record = await PersonalAccessTokenRepository(db).create(
        user_id=ids.user_id,
        organization_id=ids.organization_id,
        name=body.name,
        token_hash=digest,
        token_prefix=prefix,
        scopes=body.scopes,
        issued_at_ns=time.time_ns(),
        created_at=now,
        expires_at=now + timedelta(days=body.expires_in_days),
    )
    await db.commit()

    await _audit_pat_event(
        request,
        event_type=AuditEventType.API_KEY_CREATED,
        action="pat.create",
        user_id=ids.user_id,
        organization_id=ids.organization_id,
        pat_id=record.id,
        metadata={
            "scopes": list(record.scopes),
            "expires_at": record.expires_at.isoformat(),
        },
    )

    return PersonalAccessTokenCreateResponse(
        id=record.id,
        name=record.name,
        token_prefix=record.token_prefix,
        scopes=list(record.scopes),
        expires_at=record.expires_at,
        revoked_at=record.revoked_at,
        last_used_at=record.last_used_at,
        created_at=record.created_at,
        token=plaintext,
    )


@router.get("", response_model=list[PersonalAccessTokenResponse])
async def list_personal_access_tokens(
    _token_payload: TokenPayload = Depends(get_current_token),
    tenant: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = SessionDep,
) -> list[PersonalAccessTokenResponse]:
    """List personal access token metadata for the authenticated user."""
    ids = owner_ids(tenant)
    rows = await PersonalAccessTokenRepository(db).list_for_user(
        ids.user_id, organization_id=ids.organization_id
    )
    return [PersonalAccessTokenResponse.model_validate(row) for row in rows]


@router.delete("/{pat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_personal_access_token(
    pat_id: uuid.UUID,
    request: Request,
    token_payload: TokenPayload = Depends(get_current_token),
    tenant: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = SessionDep,
) -> None:
    """Revoke a personal access token within the caller's organization."""
    ids = owner_ids(tenant)
    target_user_id = None if token_payload.has_role("admin") else ids.user_id
    revoked = await PersonalAccessTokenRepository(db).revoke(
        pat_id,
        organization_id=ids.organization_id,
        user_id=target_user_id,
    )
    if revoked is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Personal access token not found",
        )
    await db.commit()
    await _audit_pat_event(
        request,
        event_type=AuditEventType.API_KEY_REVOKED,
        action="pat.revoke",
        user_id=ids.user_id,
        organization_id=ids.organization_id,
        pat_id=revoked.id,
    )


__all__ = ["router"]
