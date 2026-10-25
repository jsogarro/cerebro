"""Index the columns the report repositories filter and sort by.

Revision ID: d9a3c5e7b1f4
Revises: a1b2c3d4e5f6
Create Date: 2026-10-25

``get_by_project_id`` / ``get_by_user_id`` filter by tenant plus project or
user and order by ``created_at``; format lookups and the ``report_formats``
foreign key use ``report_id``.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9a3c5e7b1f4"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the report filter indexes."""
    op.create_index(
        "idx_generated_reports_org_project_created",
        "generated_reports",
        ["organization_id", "project_id", "created_at"],
    )
    op.create_index(
        "idx_generated_reports_org_user_created",
        "generated_reports",
        ["organization_id", "user_id", "created_at"],
    )
    op.create_index("ix_report_formats_report_id", "report_formats", ["report_id"])


def downgrade() -> None:
    """Drop the report filter indexes."""
    op.drop_index("ix_report_formats_report_id", table_name="report_formats")
    op.drop_index(
        "idx_generated_reports_org_user_created", table_name="generated_reports"
    )
    op.drop_index(
        "idx_generated_reports_org_project_created", table_name="generated_reports"
    )
