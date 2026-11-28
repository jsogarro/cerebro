"""Add personal_access_tokens table with forced RLS and maintenance lookup policies.

Revision ID: e7b2d4f9a1c3
Revises: d5a1c8e2b7f3
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7b2d4f9a1c3"
down_revision: str | Sequence[str] | None = "d5a1c8e2b7f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "personal_access_tokens"
_NULLIF = "organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid"


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _uuid_type() -> sa.types.TypeEngine[object]:
    if _is_postgresql():
        return postgresql.UUID(as_uuid=True)
    return sa.CHAR(36)


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("id", _uuid_type(), primary_key=True, nullable=False),
        sa.Column("organization_id", _uuid_type(), nullable=False),
        sa.Column(
            "user_id",
            _uuid_type(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=20), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("issued_at_ns", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "organization_id IS NOT NULL",
            name="ck_pat_organization_id_not_null",
        ),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_pat_name_non_empty"),
        sa.CheckConstraint("length(token_hash) = 64", name="ck_pat_token_hash_len"),
        sa.CheckConstraint(
            "length(token_prefix) > 0", name="ck_pat_token_prefix_non_empty"
        ),
        sa.CheckConstraint("issued_at_ns > 0", name="ck_pat_issued_at_ns_positive"),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_pat_expires_after_created"
        ),
    )
    op.create_index(
        "uq_personal_access_tokens_token_hash",
        TABLE_NAME,
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_personal_access_tokens_org_user",
        TABLE_NAME,
        ["organization_id", "user_id"],
    )
    op.create_index(
        "ix_personal_access_tokens_user_id",
        TABLE_NAME,
        ["user_id"],
    )

    if not _is_postgresql():
        return

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cerebro_maint') THEN
                CREATE ROLE cerebro_maint NOLOGIN NOBYPASSRLS;
            END IF;
        END
        $$
        """
    )
    op.execute(f"ALTER TABLE {TABLE_NAME} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE_NAME} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation_{TABLE_NAME} ON {TABLE_NAME} USING ({_NULLIF})"
    )
    op.execute(
        f"CREATE POLICY maintenance_select_{TABLE_NAME} ON {TABLE_NAME} "
        "FOR SELECT TO cerebro_maint USING (true)"
    )
    op.execute(
        f"CREATE POLICY maintenance_update_{TABLE_NAME} ON {TABLE_NAME} "
        "FOR UPDATE TO cerebro_maint USING (true)"
    )
    op.execute(
        f"GRANT SELECT, UPDATE (last_used_at) ON {TABLE_NAME} TO cerebro_maint"
    )
    op.execute("GRANT SELECT ON users TO cerebro_maint")


def downgrade() -> None:
    if _is_postgresql():
        op.execute(f"DROP POLICY IF EXISTS maintenance_update_{TABLE_NAME} ON {TABLE_NAME}")
        op.execute(f"DROP POLICY IF EXISTS maintenance_select_{TABLE_NAME} ON {TABLE_NAME}")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{TABLE_NAME} ON {TABLE_NAME}")
        op.execute(f"REVOKE ALL ON {TABLE_NAME} FROM cerebro_maint")
        op.execute("REVOKE SELECT ON users FROM cerebro_maint")

    op.drop_index("ix_personal_access_tokens_user_id", table_name=TABLE_NAME)
    op.drop_index("ix_personal_access_tokens_org_user", table_name=TABLE_NAME)
    op.drop_index("uq_personal_access_tokens_token_hash", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
