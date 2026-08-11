"""Tenant context against a real Postgres server.

``set_postgres_tenant_context`` is a no-op on every dialect except Postgres,
and every other suite runs on SQLite — so until this module existed, nothing
executed the statement anywhere. The unit tests in
``tests/test_tenant_context.py`` assert its *text* against a fake session, and
``tests/integration/conftest.py`` overrides ``get_tenant_context`` outright to
skip it. A statement Postgres rejects therefore passed every gate and only
surfaced against the docker-compose stack, as a 500 on the first authenticated
request that carried an organization claim.

These tests execute it.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.middleware.tenant_context import set_postgres_tenant_context

pytestmark = pytest.mark.asyncio


async def test_set_postgres_tenant_context_is_accepted_by_postgres(
    db_session: AsyncSession,
) -> None:
    """The statement executes. `SET LOCAL x = :param` does not — `SET` is
    utility syntax and takes no bind parameters, so it reaches the server as
    `SET LOCAL app.current_org_id = $1` and raises PostgresSyntaxError."""
    organization_id = str(uuid4())

    await set_postgres_tenant_context(db_session, organization_id)


async def test_set_postgres_tenant_context_is_readable_by_rls_policies(
    db_session: AsyncSession,
) -> None:
    """The value is readable through the exact expression the RLS policies use.

    Policies installed by the multi-tenancy migration select on
    ``current_setting('app.current_org_id', true)::uuid``; setting a value the
    server stores but cannot cast would leave them comparing against an error.
    """
    organization_id = str(uuid4())

    await set_postgres_tenant_context(db_session, organization_id)

    result = await db_session.execute(
        text("SELECT current_setting('app.current_org_id', true)::uuid")
    )
    assert result.scalar_one() == UUID(organization_id)


async def test_set_postgres_tenant_context_is_transaction_local(
    db_session: AsyncSession,
) -> None:
    """The setting dies with the transaction.

    Sessions are pooled and reused across requests. A tenant identifier that
    outlived its transaction would be read by the next request on the same
    connection, which is the failure mode RLS exists to prevent.
    """
    await set_postgres_tenant_context(db_session, str(uuid4()))
    await db_session.rollback()

    result = await db_session.execute(
        text("SELECT current_setting('app.current_org_id', true)")
    )
    assert result.scalar_one() == ""
