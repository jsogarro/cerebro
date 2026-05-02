"""Tenant context dependencies for organization-scoped requests."""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_current_token
from src.models.db.session import get_session


@dataclass(frozen=True)
class TenantContext:
    """Authenticated tenant context extracted from JWT claims."""

    user_id: str
    organization_id: str


def require_organization_id(token_payload: TokenPayload) -> str:
    """Return organization_id from a token payload or fail closed."""
    if not token_payload.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant organization claim is required",
        )

    return token_payload.organization_id


async def set_postgres_tenant_context(
    session: AsyncSession,
    organization_id: str,
) -> None:
    """Set the transaction-local tenant context used by Postgres RLS policies."""
    await session.execute(
        text("SET LOCAL app.current_org_id = :organization_id"),
        {"organization_id": organization_id},
    )


async def get_tenant_context(
    token_payload: TokenPayload = Depends(get_current_token),
    session: AsyncSession = Depends(get_session),
) -> TenantContext:
    """Resolve authenticated tenant context and set DB session context."""
    organization_id = require_organization_id(token_payload)
    await set_postgres_tenant_context(session, organization_id)
    return TenantContext(
        user_id=token_payload.sub,
        organization_id=organization_id,
    )
