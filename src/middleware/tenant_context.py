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
    """Set the transaction-local tenant context used by Postgres RLS policies.

    Written as ``set_config(..., is_local => true)`` rather than ``SET LOCAL``.
    The two are equivalent in effect — both scope the setting to the current
    transaction, and both are read back by the ``current_setting(
    'app.current_org_id', true)::uuid`` expression the RLS policies use — but
    ``SET`` is utility syntax that takes no bind parameters, so
    ``SET LOCAL app.current_org_id = :organization_id`` reaches Postgres as
    ``SET LOCAL app.current_org_id = $1`` and fails with a syntax error. The
    function form accepts the organization as a real parameter, which keeps
    the tenant identifier off the statement text.

    No-op on non-Postgres dialects. RLS policies are Postgres-only — SQLite
    test/dev databases don't enforce them, so silently skipping the statement
    lets local dev (``DATABASE_URL=sqlite+aiosqlite://``) exercise the same
    code paths. The tenant boundary is still enforced at the repository layer
    (``user_id`` and ``organization_id`` filters on every query).
    """
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return

    await session.execute(
        text("SELECT set_config('app.current_org_id', :organization_id, true)"),
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
