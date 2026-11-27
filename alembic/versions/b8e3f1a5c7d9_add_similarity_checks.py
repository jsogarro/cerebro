"""Add similarity checks, their matches and the egress audit rows.

Revision ID: b8e3f1a5c7d9
Revises: b5e1c8d3a9f7
Create Date: 2026-11-26

All three tables cascade or detach in the database, since erasure uses core
deletes. The tenant RLS policy is posture only (the app role owns the tables);
the repositories' owner filter is the real boundary.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e3f1a5c7d9"
down_revision: str | Sequence[str] | None = "b5e1c8d3a9f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("similarity_checks", "similarity_matches", "egress_events")


def _enable_tenant_rls() -> None:
    # NULLIF: after a transaction that set the tenant ends, the setting reads back
    # as '' and a bare ::uuid cast would fail every later query.
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table}
            ON {table}
            USING (
                organization_id
                = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            )
            """
        )


def _enable_purge_policies() -> None:
    # The retention sweep runs without a tenant; it may touch only audit rows old enough
    # that no retention window is shorter, and only when it says it is the sweep.
    for command in ("SELECT", "DELETE"):
        op.execute(
            f"""
            CREATE POLICY purge_expired_{command.lower()}_egress_events
            ON egress_events FOR {command}
            USING (
                created_at <= now() - interval '1 day'
                AND current_setting('app.purge_expired', true) = 'on'
            )
            """
        )


def upgrade() -> None:
    op.create_table(
        "similarity_checks",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("status_reason", sa.String(40), nullable=True),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("consent_text_version", sa.String(50), nullable=True),
        sa.Column("consent_text_sha256", sa.String(64), nullable=True),
        sa.Column("ledger_id", sa.UUID(), nullable=True),
        sa.Column("open_sources_ledger_id", sa.UUID(), nullable=True),
        sa.Column("coverage", sa.JSON(), nullable=False),
        sa.Column("policy_versions", sa.JSON(), nullable=False),
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
            name="fk_similarity_checks_document_id_uploaded_documents",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'partial', 'failed')",
            name="ck_similarity_checks_status",
        ),
        sa.CheckConstraint(
            "status_reason IS NULL OR status_reason IN ('deadline_exceeded', "
            "'interrupted', 'internal_error')",
            name="ck_similarity_checks_status_reason",
        ),
    )
    op.create_index(
        "ix_similarity_checks_document", "similarity_checks", ["document_id"]
    )
    op.create_index(
        "ix_similarity_checks_org_status",
        "similarity_checks",
        ["organization_id", "status"],
    )

    op.create_table(
        "similarity_matches",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("check_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("attribution", sa.String(20), nullable=False),
        sa.Column("from_user_upload", sa.Boolean(), nullable=False),
        sa.Column("doc_start", sa.Integer(), nullable=False),
        sa.Column("doc_end", sa.Integer(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("cosine", sa.Float(), nullable=True),
        sa.Column("source_origin", sa.String(20), nullable=False),
        sa.Column("source_document_id", sa.UUID(), nullable=True),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("source_title_enc", sa.Text(), nullable=False),
        sa.Column("source_year", sa.Integer(), nullable=True),
        sa.Column("source_licence", sa.JSON(), nullable=False),
        sa.Column("source_text_kind", sa.String(12), nullable=False),
        sa.Column("src_start", sa.Integer(), nullable=False),
        sa.Column("src_end", sa.Integer(), nullable=False),
        sa.Column("source_excerpt_enc", sa.Text(), nullable=True),
        sa.Column("normalizations_applied", sa.JSON(), nullable=False),
        sa.Column("declared_author_overlap", sa.Boolean(), nullable=False),
        sa.Column("section", sa.String(10), nullable=False),
        sa.ForeignKeyConstraint(
            ["check_id"],
            ["similarity_checks.id"],
            name="fk_similarity_matches_check_id_similarity_checks",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["uploaded_documents.id"],
            name="fk_similarity_matches_source_document_id_uploaded_documents",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "kind IN ('verbatim', 'normalized', 'paraphrase_candidate', "
            "'translation_candidate')",
            name="ck_similarity_matches_kind",
        ),
        sa.CheckConstraint(
            "attribution IN ('quoted_and_cited', 'quoted_uncited', "
            "'cited_unquoted', 'unattributed')",
            name="ck_similarity_matches_attribution",
        ),
        sa.CheckConstraint(
            "source_origin IN ('own_upload', 'comparison', 'openalex', 'arxiv')",
            name="ck_similarity_matches_source_origin",
        ),
        sa.CheckConstraint(
            "source_text_kind IN ('full_text', 'abstract')",
            name="ck_similarity_matches_source_text_kind",
        ),
    )
    op.create_index("ix_similarity_matches_check", "similarity_matches", ["check_id"])
    op.create_index(
        "ix_similarity_matches_source_document",
        "similarity_matches",
        ["source_document_id"],
    )

    op.create_table(
        "egress_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("check_id", sa.UUID(), nullable=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("host", sa.String(40), nullable=False),
        sa.Column("request_class", sa.String(10), nullable=False),
        sa.Column("term_count", sa.Integer(), nullable=False),
        sa.Column("bytes_received", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["check_id"],
            ["similarity_checks.id"],
            name="fk_egress_events_check_id_similarity_checks",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "host IN ('api.openalex.org', 'arxiv.org')", name="ck_egress_events_host"
        ),
        sa.CheckConstraint(
            "request_class IN ('search', 'pdf')", name="ck_egress_events_request_class"
        ),
    )
    op.create_index("ix_egress_events_check", "egress_events", ["check_id"])
    op.create_index("ix_egress_events_user", "egress_events", ["user_id"])

    if op.get_bind().dialect.name == "postgresql":
        _enable_tenant_rls()
        _enable_purge_policies()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for command in ("select", "delete"):
            op.execute(
                f"DROP POLICY IF EXISTS purge_expired_{command}_egress_events "
                "ON egress_events"
            )
        for table in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
    op.drop_table("egress_events")
    op.drop_table("similarity_matches")
    op.drop_table("similarity_checks")
