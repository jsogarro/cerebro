"""Align the experiment tables with the ORM models.

Revision ID: f3b8a6d2c4e1
Revises: a1b2c3d4e5f6
Create Date: 2026-10-25

The original migration left ten ``experiments`` columns nullable although the
model declares them NOT NULL, created timestamps naive and nullable without the
``now()`` default the model sets, and omitted the ``deleted_at`` indexes. This
revision backfills NULLs with the model defaults, then applies the model's
shape. Naive timestamps are read as UTC.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3b8a6d2c4e1"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "experiments",
    "experiment_variants",
    "experiment_assignments",
    "experiment_results",
    "experiment_analyses",
)

# column -> SQL literal of the model's Python-side default
EXPERIMENT_DEFAULTS = {
    "status": "'draft'",
    "allocation_strategy": "'random'",
    "traffic_percentage": "100.0",
    "target_segments": "'{}'::json",
    "config": "'{}'::json",
    "metrics": "'[]'::json",
    "success_criteria": "'{}'::json",
    "min_sample_size": "1000",
    "confidence_level": "0.95",
    "power": "0.8",
}

TIMESTAMPTZ = sa.DateTime(timezone=True)
TIMESTAMP = sa.DateTime()


def upgrade() -> None:
    """Apply the model's nullability, defaults, timezones and indexes."""
    for column, default in EXPERIMENT_DEFAULTS.items():
        op.execute(
            f"UPDATE experiments SET {column} = {default} WHERE {column} IS NULL"
        )
        op.alter_column("experiments", column, nullable=False)

    for table in TABLES:
        for column in ("created_at", "updated_at"):
            op.execute(f"UPDATE {table} SET {column} = now() WHERE {column} IS NULL")
            op.alter_column(
                table,
                column,
                type_=TIMESTAMPTZ,
                postgresql_using=f"{column} AT TIME ZONE 'UTC'",
                server_default=sa.text("now()"),
                nullable=False,
            )
        op.alter_column(
            table,
            "deleted_at",
            type_=TIMESTAMPTZ,
            postgresql_using="deleted_at AT TIME ZONE 'UTC'",
        )
        op.create_index(f"ix_{table}_deleted_at", table, ["deleted_at"])


def downgrade() -> None:
    """Restore the original nullable, naive, default-less columns."""
    for table in TABLES:
        op.drop_index(f"ix_{table}_deleted_at", table_name=table)
        for column in ("created_at", "updated_at", "deleted_at"):
            op.alter_column(
                table,
                column,
                type_=TIMESTAMP,
                postgresql_using=f"{column} AT TIME ZONE 'UTC'",
                server_default=None,
                nullable=True,
            )

    for column in EXPERIMENT_DEFAULTS:
        op.alter_column("experiments", column, nullable=True)
