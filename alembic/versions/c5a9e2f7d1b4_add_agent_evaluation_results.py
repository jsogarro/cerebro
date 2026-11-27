"""Add the append-only evaluation results table.

Revision ID: c5a9e2f7d1b4
Revises: b8e3f1a5c7d9
Create Date: 2026-11-27

Append-only is enforced by the shared ``reject_append_only_mutation()`` trigger
function, which an earlier revision created. The tenant RLS policy is posture
only (the app role owns the table); the repository's organization filter is the
real boundary.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5a9e2f7d1b4"
down_revision: str | Sequence[str] | None = "b8e3f1a5c7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "agent_evaluation_results"


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("evaluation_result_id", sa.String(255), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=False),
        sa.Column("task_id", sa.String(255), nullable=True),
        sa.Column("artifact_id", sa.String(255), nullable=True),
        sa.Column(
            "organization_id",
            sa.UUID(),
            nullable=True,
            comment="Tenant organization boundary identifier",
        ),
        sa.Column("evaluator_id", sa.String(255), nullable=False),
        sa.Column("evaluator_version", sa.String(100), nullable=False),
        sa.Column("dimension", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("deterministic", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("error_code", sa.String(255), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("rubric_sha256", sa.String(64), nullable=True),
        sa.Column("judge_model_id", sa.String(255), nullable=True),
        sa.Column("dataset_version", sa.String(160), nullable=True),
        sa.Column("subject_id", sa.String(255), nullable=True),
        sa.Column("input_sha256", sa.String(64), nullable=True),
        sa.Column("trigger", sa.String(255), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="When the database accepted the row, distinct from completed_at",
        ),
        sa.Column(
            "contract_schema_version",
            sa.String(20),
            nullable=False,
            server_default="1.0",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "evaluation_result_id",
            name="uq_agent_evaluation_result_org_id",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"]),
        sa.ForeignKeyConstraint(["task_id"], ["agent_run_tasks.task_id"]),
        sa.ForeignKeyConstraint(["artifact_id"], ["agent_artifacts.artifact_id"]),
        sa.CheckConstraint(
            "status IN ('abstained', 'error', 'failed', 'passed')",
            name="ck_agent_evaluation_result_status",
        ),
        sa.CheckConstraint(
            "status NOT IN ('error', 'abstained') "
            "OR (error_code IS NOT NULL AND length(error_code) > 0)",
            name="ck_agent_evaluation_result_non_verdict_requires_reason",
        ),
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name="ck_agent_evaluation_result_score_range",
        ),
        sa.CheckConstraint(
            "NOT deterministic OR (judge_model_id IS NULL AND rubric_sha256 IS NULL)",
            name="ck_agent_evaluation_result_deterministic_has_no_judge",
        ),
        sa.CheckConstraint(
            "(judge_model_id IS NULL) = (rubric_sha256 IS NULL)",
            name="ck_agent_evaluation_result_judge_fields_together",
        ),
        sa.CheckConstraint(
            "length(trigger) > 0",
            name="ck_agent_evaluation_result_trigger_non_empty",
        ),
    )
    op.create_index(f"ix_{TABLE}_run_id", TABLE, ["run_id"])
    op.create_index(f"ix_{TABLE}_organization_id", TABLE, ["organization_id"])
    op.create_index(
        "idx_agent_evaluation_result_org_run",
        TABLE,
        ["organization_id", "run_id", "completed_at"],
    )
    op.create_index(
        "idx_agent_evaluation_result_cache",
        TABLE,
        ["organization_id", "evaluator_id", "evaluator_version", "input_sha256"],
    )

    if _is_postgresql():
        op.execute(
            f"""
            CREATE TRIGGER {TABLE}_append_only
            BEFORE UPDATE OR DELETE ON {TABLE}
            FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()
            """
        )
        op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
        # NULLIF: once a transaction that set the tenant ends, the setting reads
        # back as '' and a bare ::uuid cast would fail every later query.
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{TABLE}
            ON {TABLE}
            USING (
                organization_id
                = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            )
            """
        )


def downgrade() -> None:
    if _is_postgresql():
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{TABLE} ON {TABLE}")
        op.execute(f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY")
        op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_append_only ON {TABLE}")
    op.drop_table(TABLE)
