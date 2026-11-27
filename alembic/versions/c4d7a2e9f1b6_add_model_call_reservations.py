"""Add the model-call journal.

Revision ID: c4d7a2e9f1b6
Revises: c5a9e2f7d1b4
Create Date: 2026-11-27

Additive. Rows record what a governed owner may have spent on external model
calls, so a crashed owner's uncertain spend survives it. The tenant RLS policy
is posture only (the app role owns the tables); the repository's organization
filter is the real boundary. Rollback must preserve populated rows: disable the
journal's callers and roll forward instead of dropping the table.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d7a2e9f1b6"
down_revision: str | Sequence[str] | None = "c5a9e2f7d1b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_call_reservations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("call_id", sa.String(64), nullable=False),
        sa.Column("owner_kind", sa.String(20), nullable=False),
        sa.Column("owner_id", sa.String(255), nullable=False),
        sa.Column("attempt_ref", sa.String(255), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), nullable=True),
        sa.Column("price_digest", sa.String(64), nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.Column("reserved_cost_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("dispatch_state", sa.String(20), nullable=False),
        sa.Column("settlement_state", sa.String(20), nullable=False),
        sa.Column("actual_tokens", sa.Integer(), nullable=True),
        sa.Column("actual_cost_usd", sa.Numeric(18, 8), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "organization_id", "call_id", name="uq_model_call_reservations_call"
        ),
        sa.CheckConstraint(
            "owner_kind IN ('research_run', 'evaluation_job', 'document_job')",
            name="ck_model_call_reservations_owner_kind",
        ),
        sa.CheckConstraint(
            "dispatch_state IN ('reserved', 'may_have_sent')",
            name="ck_model_call_reservations_dispatch_state",
        ),
        sa.CheckConstraint(
            "settlement_state IN ('open', 'settled_known', 'settled_unknown', "
            "'released_unsent')",
            name="ck_model_call_reservations_settlement_state",
        ),
        sa.CheckConstraint(
            "reserved_tokens >= 0 AND reserved_cost_usd >= 0",
            name="ck_model_call_reservations_reserved_non_negative",
        ),
        sa.CheckConstraint(
            "(settlement_state = 'settled_known' AND actual_tokens IS NOT NULL "
            "AND actual_cost_usd IS NOT NULL) OR (settlement_state <> 'settled_known' "
            "AND actual_tokens IS NULL AND actual_cost_usd IS NULL)",
            name="ck_model_call_reservations_known_usage",
        ),
        sa.CheckConstraint(
            "(actual_tokens IS NULL OR actual_tokens >= 0) "
            "AND (actual_cost_usd IS NULL OR actual_cost_usd >= 0)",
            name="ck_model_call_reservations_actual_non_negative",
        ),
        sa.CheckConstraint(
            "settlement_state <> 'released_unsent' OR dispatch_state = 'reserved'",
            name="ck_model_call_reservations_unsent_release",
        ),
        sa.CheckConstraint(
            "(settlement_state = 'open' AND settled_at IS NULL) "
            "OR (settlement_state <> 'open' AND settled_at IS NOT NULL)",
            name="ck_model_call_reservations_settled_at",
        ),
    )
    op.create_index(
        "ix_model_call_reservations_owner",
        "model_call_reservations",
        ["organization_id", "owner_kind", "owner_id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE model_call_reservations ENABLE ROW LEVEL SECURITY")
        # NULLIF: a bare ::uuid cast fails every query once the setting reads ''.
        op.execute(
            """
            CREATE POLICY tenant_isolation_model_call_reservations
            ON model_call_reservations
            USING (
                organization_id
                = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            )
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP POLICY IF EXISTS tenant_isolation_model_call_reservations "
            "ON model_call_reservations"
        )
    op.drop_table("model_call_reservations")
