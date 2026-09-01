"""Add optional leases to durable tool invocations.

Revision ID: a1b2c3d4e5f6
Revises: f8c9d0e1a2b3
Create Date: 2026-09-01

Lease columns remain nullable so rows written before lease acquisition, and
legacy pending rows, remain valid. The partial index covers only statuses that
can hold a lease; terminal rows do not participate in expiry scans.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f8c9d0e1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "agent_tool_invocations"
PENDING_LEASE_INDEX = "idx_agent_tool_invocation_pending_lease"
PENDING_STATUSES = "status IN ('requested', 'running')"


def upgrade() -> None:
    """Add nullable lease fields and the pending-expiry lookup index."""
    op.add_column(
        TABLE,
        sa.Column("lease_owner_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        TABLE,
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        PENDING_LEASE_INDEX,
        TABLE,
        ["status", "lease_expires_at"],
        postgresql_where=sa.text(PENDING_STATUSES),
        sqlite_where=sa.text(PENDING_STATUSES),
    )


def downgrade() -> None:
    """Remove the pending lease lookup index and nullable lease fields."""
    op.drop_index(PENDING_LEASE_INDEX, table_name=TABLE)
    op.drop_column(TABLE, "lease_expires_at")
    op.drop_column(TABLE, "lease_owner_id")
