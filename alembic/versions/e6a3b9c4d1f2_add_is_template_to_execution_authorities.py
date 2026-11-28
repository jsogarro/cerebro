"""Add is_template flag to agent_execution_authorities.

Revision ID: e6a3b9c4d1f2
Revises: d4e8f1a3b7c2
Create Date: 2026-11-28

Marks operator-registered execution authority templates that may be used as
ceilings for deterministic per-request run minting. Existing authority rows
default to false so one-off authorities are never treated as reusable templates.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6a3b9c4d1f2"
down_revision: str | Sequence[str] | None = "d4e8f1a3b7c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_execution_authorities",
        sa.Column(
            "is_template",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_execution_authorities", "is_template")
