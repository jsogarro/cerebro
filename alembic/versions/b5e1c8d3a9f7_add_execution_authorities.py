"""Add operator-registered execution authorities.

Revision ID: b5e1c8d3a9f7
Revises: a7d2e9c4b1f8
Create Date: 2026-11-26

Additive: one new table, no change to any existing table. The tenant RLS policy
is posture only (the app role owns the table); the repository's organization
filter is the real boundary.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5e1c8d3a9f7"
down_revision: str | Sequence[str] | None = "a7d2e9c4b1f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_execution_authorities",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("authority_id", sa.String(255), nullable=False),
        sa.Column("authority_version", sa.String(100), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "authority_id",
            "authority_version",
            name="uq_agent_execution_authority_identity",
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_agent_execution_authority_digest",
        ),
    )
    op.execute("ALTER TABLE agent_execution_authorities ENABLE ROW LEVEL SECURITY")
    # NULLIF: after a transaction that set the tenant ends, the setting reads
    # back as '' and a bare ::uuid cast would fail every later query.
    op.execute(
        """
        CREATE POLICY tenant_isolation_agent_execution_authorities
        ON agent_execution_authorities
        USING (
            organization_id
            = NULLIF(current_setting('app.current_org_id', true), '')::uuid
        )
        """
    )


def downgrade() -> None:
    op.drop_table("agent_execution_authorities")
