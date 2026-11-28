"""Add the run ownership lease and fencing token.

Revision ID: d5a1c8e2b7f3
Revises: f1c8a4d7e2b6
Create Date: 2026-11-27

Additive and nullable (the token defaults to 0), so executors that predate
fencing keep working: they never set an owner, and a row with no owner is never
reconciled as interrupted. Quiesce those executors before a one-time
reconciliation of their rows. Rollback must preserve populated columns: stop
admission and owners, then roll forward a corrected writer instead of dropping
them.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5a1c8e2b7f3"
down_revision: str | Sequence[str] | None = "f1c8a4d7e2b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs", sa.Column("owner_instance_id", sa.String(255), nullable=True)
    )
    op.add_column(
        "agent_runs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "fencing_token",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
            comment="Raised on every acquisition and reconciliation; never reused",
        ),
    )
    op.create_check_constraint(
        "ck_agent_run_fence_non_negative", "agent_runs", "fencing_token >= 0"
    )
    op.create_check_constraint(
        "ck_agent_run_lease_pair",
        "agent_runs",
        "(owner_instance_id IS NULL) = (lease_expires_at IS NULL)",
    )
    op.create_index("idx_agent_run_lease_expiry", "agent_runs", ["lease_expires_at"])


def downgrade() -> None:
    op.drop_index("idx_agent_run_lease_expiry", table_name="agent_runs")
    op.drop_constraint("ck_agent_run_lease_pair", "agent_runs", type_="check")
    op.drop_constraint("ck_agent_run_fence_non_negative", "agent_runs", type_="check")
    op.drop_column("agent_runs", "fencing_token")
    op.drop_column("agent_runs", "lease_expires_at")
    op.drop_column("agent_runs", "owner_instance_id")
