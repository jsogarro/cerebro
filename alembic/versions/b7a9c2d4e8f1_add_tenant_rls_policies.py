"""Add tenant row-level security policies

Revision ID: b7a9c2d4e8f1
Revises: 9d8c7b6a5e4f
Create Date: 2026-05-02 18:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7a9c2d4e8f1"
down_revision: str | Sequence[str] | None = "9d8c7b6a5e4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgresql() -> bool:
    """Return whether the active Alembic bind is PostgreSQL."""
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    """Enable tenant RLS policies for tenant-owned data tables."""
    if not _is_postgresql():
        return

    op.execute("ALTER TABLE research_projects ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_tasks ENABLE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY tenant_isolation_research_projects
        ON research_projects
        USING (
            organization_id = current_setting('app.current_org_id', true)::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation_agent_tasks
        ON agent_tasks
        USING (
            organization_id = current_setting('app.current_org_id', true)::uuid
        )
        """
    )


def downgrade() -> None:
    """Remove tenant RLS policies."""
    if not _is_postgresql():
        return

    op.execute("DROP POLICY IF EXISTS tenant_isolation_agent_tasks ON agent_tasks")
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_research_projects "
        "ON research_projects"
    )
    op.execute("ALTER TABLE agent_tasks DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE research_projects DISABLE ROW LEVEL SECURITY")
