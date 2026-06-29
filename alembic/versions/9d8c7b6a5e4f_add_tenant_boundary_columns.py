"""Add tenant boundary columns

Revision ID: 9d8c7b6a5e4f
Revises: e1c02ed69b45
Create Date: 2026-05-02 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d8c7b6a5e4f"
down_revision: str | Sequence[str] | None = "e1c02ed69b45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable tenant columns and indexes as the first RBAC slice."""
    op.add_column(
        "users",
        sa.Column(
            "organization_id",
            sa.UUID(),
            nullable=True,
            comment="Tenant organization boundary identifier",
        ),
    )
    op.add_column(
        "research_projects",
        sa.Column(
            "organization_id",
            sa.UUID(),
            nullable=True,
            comment="Tenant organization boundary identifier",
        ),
    )
    op.add_column(
        "agent_tasks",
        sa.Column(
            "organization_id",
            sa.UUID(),
            nullable=True,
            comment="Tenant organization boundary identifier",
        ),
    )

    op.create_index("idx_user_org_active", "users", ["organization_id", "is_active"])
    op.create_index(
        "idx_project_org_user_status",
        "research_projects",
        ["organization_id", "user_id", "status", "created_at"],
    )
    op.create_index(
        "idx_project_org_status",
        "research_projects",
        ["organization_id", "status", "created_at"],
    )
    op.create_index(
        "idx_task_org_project_status",
        "agent_tasks",
        ["organization_id", "project_id", "status"],
    )
    op.create_index(
        "idx_task_org_status",
        "agent_tasks",
        ["organization_id", "status", "created_at"],
    )


def downgrade() -> None:
    """Remove tenant columns and indexes."""
    op.drop_index("idx_task_org_status", table_name="agent_tasks")
    op.drop_index("idx_task_org_project_status", table_name="agent_tasks")
    op.drop_index("idx_project_org_status", table_name="research_projects")
    op.drop_index("idx_project_org_user_status", table_name="research_projects")
    op.drop_index("idx_user_org_active", table_name="users")

    op.drop_column("agent_tasks", "organization_id")
    op.drop_column("research_projects", "organization_id")
    op.drop_column("users", "organization_id")
