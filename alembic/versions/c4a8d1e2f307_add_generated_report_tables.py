"""Add durable generated report and report format tables.

Revision ID: c4a8d1e2f307
Revises: 7b1e4c9d2a08
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a8d1e2f307"
down_revision: str | Sequence[str] | None = "7b1e4c9d2a08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the report tables and their tenant/query indexes."""
    op.create_table(
        "generated_reports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("workflow_id", sa.String(length=255), nullable=True),
        sa.Column(
            "user_id",
            sa.UUID(),
            nullable=True,
        ),
        sa.Column(
            "organization_id",
            sa.UUID(),
            nullable=False,
            comment="Tenant organization boundary identifier",
        ),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("report_type", sa.String(length=50), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("domains", sa.JSON(), nullable=True),
        sa.Column("configuration", sa.JSON(), nullable=True),
        sa.Column("formats_generated", sa.JSON(), nullable=True),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("total_sources", sa.Integer(), nullable=False),
        sa.Column("total_citations", sa.Integer(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("generation_status", sa.String(length=50), nullable=False),
        sa.Column("generation_started_at", sa.DateTime(), nullable=True),
        sa.Column("generation_completed_at", sa.DateTime(), nullable=True),
        sa.Column("generation_time_seconds", sa.Float(), nullable=True),
        sa.Column("generation_errors", sa.JSON(), nullable=True),
        sa.Column("agents_used", sa.JSON(), nullable=True),
        sa.Column("storage_path", sa.String(length=1000), nullable=True),
        sa.Column("file_sizes", sa.JSON(), nullable=True),
        sa.Column("executive_summary", sa.Text(), nullable=True),
        sa.Column("content_preview", sa.Text(), nullable=True),
        sa.Column("key_findings", sa.JSON(), nullable=True),
        sa.Column("is_public", sa.Boolean(), nullable=False),
        sa.Column("access_count", sa.Integer(), nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["research_projects.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_generated_reports_deleted_at",
        "generated_reports",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_generated_reports_generation_status",
        "generated_reports",
        ["generation_status"],
        unique=False,
    )
    op.create_index(
        "ix_generated_reports_is_public",
        "generated_reports",
        ["is_public"],
        unique=False,
    )
    op.create_index(
        "ix_generated_reports_organization_id",
        "generated_reports",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_generated_reports_quality_score",
        "generated_reports",
        ["quality_score"],
        unique=False,
    )
    op.create_index(
        "ix_generated_reports_report_type",
        "generated_reports",
        ["report_type"],
        unique=False,
    )
    op.create_index(
        "ix_generated_reports_title",
        "generated_reports",
        ["title"],
        unique=False,
    )
    op.create_index(
        "ix_generated_reports_workflow_id",
        "generated_reports",
        ["workflow_id"],
        unique=False,
    )

    op.create_table(
        "report_formats",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("report_id", sa.UUID(), nullable=False),
        sa.Column("format_type", sa.String(length=20), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("file_extension", sa.String(length=10), nullable=False),
        sa.Column("encoding", sa.String(length=20), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("file_hash", sa.String(length=64), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("content_binary", sa.LargeBinary(), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.Column("generation_time_ms", sa.Integer(), nullable=True),
        sa.Column("generator_version", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(
            ["report_id"], ["generated_reports.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_report_formats_deleted_at",
        "report_formats",
        ["deleted_at"],
        unique=False,
    )
    op.create_index(
        "ix_report_formats_format_type",
        "report_formats",
        ["format_type"],
        unique=False,
    )


def downgrade() -> None:
    """Drop report formats before their generated report parents."""
    op.drop_index("ix_report_formats_format_type", table_name="report_formats")
    op.drop_index("ix_report_formats_deleted_at", table_name="report_formats")
    op.drop_table("report_formats")

    op.drop_index("ix_generated_reports_workflow_id", table_name="generated_reports")
    op.drop_index("ix_generated_reports_title", table_name="generated_reports")
    op.drop_index("ix_generated_reports_report_type", table_name="generated_reports")
    op.drop_index("ix_generated_reports_quality_score", table_name="generated_reports")
    op.drop_index(
        "ix_generated_reports_organization_id", table_name="generated_reports"
    )
    op.drop_index("ix_generated_reports_is_public", table_name="generated_reports")
    op.drop_index(
        "ix_generated_reports_generation_status", table_name="generated_reports"
    )
    op.drop_index("ix_generated_reports_deleted_at", table_name="generated_reports")
    op.drop_table("generated_reports")
