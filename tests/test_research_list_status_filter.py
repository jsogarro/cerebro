"""``GET /api/v1/research/projects?status=`` filters on the statuses the API returns.

The list endpoint returns ``ResearchStatus`` values (a DRAFT row is reported as
``pending``) but used to filter with ``ProjectStatus(status)``, so an unknown
value was a 500 and so was ``pending``.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.routes.research import get_research_repo
from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_jwt_service
from src.middleware.tenant_context import TenantContext, get_tenant_context
from src.models.db.research_project import ProjectStatus

AUTH_USER_ID = uuid4()
AUTH_ORG_ID = uuid4()
AUTH_TOKEN = "test-token"


class _TestJWTService:
    async def validate_token(self, token: str) -> TokenPayload:
        assert token == AUTH_TOKEN
        now = datetime.now(UTC)
        return TokenPayload(
            sub=str(AUTH_USER_ID),
            email="test@example.com",
            organization_id=str(AUTH_ORG_ID),
            jti="test-jti",
            iat=now,
            exp=now + timedelta(minutes=5),
        )


class _FakeRepo:
    def __init__(self) -> None:
        self.statuses: list[ProjectStatus | None] = []

    async def get_by_user(self, _user_id: Any, **kwargs: Any) -> list[Any]:
        self.statuses.append(kwargs["status"])
        now = datetime.now(UTC)
        return [
            SimpleNamespace(
                id=uuid4(),
                title="Draft project",
                query='{"text":"q","domains":["d"]}',
                user_id=str(AUTH_USER_ID),
                status=ProjectStatus.DRAFT,
                created_at=now,
                updated_at=now,
            )
        ]


@pytest.fixture
def repo_and_client() -> Any:
    from src.api.main import app

    repo = _FakeRepo()
    app.dependency_overrides[get_research_repo] = lambda: repo
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id=str(AUTH_USER_ID), organization_id=str(AUTH_ORG_ID)
    )
    app.dependency_overrides[get_jwt_service] = _TestJWTService
    try:
        yield repo, TestClient(app, headers={"Authorization": f"Bearer {AUTH_TOKEN}"})
    finally:
        for dep in (get_research_repo, get_tenant_context, get_jwt_service):
            app.dependency_overrides.pop(dep, None)


def test_unknown_status_is_422_not_500(repo_and_client: Any) -> None:
    repo, client = repo_and_client
    response = client.get("/api/v1/research/projects?status=bogus")
    assert response.status_code == 422
    assert repo.statuses == []


def test_pending_filters_on_draft_rows(repo_and_client: Any) -> None:
    repo, client = repo_and_client
    response = client.get("/api/v1/research/projects?status=pending")
    assert response.status_code == 200
    assert repo.statuses == [ProjectStatus.DRAFT]
    assert response.json()[0]["status"] == "pending"


def test_stored_status_passes_through(repo_and_client: Any) -> None:
    repo, client = repo_and_client
    response = client.get("/api/v1/research/projects?status=completed")
    assert response.status_code == 200
    assert repo.statuses == [ProjectStatus.COMPLETED]


def test_planning_matches_nothing_without_querying(repo_and_client: Any) -> None:
    """No stored row is ever 'planning', so the honest answer is an empty list."""
    repo, client = repo_and_client
    response = client.get("/api/v1/research/projects?status=planning")
    assert response.status_code == 200
    assert response.json() == []
    assert repo.statuses == []
