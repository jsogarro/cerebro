"""Characterization tests for tenant boundary model columns."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from src.models.db.agent_task import AgentTask
from src.models.db.research_project import ResearchProject
from src.models.db.user import User

REPO_ROOT = Path(__file__).resolve().parent.parent


def _index_names(model: Any) -> set[str]:
    return {index.name or "" for index in model.__table__.indexes}


def test_user_has_tenant_boundary_column_and_index() -> None:
    org_id = uuid4()
    user = User(
        email="tenant-user@example.com",
        username="tenant-user",
        hashed_password="hash",
        organization_id=org_id,
    )

    assert User.__table__.c.organization_id.nullable is True
    assert user.organization_id == org_id
    assert "idx_user_org_active" in _index_names(User)


def test_research_project_has_tenant_boundary_indexes() -> None:
    org_id = uuid4()
    project = ResearchProject(
        title="Tenant-scoped research",
        query="tenant boundary",
        domains=["security"],
        user_id="user-a",
        organization_id=org_id,
    )

    assert ResearchProject.__table__.c.organization_id.nullable is True
    assert project.organization_id == org_id
    assert {
        "idx_project_org_user_status",
        "idx_project_org_status",
    }.issubset(_index_names(ResearchProject))


def test_agent_task_has_tenant_boundary_indexes() -> None:
    org_id = uuid4()
    task = AgentTask(
        project_id=uuid4(),
        organization_id=org_id,
        agent_type="literature_review",
        input_data={},
    )

    assert AgentTask.__table__.c.organization_id.nullable is True
    assert task.organization_id == org_id
    assert {
        "idx_task_org_project_status",
        "idx_task_org_status",
    }.issubset(_index_names(AgentTask))


def test_tenant_boundary_migration_adds_columns_and_indexes() -> None:
    migration = (
        REPO_ROOT
        / "alembic"
        / "versions"
        / "9d8c7b6a5e4f_add_tenant_boundary_columns.py"
    ).read_text()

    assert 'down_revision: str | Sequence[str] | None = "e1c02ed69b45"' in migration
    for table_name in ("users", "research_projects", "agent_tasks"):
        assert f'"{table_name}"' in migration
        assert '"organization_id"' in migration
    for index_name in (
        "idx_user_org_active",
        "idx_project_org_user_status",
        "idx_project_org_status",
        "idx_task_org_project_status",
        "idx_task_org_status",
    ):
        assert index_name in migration
