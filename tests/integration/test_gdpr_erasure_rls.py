"""GDPR erasure as ``cerebro_app`` under forced row-level security.

``test_gdpr_erasure.py`` runs as a superuser, which bypasses row policies and so
cannot see an erasure that deletes nothing. Here the route runs over the real
application login against the migrated schema.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes import users
from src.middleware.auth_middleware import get_current_active_user, get_jwt_service
from src.models.db.generated_report import GeneratedReport
from src.models.db.research_project import ResearchProject
from src.models.db.session import get_session
from src.models.db.similarity_check import EgressEvent
from src.models.db.uploaded_document import UploadedDocument, UsageLedger
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

ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()
POLICED = (
    GeneratedReport,
    ResearchProject,
    UploadedDocument,
    UsageLedger,
    EgressEvent,
)


async def _seed(
    admin: Factory, org: uuid.UUID, tag: str, *, project: bool
) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with admin() as s:
        s.add(
            User(
                id=user_id,
                email=f"{tag}@example.com",
                username=tag,
                hashed_password="x",
                organization_id=org,
            )
        )
        await s.flush()
        if project:
            s.add(
                ResearchProject(
                    title=tag,
                    query="q",
                    user_id=str(user_id),
                    organization_id=org,
                    domains=["privacy"],
                    status="DRAFT",
                )
            )
        s.add(
            GeneratedReport(
                user_id=user_id,
                organization_id=org,
                title=tag,
                report_type="full",
                query="q",
            )
        )
        s.add(UsageLedger(organization_id=org, user_id=user_id, kind="upload"))
        s.add(
            UploadedDocument(
                organization_id=org,
                user_id=user_id,
                filename_enc="f",
                media_type="plain",
                source_sha256="s",
                text_sha256="t",
                char_count=5,
                pages=[],
                extractor_version="v",
                text_enc="x",
                declared_authors_enc="y",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        s.add(
            EgressEvent(
                organization_id=org,
                user_id=user_id,
                host="arxiv.org",
                request_class="pdf",
                term_count=0,
                bytes_received=1,
                duration_ms=1,
                status="200",
            )
        )
        await s.commit()
    return user_id


async def _rows(admin: Factory, user_id: uuid.UUID) -> dict[str, int]:
    async with admin() as s:
        counts = {
            m.__tablename__: (
                await s.execute(
                    select(func.count())
                    .select_from(m)
                    .where(
                        m.user_id == (str(user_id) if m is ResearchProject else user_id)
                    )
                )
            ).scalar_one()
            for m in POLICED
        }
        counts["users"] = (
            await s.execute(
                select(func.count()).select_from(User).where(User.id == user_id)
            )
        ).scalar_one()
        return counts


def _client(app_factory: Factory, caller: uuid.UUID, *, superuser: bool = False):
    async def _session() -> AsyncGenerator[AsyncSession, None]:
        async with app_factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    jwt = Mock(revoke_all_user_tokens=AsyncMock(return_value=0))
    app = FastAPI()
    app.include_router(users.router)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_jwt_service] = lambda: jwt
    app.dependency_overrides[users.get_memory_system] = lambda: None
    app.dependency_overrides[get_current_active_user] = lambda: User(
        id=caller, is_superuser=superuser
    )
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


async def test_erasure_deletes_every_policed_row_of_the_user_and_no_one_elses(
    app: Factory, admin: Factory
) -> None:
    victim = await _seed(admin, ORG_A, "victim", project=True)
    bystander = await _seed(admin, ORG_B, "bystander", project=True)

    async with _client(app, victim) as client:
        response = await client.delete(f"/users/{victim}/gdpr")

    assert response.status_code == 200, response.text
    assert set((await _rows(admin, victim)).values()) == {0}
    assert set((await _rows(admin, bystander)).values()) == {1}


async def test_erasure_of_a_user_without_projects_still_removes_their_egress_rows(
    app: Factory, admin: Factory
) -> None:
    victim = await _seed(admin, ORG_A, "no-project", project=False)

    async with _client(app, victim) as client:
        response = await client.delete(f"/users/{victim}/gdpr")

    assert response.status_code == 200, response.text
    assert (await _rows(admin, victim))["egress_events"] == 0


async def test_a_superuser_erases_a_user_of_another_organization(
    app: Factory, admin: Factory
) -> None:
    victim = await _seed(admin, ORG_B, "other-org", project=True)
    operator = await _seed(admin, ORG_A, "operator", project=False)

    async with _client(app, operator, superuser=True) as client:
        response = await client.delete(f"/users/{victim}/gdpr")

    assert response.status_code == 200, response.text
    assert set((await _rows(admin, victim)).values()) == {0}
