"""Add TOTP second factor columns and single-use recovery codes.

Revision ID: c9d4a7e2b6f1
Revises: b8e3f1a5c7d9
Create Date: 2026-11-27

The TOTP secret is stored Fernet-encrypted. Recovery codes keep only a hash and
cascade from ``users``, so erasure leaves none behind.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d4a7e2b6f1"
down_revision: str | Sequence[str] | None = "b8e3f1a5c7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_secret_enc", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("users", sa.Column("totp_last_step", sa.BigInteger(), nullable=True))
    op.create_table(
        "mfa_recovery_codes",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_mfa_recovery_codes_user_id_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", "code_hash", name="uq_mfa_recovery_user_code"),
    )


def downgrade() -> None:
    op.drop_table("mfa_recovery_codes")
    op.drop_column("users", "totp_last_step")
    op.drop_column("users", "totp_enabled")
    op.drop_column("users", "totp_secret_enc")
