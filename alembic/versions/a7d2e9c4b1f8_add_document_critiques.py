"""Add document critiques and their findings.

Revision ID: a7d2e9c4b1f8
Revises: e4b7c1a9d6f2
Create Date: 2026-11-26

Both tables cascade from ``uploaded_documents`` in the database, since erasure
uses core deletes. The tenant RLS policy is posture only (the app role owns the
tables); the repositories' owner filter is the real boundary.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7d2e9c4b1f8"
down_revision: str | Sequence[str] | None = "e4b7c1a9d6f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("document_critiques", "critique_findings")


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
    op.create_table(
        "document_critiques",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("status_reason", sa.String(40), nullable=True),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("prompt_versions", sa.JSON(), nullable=False),
        sa.Column("consent_text_version", sa.String(50), nullable=False),
        sa.Column("consent_text_sha256", sa.String(64), nullable=False),
        sa.Column("consent_recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_units", sa.Integer(), nullable=False),
        sa.Column("role_outcomes", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["uploaded_documents.id"],
            name="fk_document_critiques_document_id_uploaded_documents",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'partial', 'failed')",
            name="ck_document_critiques_status",
        ),
        sa.CheckConstraint(
            "status_reason IS NULL OR status_reason IN ('deadline_exceeded', "
            "'interrupted', 'internal_error', 'all_roles_failed', 'tracing_enabled')",
            name="ck_document_critiques_status_reason",
        ),
    )
    op.create_index(
        "ix_document_critiques_document", "document_critiques", ["document_id"]
    )
    op.create_index(
        "ix_document_critiques_org_status",
        "document_critiques",
        ["organization_id", "status"],
    )

    # Global budget and count checks filter by kind and window across every org.
    op.create_index(
        "ix_usage_ledger_kind_created",
        "usage_ledger",
        ["kind", "created_at"],
        postgresql_include=["units"],
    )

    op.create_table(
        "critique_findings",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("critique_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("quote_enc", sa.Text(), nullable=False),
        sa.Column("raw_start", sa.Integer(), nullable=False),
        sa.Column("raw_end", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("match_kind", sa.String(12), nullable=False),
        sa.Column("second_quote_enc", sa.Text(), nullable=True),
        sa.Column("second_raw_start", sa.Integer(), nullable=True),
        sa.Column("second_raw_end", sa.Integer(), nullable=True),
        sa.Column("second_page", sa.Integer(), nullable=True),
        sa.Column("problem_enc", sa.Text(), nullable=False),
        sa.Column("fix_enc", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["critique_id"],
            ["document_critiques.id"],
            name="fk_critique_findings_critique_id_document_critiques",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "role IN ('methods', 'statistics', 'claims_evidence', 'citations', 'red_team')",
            name="ck_critique_findings_role",
        ),
        sa.CheckConstraint(
            "severity IN ('critical', 'major', 'minor')",
            name="ck_critique_findings_severity",
        ),
        sa.CheckConstraint(
            "match_kind IN ('exact', 'normalized')",
            name="ck_critique_findings_match_kind",
        ),
    )
    op.create_index(
        "ix_critique_findings_critique", "critique_findings", ["critique_id"]
    )

    if op.get_bind().dialect.name == "postgresql":
        _enable_tenant_rls()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.drop_table("critique_findings")
    op.drop_index("ix_usage_ledger_kind_created", table_name="usage_ledger")
    op.drop_table("document_critiques")
