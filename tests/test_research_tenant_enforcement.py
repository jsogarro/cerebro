"""Tenant enforcement tests for research route handlers."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, status

from src.api.routes import research
from src.api.routes.research import CreateResearchProjectRequest
from src.middleware.tenant_context import TenantContext
from src.models.db.research_project import ProjectStatus
from src.models.research_project import ResearchQuery


class _Repo:
    def __init__(self) -> None:
        self.created: dict[str, object] | None = None
        self.updated: list[dict[str, object]] = []
        self.get_for_user_calls: list[dict[str, object]] = []
        self.get_by_user_calls: list[dict[str, object]] = []
        self.project = SimpleNamespace(
            id=uuid4(),
            title="Tenant Project",
            query='{"text": "tenant query", "domains": ["AI"]}',
            user_id="user-123",
            status=ProjectStatus.IN_PROGRESS,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            results=[],
        )

    async def create(self, **kwargs: object) -> SimpleNamespace:
        self.created = kwargs
        return self.project

    async def update_status(
        self,
        project_id: object,
        project_status: ProjectStatus,
        organization_id: str | None = None,
    ) -> SimpleNamespace:
        self.updated.append(
            {
                "project_id": project_id,
                "status": project_status,
                "organization_id": organization_id,
            }
        )
        return self.project

    async def get_for_user(
        self,
        project_id: object,
        user_id: str,
        organization_id: str,
        load_relationships: list[str] | None = None,
    ) -> SimpleNamespace | None:
        self.get_for_user_calls.append(
            {
                "project_id": project_id,
                "user_id": user_id,
                "organization_id": organization_id,
                "load_relationships": load_relationships,
            }
        )
        return self.project

    async def get_by_user(
        self,
        user_id: str,
        status: ProjectStatus | None = None,
        limit: int | None = None,
        offset: int | None = None,
        organization_id: str | None = None,
    ) -> list[SimpleNamespace]:
        self.get_by_user_calls.append(
            {
                "user_id": user_id,
                "status": status,
                "limit": limit,
                "offset": offset,
                "organization_id": organization_id,
            }
        )
        return [self.project]


class _ExecutionService:
    active_executions: dict[str, object] = {}

    async def start_research_execution(self, _project: object) -> str:
        return "execution-123"


def _tenant_context() -> TenantContext:
    return TenantContext(user_id="user-123", organization_id="org-123")


def _create_request(user_id: str = "user-123") -> CreateResearchProjectRequest:
    return CreateResearchProjectRequest(
        title="Tenant Project",
        query=ResearchQuery(text="tenant query", domains=["AI"]),
        user_id=user_id,
    )


@pytest.mark.asyncio
async def test_create_research_project_rejects_body_user_mismatch() -> None:
    repo = _Repo()

    with pytest.raises(HTTPException) as exc_info:
        await research.create_research_project(
            request=_create_request(user_id="other-user"),
            repo=repo,  # type: ignore[arg-type]
            tenant_context=_tenant_context(),
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert repo.created is None


@pytest.mark.asyncio
async def test_create_research_project_sets_tenant_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo()
    monkeypatch.setattr(
        research,
        "get_direct_execution_service",
        lambda: _ExecutionService(),
    )

    project = await research.create_research_project(
        request=_create_request(),
        repo=repo,  # type: ignore[arg-type]
        tenant_context=_tenant_context(),
    )

    assert project.user_id == "user-123"
    assert repo.created is not None
    assert repo.created["user_id"] == "user-123"
    assert repo.created["organization_id"] == "org-123"
    assert repo.updated[0]["organization_id"] == "org-123"


@pytest.mark.asyncio
async def test_get_research_project_filters_by_user_and_org() -> None:
    repo = _Repo()
    project_id = uuid4()

    await research.get_research_project(
        project_id=project_id,
        repo=repo,  # type: ignore[arg-type]
        tenant_context=_tenant_context(),
    )

    assert repo.get_for_user_calls == [
        {
            "project_id": project_id,
            "user_id": "user-123",
            "organization_id": "org-123",
            "load_relationships": None,
        }
    ]


@pytest.mark.asyncio
async def test_get_research_project_returns_404_outside_tenant_boundary() -> None:
    repo = _Repo()

    async def _not_found(*_args: object, **_kwargs: object) -> None:
        return None

    repo.get_for_user = _not_found  # type: ignore[method-assign]

    with pytest.raises(HTTPException) as exc_info:
        await research.get_research_project(
            project_id=uuid4(),
            repo=repo,  # type: ignore[arg-type]
            tenant_context=_tenant_context(),
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_list_research_projects_uses_authenticated_user_and_org() -> None:
    repo = _Repo()

    await research.list_research_projects(
        user_id=None,
        status=None,
        limit=25,
        offset=5,
        repo=repo,  # type: ignore[arg-type]
        tenant_context=_tenant_context(),
    )

    assert repo.get_by_user_calls == [
        {
            "user_id": "user-123",
            "status": None,
            "limit": 25,
            "offset": 5,
            "organization_id": "org-123",
        }
    ]


@pytest.mark.asyncio
async def test_list_research_projects_rejects_other_user_filter() -> None:
    repo = _Repo()

    with pytest.raises(HTTPException) as exc_info:
        await research.list_research_projects(
            user_id="other-user",
            status=None,
            limit=10,
            offset=0,
            repo=repo,  # type: ignore[arg-type]
            tenant_context=_tenant_context(),
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert repo.get_by_user_calls == []
