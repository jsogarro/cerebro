"""Default the audit timestamps of the durable run and tool tables.

Revision ID: a9c3e5f7b1d2
Revises: c9d4a7e2b6f1
Create Date: 2026-11-27

The ORM declares ``server_default=now()`` on ``created_at`` and ``updated_at``
and omits both from its INSERTs, but the migrations that created these tables
gave the NOT NULL columns no default, so on a migrated PostgreSQL database
every first write to them failed. Tests that build the schema with
``create_all`` never saw it.

``oauth_accounts.first_connected_at`` is corrected here too: its default was the
timestamp of the migration run, so every row inserted later would have carried it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9c3e5f7b1d2"
down_revision: str | Sequence[str] | None = "c9d4a7e2b6f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "agent_runs",
    "agent_run_tasks",
    "agent_task_attempts",
    "agent_capability_grants",
    "agent_capability_approvals",
    "agent_artifacts",
    "agent_tool_invocations",
)


def upgrade() -> None:
    for table in _TABLES:
        for column in ("created_at", "updated_at"):
            op.alter_column(table, column, server_default=sa.func.now())
    op.alter_column(
        "oauth_accounts", "first_connected_at", server_default=sa.func.now()
    )


def downgrade() -> None:
    for table in _TABLES:
        for column in ("created_at", "updated_at"):
            op.alter_column(table, column, server_default=None)
    op.alter_column("oauth_accounts", "first_connected_at", server_default=None)
