"""HTTP dependency overrides remain substitutable with lightweight fakes."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import query_api, research
from src.api.services.direct_execution_service import (
    get_application_direct_execution_service,
)
from src.middleware.tenant_context import TenantContext, get_tenant_context
from src.models.db.research_project import ProjectStatus


class _FakeExecutionService:
    """Deliberately not a DirectExecutionService."""

    def __init__(self) -> None:
        self.started_projects: list[object] = []
        self.active_executions: dict[str, object] = {}

    async def start_research_execution(
        self,
        project: object,
        context: dict[str, Any] | None = None,
    ) -> str:
        self.started_projects.append(project)
        return "fake-execution"

    async def get_execution_status(
        self, execution_id: str, *, organization_id: str | None = None
    ) -> SimpleNamespace:
        return SimpleNamespace(
            status="running",
            routing_decision={"source": "fake"},
            supervisor_type="research",
            agent_results={},
            quality_scores={},
            execution_time_seconds=0.0,
            started_at=datetime.now(UTC),
        )


class _FakeResearchRepository:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.project = SimpleNamespace(
            id=uuid4(),
            title="Override Project",
            query='{"text":"Use the fake service","domains":["research"]}',
            user_id="user-1",
            status=ProjectStatus.DRAFT,
            created_at=now,
            updated_at=now,
        )
        self.updated: list[ProjectStatus] = []

    async def create(self, **_kwargs: object) -> SimpleNamespace:
        return self.project

    async def update_status(
        self,
        _project_id: object,
        project_status: ProjectStatus,
        organization_id: str | None = None,
    ) -> SimpleNamespace:
        assert organization_id == "org-1"
        self.updated.append(project_status)
        return self.project


def test_query_endpoint_uses_non_concrete_dependency_override() -> None:
    fake = _FakeExecutionService()
    test_app = FastAPI()
    test_app.include_router(query_api.router)
    test_app.dependency_overrides[get_application_direct_execution_service] = lambda: (
        fake
    )
    test_app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id="user-1", organization_id="org-1"
    )

    with TestClient(test_app) as client:
        response = client.post(
            "/api/v1/query/research",
            json={"query": "Inspect evidence for this research claim"},
        )

    assert response.status_code == 200
    assert response.json()["execution_id"] == "fake-execution"
    assert len(fake.started_projects) == 1


def test_research_endpoint_uses_non_concrete_dependency_override() -> None:
    fake = _FakeExecutionService()
    repository = _FakeResearchRepository()
    test_app = FastAPI()
    test_app.include_router(research.router, prefix="/api/v1")
    test_app.dependency_overrides[get_application_direct_execution_service] = lambda: (
        fake
    )
    test_app.dependency_overrides[research.get_research_repo] = lambda: repository
    test_app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id="user-1",
        organization_id="org-1",
    )

    with TestClient(test_app) as client:
        response = client.post(
            "/api/v1/research/projects",
            json={
                "title": "Override Project",
                "query": {
                    "text": "Use the fake service",
                    "domains": ["research"],
                },
                "user_id": "user-1",
            },
        )

    assert response.status_code == 201
    assert response.json()["status"] == "in_progress"
    assert len(fake.started_projects) == 1
    assert repository.updated == [ProjectStatus.IN_PROGRESS]
