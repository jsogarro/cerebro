"""Add durable run lifecycle, event stream, and outbox tables

Revision ID: c3f5a1d7b209
Revises: b7a9c2d4e8f1
Create Date: 2026-08-04 10:00:00.000000

Creates the authoritative persistence for runs, tasks, attempts, and the
configuration snapshot a run is admitted under, plus an append-only event
stream and the transactional outbox that carries those events to consumers.

New tables mirror the tenant posture of the existing tenant-owned tables: a
nullable ``organization_id`` boundary column and a Postgres row-level security
policy keyed on ``app.current_org_id``.

The event stream and configuration snapshots are enforced append-only by
database triggers, so a direct UPDATE or DELETE fails even when it bypasses
the ORM.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f5a1d7b209"
down_revision: str | Sequence[str] | None = "b7a9c2d4e8f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES = ("agent_run_events", "agent_run_config_snapshots")
TENANT_SCOPED_TABLES = (
    "agent_runs",
    "agent_run_tasks",
    "agent_task_attempts",
    "agent_run_config_snapshots",
    "agent_run_events",
    "agent_run_event_outbox",
)


def _is_postgresql() -> bool:
    """Return whether the active Alembic bind is PostgreSQL."""
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def _audit_columns() -> list[sa.Column]:
    """Return the shared audit columns every BaseModel-derived table carries."""
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("updated_by", sa.String(255), nullable=True),
    ]


def _lifecycle_columns() -> list[sa.Column]:
    """Return the timestamp and reason columns shared by all lifecycle records."""
    return [
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column(
            "cancellation_requested_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
    ]


def _tenant_column() -> sa.Column:
    return sa.Column(
        "organization_id",
        sa.UUID(),
        nullable=True,
        comment="Tenant organization boundary identifier",
    )


def _contract_version_column() -> sa.Column:
    return sa.Column(
        "contract_schema_version",
        sa.String(20),
        nullable=False,
        server_default="1.0",
    )


def _create_runs() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=False),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        _tenant_column(),
        sa.Column("workflow_definition_id", sa.String(255), nullable=False),
        sa.Column("workflow_definition_version", sa.String(100), nullable=False),
        sa.Column("routing_policy_id", sa.String(255), nullable=False),
        sa.Column("routing_policy_version", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("requested_by", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *_lifecycle_columns(),
        sa.Column(
            "last_event_sequence",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
            comment="High-water mark used to allocate the next run event sequence",
        ),
        _contract_version_column(),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_agent_run_tenant_idempotency"
        ),
        sa.CheckConstraint(
            "status IN ('cancelled', 'cancelling', 'created', 'failed', "
            "'queued', 'running', 'succeeded')",
            name="ck_agent_run_status",
        ),
        sa.CheckConstraint(
            "last_event_sequence >= 0", name="ck_agent_run_sequence_non_negative"
        ),
    )
    op.create_index("ix_agent_runs_deleted_at", "agent_runs", ["deleted_at"])
    op.create_index("ix_agent_runs_tenant_id", "agent_runs", ["tenant_id"])
    op.create_index("ix_agent_runs_organization_id", "agent_runs", ["organization_id"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_index(
        "idx_agent_run_org_status",
        "agent_runs",
        ["organization_id", "status", "created_at"],
    )
    op.create_index(
        "idx_agent_run_tenant_status",
        "agent_runs",
        ["tenant_id", "status", "created_at"],
    )


def _create_tasks() -> None:
    op.create_table(
        "agent_run_tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.String(255), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=False),
        _tenant_column(),
        sa.Column("task_key", sa.String(255), nullable=False),
        sa.Column("task_type", sa.String(255), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("dependency_ids", sa.JSON(), nullable=False),
        sa.Column("assigned_worker_type", sa.String(255), nullable=True),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *_lifecycle_columns(),
        _contract_version_column(),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"]),
        sa.UniqueConstraint(
            "run_id", "idempotency_key", name="uq_agent_run_task_idempotency"
        ),
        sa.UniqueConstraint("run_id", "task_key", name="uq_agent_run_task_key"),
        sa.CheckConstraint(
            "status IN ('cancelled', 'cancelling', 'failed', 'pending', "
            "'ready', 'running', 'skipped', 'succeeded')",
            name="ck_agent_run_task_status",
        ),
    )
    op.create_index("ix_agent_run_tasks_deleted_at", "agent_run_tasks", ["deleted_at"])
    op.create_index("ix_agent_run_tasks_run_id", "agent_run_tasks", ["run_id"])
    op.create_index(
        "ix_agent_run_tasks_organization_id", "agent_run_tasks", ["organization_id"]
    )
    op.create_index("ix_agent_run_tasks_status", "agent_run_tasks", ["status"])
    op.create_index(
        "idx_agent_run_task_run_status", "agent_run_tasks", ["run_id", "status"]
    )
    op.create_index(
        "idx_agent_run_task_org_status",
        "agent_run_tasks",
        ["organization_id", "status", "created_at"],
    )


def _create_attempts() -> None:
    op.create_table(
        "agent_task_attempts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("attempt_id", sa.String(255), nullable=False),
        sa.Column("task_id", sa.String(255), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=False),
        _tenant_column(),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("executor_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        *_lifecycle_columns(),
        sa.Column(
            "journaled_result",
            sa.JSON(),
            nullable=True,
            comment="Nondeterministic attempt output replayed instead of recomputed",
        ),
        _contract_version_column(),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id"),
        sa.ForeignKeyConstraint(["task_id"], ["agent_run_tasks.task_id"]),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"]),
        sa.UniqueConstraint(
            "task_id", "idempotency_key", name="uq_agent_task_attempt_idempotency"
        ),
        sa.UniqueConstraint("task_id", "ordinal", name="uq_agent_task_attempt_ordinal"),
        sa.CheckConstraint("ordinal >= 1", name="ck_agent_task_attempt_ordinal"),
        sa.CheckConstraint(
            "status IN ('cancelled', 'cancelling', 'created', 'failed', "
            "'running', 'succeeded', 'timed_out')",
            name="ck_agent_task_attempt_status",
        ),
    )
    op.create_index(
        "ix_agent_task_attempts_deleted_at", "agent_task_attempts", ["deleted_at"]
    )
    op.create_index(
        "ix_agent_task_attempts_task_id", "agent_task_attempts", ["task_id"]
    )
    op.create_index("ix_agent_task_attempts_run_id", "agent_task_attempts", ["run_id"])
    op.create_index(
        "ix_agent_task_attempts_organization_id",
        "agent_task_attempts",
        ["organization_id"],
    )
    op.create_index("ix_agent_task_attempts_status", "agent_task_attempts", ["status"])
    op.create_index(
        "idx_agent_task_attempt_run_status", "agent_task_attempts", ["run_id", "status"]
    )
    op.create_index(
        "idx_agent_task_attempt_org_status",
        "agent_task_attempts",
        ["organization_id", "status", "created_at"],
    )


def _create_config_snapshots() -> None:
    op.create_table(
        "agent_run_config_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("config_snapshot_id", sa.String(255), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=False),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        _tenant_column(),
        sa.Column("authority_id", sa.String(255), nullable=False),
        sa.Column("authority_version", sa.String(100), nullable=False),
        sa.Column("workflow_definition_id", sa.String(255), nullable=False),
        sa.Column("workflow_definition_version", sa.String(100), nullable=False),
        sa.Column("routing_policy_id", sa.String(255), nullable=False),
        sa.Column("routing_policy_version", sa.String(100), nullable=False),
        sa.Column(
            "pinned_versions",
            sa.JSON(),
            nullable=False,
            comment="Serialized PinnedVersions contract: prompt, evaluator, "
            "artifact-schema, policy, and tool versions pinned for this run",
        ),
        sa.Column(
            "configuration",
            sa.JSON(),
            nullable=False,
            comment="Frozen compiled execution configuration for this run",
        ),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        _contract_version_column(),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("config_snapshot_id"),
        sa.UniqueConstraint("run_id"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"]),
        sa.CheckConstraint(
            "length(content_sha256) = 64", name="ck_agent_run_config_snapshot_digest"
        ),
    )
    op.create_index(
        "ix_agent_run_config_snapshots_organization_id",
        "agent_run_config_snapshots",
        ["organization_id"],
    )
    op.create_index(
        "idx_agent_run_config_snapshot_authority",
        "agent_run_config_snapshots",
        ["authority_id", "created_at"],
    )


def _create_events() -> None:
    op.create_table(
        "agent_run_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.String(255), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=False),
        _tenant_column(),
        sa.Column("aggregate_type", sa.String(255), nullable=False),
        sa.Column("aggregate_id", sa.String(255), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(255), nullable=False),
        sa.Column("event_type_version", sa.String(100), nullable=False),
        sa.Column(
            "envelope_schema_version",
            sa.String(20),
            nullable=False,
            server_default="1.0",
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="When the database accepted the event, distinct from occurred_at",
        ),
        sa.Column("producer", sa.String(255), nullable=False),
        sa.Column("deduplication_key", sa.String(512), nullable=False),
        sa.Column("correlation_id", sa.String(255), nullable=True),
        sa.Column("causation_event_id", sa.String(255), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"]),
        sa.UniqueConstraint("run_id", "sequence", name="uq_agent_run_event_sequence"),
        sa.UniqueConstraint(
            "run_id", "deduplication_key", name="uq_agent_run_event_dedup"
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_agent_run_event_sequence"),
    )
    op.create_index(
        "ix_agent_run_events_organization_id", "agent_run_events", ["organization_id"]
    )
    op.create_index(
        "idx_agent_run_event_org_cursor",
        "agent_run_events",
        ["organization_id", "run_id", "sequence"],
    )
    op.create_index(
        "idx_agent_run_event_type", "agent_run_events", ["event_type", "occurred_at"]
    )


def _create_outbox() -> None:
    op.create_table(
        "agent_run_event_outbox",
        sa.Column(
            "id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
            comment="Monotonic relay cursor; drains in insertion order",
        ),
        sa.Column("event_id", sa.String(255), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=False),
        _tenant_column(),
        sa.Column("destination", sa.String(100), nullable=False),
        sa.Column(
            "idempotency_key",
            sa.String(512),
            nullable=False,
            comment="Stable key consumers dedupe redeliveries on",
        ),
        sa.Column(
            "payload",
            sa.JSON(),
            nullable=False,
            comment="Serialized envelope, so the relay needs no join to deliver",
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="Earliest time the relay may claim this row; backoff moves it out",
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(255), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["event_id"], ["agent_run_events.event_id"]),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"]),
        sa.UniqueConstraint(
            "event_id", "destination", name="uq_agent_run_event_outbox_destination"
        ),
        sa.CheckConstraint(
            "status IN ('delivered', 'failed', 'in_flight', 'pending')",
            name="ck_agent_run_event_outbox_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_agent_run_event_outbox_attempts"),
        sa.CheckConstraint(
            "status <> 'delivered' OR delivered_at IS NOT NULL",
            name="ck_agent_run_event_outbox_delivered_at",
        ),
    )
    op.create_index(
        "ix_agent_run_event_outbox_run_id", "agent_run_event_outbox", ["run_id"]
    )
    op.create_index(
        "ix_agent_run_event_outbox_organization_id",
        "agent_run_event_outbox",
        ["organization_id"],
    )
    op.create_index(
        "idx_agent_run_event_outbox_claimable",
        "agent_run_event_outbox",
        ["status", "available_at", "id"],
    )


def _install_append_only_triggers() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_append_only_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Table % is append-only', TG_TABLE_NAME
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()
            """
        )


def _enable_tenant_rls() -> None:
    for table in TENANT_SCOPED_TABLES:
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
    """Create the durable run lifecycle, event stream, and outbox tables."""
    _create_runs()
    _create_tasks()
    _create_attempts()
    _create_config_snapshots()
    _create_events()
    _create_outbox()

    op.add_column(
        "workflow_checkpoints",
        sa.Column("run_id", sa.String(255), nullable=True),
    )
    op.create_foreign_key(
        "fk_workflow_checkpoints_run_id",
        "workflow_checkpoints",
        "agent_runs",
        ["run_id"],
        ["run_id"],
    )
    op.create_index(
        "ix_workflow_checkpoints_run_id", "workflow_checkpoints", ["run_id"]
    )

    if _is_postgresql():
        _install_append_only_triggers()
        _enable_tenant_rls()


def downgrade() -> None:
    """Remove the durable run lifecycle, event stream, and outbox tables."""
    if _is_postgresql():
        for table in TENANT_SCOPED_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        for table in APPEND_ONLY_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
        op.execute("DROP FUNCTION IF EXISTS reject_append_only_mutation()")

    op.drop_index("ix_workflow_checkpoints_run_id", table_name="workflow_checkpoints")
    op.drop_constraint(
        "fk_workflow_checkpoints_run_id", "workflow_checkpoints", type_="foreignkey"
    )
    op.drop_column("workflow_checkpoints", "run_id")

    op.drop_table("agent_run_event_outbox")
    op.drop_table("agent_run_events")
    op.drop_table("agent_run_config_snapshots")
    op.drop_table("agent_task_attempts")
    op.drop_table("agent_run_tasks")
    op.drop_table("agent_runs")
