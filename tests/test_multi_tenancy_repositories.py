"""Repository characterization tests for tenant scoping."""

from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db.api_key import APIKey
from src.models.db.research_project import ProjectStatus, ResearchProject
from src.repositories.base import BaseRepository
from src.repositories.research_repository import ResearchRepository
from src.repositories.task_repository import TaskRepository


class _ScalarResult:
    def all(self) -> list[Any]:
        return []


class _ExecuteResult:
    rowcount = 0

    def scalar(self) -> int:
        return 0

    def scalars(self) -> _ScalarResult:
        return _ScalarResult()

    def __iter__(self) -> Any:
        return iter([])


class _CapturingSession:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _ExecuteResult:
        self.statements.append(statement)
        return _ExecuteResult()


def _sql(statement: Any) -> str:
    return str(statement.compile(compile_kwargs={"literal_binds": False}))


def test_base_repository_applies_organization_scope() -> None:
    repo = BaseRepository(ResearchProject, cast(AsyncSession, object()))
    statement = repo.apply_organization_scope(repo.build_query(), uuid4())

    assert "research_projects.organization_id" in _sql(statement)


def test_base_repository_rejects_scope_for_unscoped_models() -> None:
    repo = BaseRepository(APIKey, cast(AsyncSession, object()))

    with pytest.raises(ValueError, match="does not support organization scoping"):
        repo.apply_organization_scope(repo.build_query(), uuid4())


@pytest.mark.asyncio
async def test_research_repository_applies_organization_scope_to_queries() -> None:
    session = _CapturingSession()
    repo = ResearchRepository(cast(AsyncSession, session))
    org_id = uuid4()

    await repo.get_in_progress(user_id=uuid4(), organization_id=org_id)
    await repo.search_projects(
        query="deep research",
        status=[ProjectStatus.COMPLETED],
        organization_id=org_id,
    )
    await repo.get_statistics(user_id=uuid4(), organization_id=org_id)

    assert session.statements
    assert all(
        "research_projects.organization_id" in _sql(stmt)
        for stmt in session.statements
    )


@pytest.mark.asyncio
async def test_task_repository_applies_organization_scope_to_queries() -> None:
    session = _CapturingSession()
    repo = TaskRepository(cast(AsyncSession, session))
    org_id = uuid4()

    await repo.get_pending_tasks(agent_type="literature", organization_id=org_id)
    await repo.get_failed_tasks(organization_id=org_id)
    await repo.get_task_metrics(project_id=uuid4(), organization_id=org_id)
    await repo.get_ready_tasks(project_id=uuid4(), organization_id=org_id)

    assert session.statements
    assert all(
        "agent_tasks.organization_id" in _sql(stmt) for stmt in session.statements
    )
