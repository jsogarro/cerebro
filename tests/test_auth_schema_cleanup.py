"""Reachability and migration contracts for the dormant auth schema cleanup."""

import ast
import os
import subprocess
import sys
from pathlib import Path

from src.models.db import Base, ResearchProject, User

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / "alembic" / "versions"
CLEANUP_REVISION = "f8c9d0e1a2b3"
PARENT_REVISION = "c4a8d1e2f307"

REMOVED_TABLES = {
    "api_keys",
    "mfa_settings",
    "password_history",
    "user_sessions",
}
REMOVED_MODULES = {
    "src.models.db.api_key",
    "src.models.db.auth_tables",
    "src.models.db.mfa_settings",
    "src.models.db.password_history",
    "src.models.db.user_session",
    "src.repositories.api_key_repository",
}
REMOVED_RELATIONSHIPS = {
    "api_keys",
    "mfa_settings",
    "password_history",
    "sessions",
}


def _cleanup_migration() -> Path:
    matches = list(MIGRATIONS_DIR.glob(f"{CLEANUP_REVISION}_*.py"))
    assert len(matches) == 1, matches
    return matches[0]


def _run_alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = "postgresql://user:pass@localhost/db"
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments, "--sql"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_model_registry_excludes_removed_auth_schema_but_keeps_live_models() -> None:
    """Only models with live callers remain registered in the ORM metadata."""
    assert not REMOVED_TABLES.intersection(Base.metadata.tables)
    assert {
        "agent_tasks",
        "audit_logs",
        "oauth_accounts",
        "security_alerts",
    }.issubset(Base.metadata.tables)

    user_relationships = set(User.__mapper__.relationships.keys())
    assert not REMOVED_RELATIONSHIPS.intersection(user_relationships)
    assert {"audit_logs", "oauth_accounts", "security_alerts"}.issubset(
        user_relationships
    )
    assert "agent_tasks" in ResearchProject.__mapper__.relationships


def test_removed_auth_modules_have_no_source_or_factory_importers() -> None:
    """Deleted model/repository modules have no remaining Python importers."""
    roots = [REPO_ROOT / "src", REPO_ROOT / "tests" / "factories"]
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert node.module not in REMOVED_MODULES, (path, node.module)
                elif isinstance(node, ast.Import):
                    assert all(
                        alias.name not in REMOVED_MODULES for alias in node.names
                    ), (
                        path,
                        [alias.name for alias in node.names],
                    )

    for module in REMOVED_MODULES:
        module_path = REPO_ROOT / Path(module.replace(".", "/") + ".py")
        assert not module_path.exists(), module_path


def test_cleanup_migration_declares_reversible_revision() -> None:
    """The cleanup is a linear, explicit Alembic revision with both directions."""
    tree = ast.parse(_cleanup_migration().read_text())
    assignments = {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }

    assert assignments["revision"] == CLEANUP_REVISION
    assert assignments["down_revision"] == PARENT_REVISION
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    table_calls = {
        (node.func.attr, node.args[0].value)
        for node in calls
        if isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
        and node.func.attr in {"create_table", "drop_table"}
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }

    assert table_calls == {
        (operation, table)
        for operation in {"create_table", "drop_table"}
        for table in REMOVED_TABLES
    }


def test_cleanup_migration_offline_upgrade_is_executable() -> None:
    """Offline PostgreSQL SQL drops only the dead auth tables and enum."""
    result = _run_alembic("upgrade", f"{PARENT_REVISION}:{CLEANUP_REVISION}")
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    for table in REMOVED_TABLES:
        assert f"DROP TABLE {table}" in output
    assert "DROP TYPE mfa_method" in output
    assert "DROP TABLE agent_tasks" not in output
    assert "DROP TABLE audit_logs" not in output


def test_cleanup_migration_offline_downgrade_is_executable() -> None:
    """Offline PostgreSQL SQL recreates the removed schema on downgrade."""
    result = _run_alembic("downgrade", f"{CLEANUP_REVISION}:{PARENT_REVISION}")
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    for table in REMOVED_TABLES:
        assert f"CREATE TABLE {table}" in output
    assert "CREATE TYPE mfa_method" in output
