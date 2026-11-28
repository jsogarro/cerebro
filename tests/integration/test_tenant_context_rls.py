"""The request tenant context survives a mid-handler commit under forced RLS."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from src.auth.models import TokenPayload
from src.middleware.tenant_context import get_tenant_context
from src.models.db.research_project import ResearchProject
from src.models.db.user import User
from tests.integration.rls_fixtures import (  # noqa: F401
    Factory,
    admin_fixture,
    app_fixture,
    engines_fixture,
    maint_fixture,
    server_fixture,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow]
pytestmark.append(pytest.mark.asyncio(loop_scope="module"))

ORG = uuid.uuid4()


def _token() -> TokenPayload:
    now = datetime.now(UTC)
    return TokenPayload(
        sub=str(uuid.uuid4()),
        email="u@example.com",
        roles=[],
        permissions=[],
        organization_id=str(ORG),
        jti="j",
        iat=now,
        exp=now + timedelta(minutes=15),
        device_id=None,
    )


async def test_a_handler_still_sees_its_tenant_after_it_commits(
    app: Factory, admin: Factory
) -> None:
    user_id = uuid.uuid4()
    async with admin() as s:
        s.add(
            User(
                id=user_id,
                email="u@example.com",
                username="u",
                hashed_password="x",
                organization_id=ORG,
            )
        )
        await s.flush()
        s.add(
            ResearchProject(
                title="t",
                query="q",
                user_id=str(user_id),
                organization_id=ORG,
                domains=[],
                status="DRAFT",
            )
        )
        await s.commit()

    async with app() as session:
        await get_tenant_context(_token(), session)
        before = await session.scalar(select(func.count()).select_from(ResearchProject))
        await session.commit()
        after = await session.scalar(select(func.count()).select_from(ResearchProject))

    assert (before, after) == (1, 1)
