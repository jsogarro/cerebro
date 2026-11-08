"""Drop the never-adopted agent_tasks table.

Revision ID: d2e6f0a4b8c1
Revises: a1b2c3d4e5f6
Create Date: 2026-10-25

No code path ever wrote agent_tasks; run state lives in the durable lifecycle
tables. Nothing references it by foreign key. The downgrade reproduces the
schema the earlier migrations built (initial table, tenant column and indexes,
and the PostgreSQL RLS policy); rows removed by the upgrade are not recovered.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2e6f0a4b8c1"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "agent_tasks"


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    """Drop agent_tasks, its indexes, RLS policy, and status enum."""
    if _is_postgresql():
        op.execute("DROP POLICY IF EXISTS tenant_isolation_agent_tasks ON agent_tasks")
    for index in (
        "idx_task_org_status",
        "idx_task_org_project_status",
        "ix_agent_tasks_status",
        "ix_agent_tasks_started_at",
        "ix_agent_tasks_project_id",
        "ix_agent_tasks_deleted_at",
        "ix_agent_tasks_agent_type",
        "idx_task_started",
        "idx_task_project_status",
        "idx_task_priority",
        "idx_task_agent_status",
    ):
        op.drop_index(index, table_name=TABLE)
    op.drop_table(TABLE)
    if _is_postgresql():
        op.execute("DROP TYPE IF EXISTS taskstatus")


def downgrade() -> None:
    """Recreate agent_tasks as the parent revision left it."""
    op.create_table(
        TABLE,
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column(
            "agent_type",
            sa.String(length=100),
            nullable=False,
            comment="Type of agent (e.g., literature_review, synthesis)",
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "QUEUED",
                "IN_PROGRESS",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                "RETRYING",
                name="taskstatus",
            ),
            nullable=False,
        ),
        sa.Column(
            "input_data", sa.JSON(), nullable=False, comment="Input data for the agent"
        ),
        sa.Column(
            "output_data",
            sa.JSON(),
            nullable=True,
            comment="Output data from the agent",
        ),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
            comment="Error message if task failed",
        ),
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            comment="Number of retry attempts",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "execution_time_ms",
            sa.Integer(),
            nullable=True,
            comment="Execution time in milliseconds",
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            comment="Task priority (higher = more important)",
        ),
        sa.Column(
            "depends_on",
            sa.JSON(),
            nullable=True,
            comment="List of task IDs this task depends on",
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column(
            "organization_id",
            sa.UUID(),
            nullable=True,
            comment="Tenant organization boundary identifier",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["research_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_task_agent_status", TABLE, ["agent_type", "status"])
    op.create_index("idx_task_priority", TABLE, ["priority", "status"])
    op.create_index("idx_task_project_status", TABLE, ["project_id", "status"])
    op.create_index("idx_task_started", TABLE, ["started_at", "status"])
    for column in ("agent_type", "deleted_at", "project_id", "started_at", "status"):
        op.create_index(f"ix_agent_tasks_{column}", TABLE, [column])
    op.create_index(
        "idx_task_org_project_status",
        TABLE,
        ["organization_id", "project_id", "status"],
    )
    op.create_index(
        "idx_task_org_status", TABLE, ["organization_id", "status", "created_at"]
    )
    if _is_postgresql():
        op.execute("ALTER TABLE agent_tasks ENABLE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY tenant_isolation_agent_tasks
            ON agent_tasks
            USING (
                organization_id = current_setting('app.current_org_id', true)::uuid
            )
            """
        )
