"""The research project model must agree with the migration that builds the table.

Migration ``4141aabbf97d_initial_schema_with_all_models`` creates
``research_projects.user_id`` as ``sa.UUID()`` with a foreign key to
``users.id``, and that migration is what builds the database in CI, in the
docker-compose stack, and in production. The model declared the same column as
``String(255)``. SQLAlchemy takes a filter's bind type from the *model*, so the
tenant-scoped project query went to the server as ``$1::VARCHAR`` against a
``uuid`` column and Postgres refused it outright:

    operator does not exist: uuid = character varying

That is a 500 on ``GET /api/v1/research/projects`` where an empty list is the
answer, and it reached a deployed stack with the whole suite green.

Nothing caught it because every Postgres-backed suite builds its schema with
``Base.metadata.create_all`` (``tests/integration/conftest.py``), i.e. *from the
models*. There the divergence cancels out: the same wrong declaration generates
both the query and the column it queries, so they agree and the query runs. The
mismatch is only observable where the schema comes from the migrations. These
tests compare the model against the migration directly, so they need no
database and cannot be silenced by a schema built from the thing under test.
"""

import ast
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import asyncpg as postgresql_asyncpg
from sqlalchemy.schema import CreateTable

from src.models.db.research_project import ResearchProject

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "4141aabbf97d_initial_schema_with_all_models.py"
)


def _migration_column_type(table: str, column: str) -> str:
    """Return the type a migration declares for a column, e.g. ``UUID``.

    Parsed from the migration source rather than hardcoded, so this test tracks
    the schema-of-record instead of a copy of it that can drift.
    """
    tree = ast.parse(MIGRATION.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "create_table"):
            continue
        if not (node.args and getattr(node.args[0], "value", None) == table):
            continue
        for arg in node.args[1:]:
            if not isinstance(arg, ast.Call):
                continue
            if getattr(arg.func, "attr", None) != "Column":
                continue
            if not (arg.args and getattr(arg.args[0], "value", None) == column):
                continue
            return arg.args[1].func.attr
    raise AssertionError(f"{table}.{column} not found in {MIGRATION.name}")


def test_user_id_column_matches_the_migration_that_creates_it() -> None:
    """The model's column type is the one the migration actually creates."""
    migration_type = _migration_column_type("research_projects", "user_id")
    model_ddl = str(
        CreateTable(ResearchProject.__table__).compile(
            dialect=postgresql_asyncpg.dialect()
        )
    )

    assert migration_type == "UUID"
    assert "user_id UUID" in model_ddl, (
        f"migration creates user_id as {migration_type}; model renders:\n{model_ddl}"
    )


def test_user_id_filter_binds_as_uuid_on_postgres() -> None:
    """The tenant-scoped query's bind is castable against a ``uuid`` column.

    This is the exact comparison ``ResearchRepository.get_by_user`` builds, and
    the parameter it binds is a ``str`` — as it is in production, where the
    tenant context carries string identifiers. A ``VARCHAR`` bind here is the
    500.
    """
    statement = select(ResearchProject.id).where(
        ResearchProject.user_id == str(uuid.uuid4())
    )

    compiled = str(statement.compile(dialect=postgresql_asyncpg.dialect()))

    assert "user_id = $1::UUID" in compiled, compiled
    assert "VARCHAR" not in compiled, compiled
