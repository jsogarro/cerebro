"""The never-adopted ``agent_tasks`` table and ``TaskRepository`` are gone.

Nothing wrote ``agent_tasks``: run state lives in the durable lifecycle tables
(``agent_runs`` / ``agent_run_tasks`` / ``agent_task_attempts``). The table, its
ORM model and repository, and every reader are removed, and the migration that
drops them is reversible.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

from src.models.db import Base, ResearchProject

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / "alembic" / "versions"
DROP_REVISION = "d2e6f0a4b8c1"
PARENT_REVISION = "a1b2c3d4e5f6"
REMOVED_MODULES = {"src.models.db.agent_task", "src.repositories.task_repository"}


def _drop_migration() -> Path:
    matches = list(MIGRATIONS_DIR.glob(f"{DROP_REVISION}_*.py"))
    assert len(matches) == 1, matches
    return matches[0]


def _alembic_sql(*arguments: str) -> str:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = "postgresql://user:pass@localhost/db"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments, "--sql"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    return output


def test_agent_tasks_is_not_mapped() -> None:
    assert "agent_tasks" not in Base.metadata.tables
    assert "agent_tasks" not in ResearchProject.__mapper__.relationships


def test_removed_modules_are_gone_and_unimported() -> None:
    for module in REMOVED_MODULES:
        assert not (REPO_ROOT / (module.replace(".", "/") + ".py")).exists()
    for root in (REPO_ROOT / "src", REPO_ROOT / "tests"):
        for path in root.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
                if isinstance(node, ast.ImportFrom):
                    assert node.module not in REMOVED_MODULES, (path, node.module)
                    if node.module in {"src.models.db", "src.repositories"}:
                        names = {alias.name for alias in node.names}
                        assert not names & {"AgentTask", "TaskRepository"}, path
                elif isinstance(node, ast.Import):
                    names = {alias.name for alias in node.names}
                    assert not names & REMOVED_MODULES, path


def test_drop_migration_is_the_single_head_after_its_parent() -> None:
    source = _drop_migration().read_text()
    assert f'revision: str = "{DROP_REVISION}"' in source
    assert f'down_revision: str | Sequence[str] | None = "{PARENT_REVISION}"' in source
    parents = [
        path
        for path in MIGRATIONS_DIR.glob("*.py")
        if f'"{DROP_REVISION}"' in path.read_text() and path != _drop_migration()
    ]
    assert parents == []


def test_offline_upgrade_drops_table_policy_and_enum() -> None:
    output = _alembic_sql("upgrade", f"{PARENT_REVISION}:{DROP_REVISION}")

    assert "DROP POLICY IF EXISTS tenant_isolation_agent_tasks ON agent_tasks" in output
    assert "DROP TABLE agent_tasks" in output
    assert "DROP TYPE IF EXISTS taskstatus" in output
    assert "DROP TABLE research_projects" not in output


def test_offline_downgrade_restores_the_parent_schema() -> None:
    output = _alembic_sql("downgrade", f"{DROP_REVISION}:{PARENT_REVISION}")

    assert "CREATE TABLE agent_tasks" in output
    assert "CREATE TYPE taskstatus AS ENUM" in output
    assert "organization_id UUID" in output
    assert "FOREIGN KEY(project_id) REFERENCES research_projects (id)" in output
    for index in (
        "idx_task_agent_status",
        "idx_task_priority",
        "idx_task_project_status",
        "idx_task_started",
        "ix_agent_tasks_agent_type",
        "ix_agent_tasks_deleted_at",
        "ix_agent_tasks_project_id",
        "ix_agent_tasks_started_at",
        "ix_agent_tasks_status",
        "idx_task_org_project_status",
        "idx_task_org_status",
    ):
        assert f"CREATE INDEX {index} ON agent_tasks" in output
    assert "ALTER TABLE agent_tasks ENABLE ROW LEVEL SECURITY" in output
    assert "CREATE POLICY tenant_isolation_agent_tasks" in output
