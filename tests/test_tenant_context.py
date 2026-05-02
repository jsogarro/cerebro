"""Tests for tenant context dependency helpers."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import TokenPayload
from src.middleware.tenant_context import (
    TenantContext,
    get_tenant_context,
    require_organization_id,
    set_postgres_tenant_context,
)


def _token_payload(organization_id: str | None) -> TokenPayload:
    now = datetime.now(UTC)
    return TokenPayload(
        sub="user-123",
        email="user@example.com",
        roles=[],
        permissions=[],
        organization_id=organization_id,
        jti="jti-123",
        iat=now,
        exp=now + timedelta(minutes=15),
        device_id=None,
    )


class _CapturingSession:
    def __init__(self) -> None:
        self.executed: list[tuple[Any, dict[str, str]]] = []

    async def execute(self, statement: Any, parameters: dict[str, str]) -> None:
        self.executed.append((statement, parameters))


def test_require_organization_id_returns_claim() -> None:
    assert require_organization_id(_token_payload("org-123")) == "org-123"


def test_require_organization_id_fails_closed_when_claim_missing() -> None:
    with pytest.raises(HTTPException) as exc_info:
        require_organization_id(_token_payload(None))

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "Tenant organization claim is required"


@pytest.mark.asyncio
async def test_set_postgres_tenant_context_sets_transaction_local_var() -> None:
    session = _CapturingSession()

    await set_postgres_tenant_context(cast(AsyncSession, session), "org-456")

    assert len(session.executed) == 1
    statement, parameters = session.executed[0]
    assert "SET LOCAL app.current_org_id" in str(statement)
    assert parameters == {"organization_id": "org-456"}


@pytest.mark.asyncio
async def test_get_tenant_context_returns_context_and_sets_db_var() -> None:
    session = _CapturingSession()

    context = await get_tenant_context(
        token_payload=_token_payload("org-789"),
        session=cast(AsyncSession, session),
    )

    assert context == TenantContext(user_id="user-123", organization_id="org-789")
    assert session.executed[0][1] == {"organization_id": "org-789"}
