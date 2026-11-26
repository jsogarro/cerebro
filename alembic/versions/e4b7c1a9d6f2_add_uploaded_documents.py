"""Add uploaded documents and the usage ledger.

Revision ID: e4b7c1a9d6f2
Revises: d9a3c5e7b1f4
Create Date: 2026-11-26

Both tables are owner-private. The tenant RLS policy is posture only (the app
role owns the tables); the repositories' owner filter is the real boundary.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4b7c1a9d6f2"
down_revision: str | Sequence[str] | None = "d9a3c5e7b1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("uploaded_documents", "usage_ledger")


def _enable_tenant_rls() -> None:
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table}
            ON {table}
            USING (
                organization_id = current_setting('app.current_org_id', true)::uuid
            )
            """
        )


def upgrade() -> None:
    """Create both tables, their CHECKs and indexes, and the RLS policies."""
    op.create_table(
        "uploaded_documents",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("filename_enc", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(20), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("pages", sa.JSON(), nullable=False),
        sa.Column("extractor_version", sa.String(100), nullable=False),
        sa.Column("invisible_chars_removed", sa.Integer(), nullable=False),
        sa.Column("text_enc", sa.Text(), nullable=False),
        sa.Column("declared_authors_enc", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_uploaded_documents_user_id_users",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "media_type IN ('pdf', 'markdown', 'plain')",
            name="ck_uploaded_documents_media_type",
        ),
        sa.CheckConstraint("char_count > 0", name="ck_uploaded_documents_char_count"),
    )
    op.create_index(
        "ix_uploaded_documents_owner_created",
        "uploaded_documents",
        ["organization_id", "user_id", "created_at"],
    )
    op.create_index(
        "ix_uploaded_documents_expires_at", "uploaded_documents", ["expires_at"]
    )

    op.create_table(
        "usage_ledger",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_usage_ledger_user_id_users",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "kind IN ('upload', 'critique', 'similarity_check', "
            "'similarity_open_sources', 'revision')",
            name="ck_usage_ledger_kind",
        ),
    )
    op.create_index(
        "ix_usage_ledger_owner_kind_created",
        "usage_ledger",
        ["organization_id", "user_id", "kind", "created_at"],
    )

    if op.get_bind().dialect.name == "postgresql":
        _enable_tenant_rls()


def downgrade() -> None:
    """Drop the policies, then the tables."""
    if op.get_bind().dialect.name == "postgresql":
        for table in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.drop_table("usage_ledger")
    op.drop_table("uploaded_documents")
