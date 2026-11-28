"""Add external_read sensitivity and allowed_hosts on agent_capability_grants.

Revision ID: d4e8f1a3b7c2
Revises: c9d4a7e2b6f1
Create Date: 2026-11-28

Stores per-grant allowed outbound hosts on ``agent_capability_grants`` and
extends ``ck_agent_capability_grant_sensitivity`` with ``external_read`` so
outbound research tools enforce per-authority host allowlists across restarts.

Downgrade precondition (fail-closed): recreating the narrower CHECK constraint
fails if any row still carries ``sensitivity = 'external_read'``. Quiesce runs
and remove or migrate any ``external_read`` grant rows before downgrading.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e8f1a3b7c2"
down_revision: str | Sequence[str] | None = "c9d4a7e2b6f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_capability_grants",
        sa.Column(
            "allowed_hosts",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.drop_constraint(
        "ck_agent_capability_grant_sensitivity",
        "agent_capability_grants",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_capability_grant_sensitivity",
        "agent_capability_grants",
        "sensitivity IN ('exfiltration', 'external_read', 'external_write', "
        "'internal_write', 'read_only')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_agent_capability_grant_sensitivity",
        "agent_capability_grants",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_capability_grant_sensitivity",
        "agent_capability_grants",
        "sensitivity IN ('exfiltration', 'external_write', 'internal_write', "
        "'read_only')",
    )
    op.drop_column("agent_capability_grants", "allowed_hosts")
