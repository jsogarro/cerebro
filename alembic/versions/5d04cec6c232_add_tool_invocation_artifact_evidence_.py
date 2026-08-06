"""Add tool invocation, artifact, evidence, and capability tables

Revision ID: 5d04cec6c232
Revises: c3f5a1d7b209
Create Date: 2026-08-06 10:22:49.997669

Creates the persistence for the Wave 4 provenance and capability contracts in
``src.core.contracts.provenance`` and ``src.core.contracts.capabilities``:
``agent_capability_grants`` and ``agent_capability_approvals`` (mutable,
task-scoped tool permissions), ``agent_artifacts`` and
``agent_tool_invocations`` (mutable), and ``agent_evidence`` and
``agent_claim_supports`` (append-only, enforced by database trigger exactly
as ``agent_run_events`` and ``agent_run_config_snapshots`` were in
``c3f5a1d7b209`` — this migration reuses that migration's
``reject_append_only_mutation()`` trigger function rather than redefining
it, since by the time this migration runs that function already exists).

Table creation order follows the foreign-key DAG: grants and approvals first
(referenced by tool invocations), then artifacts, then tool invocations
(which reference both), then evidence and claim supports (which reference
artifacts and tool invocations).

New tenant-scoped tables get the same nullable ``organization_id`` boundary
column and row-level security *posture* as every table ``c3f5a1d7b209``
created. Per the accepted Wave 3 tenant identity decision, that RLS is not a
functioning security boundary today — the application connects as the
Postgres table owner, which bypasses row-level security policies entirely —
so it must not be read as enforcement. The actual boundary is the
``organization_id`` filter each repository applies.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5d04cec6c232"
down_revision: str | Sequence[str] | None = "c3f5a1d7b209"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES = ("agent_evidence", "agent_claim_supports")
TENANT_SCOPED_TABLES = (
    "agent_capability_grants",
    "agent_capability_approvals",
    "agent_artifacts",
    "agent_tool_invocations",
    "agent_evidence",
    "agent_claim_supports",
)

PROMPT_BINDING_ALL_OR_NOTHING_SQL = (
    "(prompt_id IS NULL AND prompt_version IS NULL "
    "AND template_sha256 IS NULL AND rendered_sha256 IS NULL) "
    "OR "
    "(prompt_id IS NOT NULL AND prompt_version IS NOT NULL "
    "AND template_sha256 IS NOT NULL AND rendered_sha256 IS NOT NULL)"
)
PRODUCER_KIND_BICONDITIONAL_SQL = (
    "(producer_kind = 'system' AND prompt_id IS NULL) "
    "OR (producer_kind = 'model_turn' AND prompt_id IS NOT NULL)"
)
TRUST_CLASSIFICATION_VALUES_SQL = (
    "'trusted_control', 'application', 'user_supplied', "
    "'external_untrusted', 'derived_untrusted'"
)


def _is_postgresql() -> bool:
    """Return whether the active Alembic bind is PostgreSQL."""
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


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


def _audit_columns() -> list[sa.Column]:
    """Return the shared audit columns every BaseModel-derived table carries."""
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("updated_by", sa.String(255), nullable=True),
    ]


def _optional_prompt_binding_columns() -> list[sa.Column]:
    """Nullable PromptBinding columns plus the producer_kind that gates them."""
    return [
        sa.Column(
            "producer_kind", sa.String(20), nullable=False, server_default="system"
        ),
        sa.Column("prompt_id", sa.String(255), nullable=True),
        sa.Column("prompt_version", sa.String(100), nullable=True),
        sa.Column("template_sha256", sa.String(64), nullable=True),
        sa.Column("rendered_sha256", sa.String(64), nullable=True),
    ]


def _required_prompt_binding_columns() -> list[sa.Column]:
    """NOT NULL PromptBinding columns, for records that always name a prompt."""
    return [
        sa.Column("prompt_id", sa.String(255), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("template_sha256", sa.String(64), nullable=False),
        sa.Column("rendered_sha256", sa.String(64), nullable=False),
    ]


def _create_capability_grants() -> None:
    op.create_table(
        "agent_capability_grants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("grant_id", sa.String(255), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=False),
        sa.Column("task_id", sa.String(255), nullable=False),
        _tenant_column(),
        sa.Column("capability_scope", sa.String(255), nullable=False),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("tool_versions", sa.JSON(), nullable=False),
        sa.Column("sensitivity", sa.String(30), nullable=False),
        sa.Column("max_input_trust", sa.String(30), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _contract_version_column(),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("grant_id"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"]),
        sa.ForeignKeyConstraint(["task_id"], ["agent_run_tasks.task_id"]),
        sa.CheckConstraint(
            "sensitivity IN ('exfiltration', 'external_write', 'internal_write', "
            "'read_only')",
            name="ck_agent_capability_grant_sensitivity",
        ),
        sa.CheckConstraint(
            f"max_input_trust IN ({TRUST_CLASSIFICATION_VALUES_SQL})",
            name="ck_agent_capability_grant_max_input_trust",
        ),
        sa.CheckConstraint(
            "expires_at > issued_at", name="ck_agent_capability_grant_window"
        ),
        sa.CheckConstraint(
            "sensitivity NOT IN ('external_write', 'exfiltration') "
            "OR requires_approval",
            name="ck_agent_capability_grant_approval_required",
        ),
    )
    op.create_index(
        "idx_agent_capability_grant_run_task",
        "agent_capability_grants",
        ["run_id", "task_id", "tool_name"],
    )
    op.create_index(
        "idx_agent_capability_grant_org",
        "agent_capability_grants",
        ["organization_id", "run_id"],
    )
    op.create_index(
        "ix_agent_capability_grants_deleted_at",
        "agent_capability_grants",
        ["deleted_at"],
    )


def _create_capability_approvals() -> None:
    op.create_table(
        "agent_capability_approvals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("approval_id", sa.String(255), nullable=False),
        sa.Column("grant_id", sa.String(255), nullable=False),
        _tenant_column(),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(255), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _contract_version_column(),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_id"),
        sa.ForeignKeyConstraint(["grant_id"], ["agent_capability_grants.grant_id"]),
        sa.UniqueConstraint(
            "grant_id",
            "request_fingerprint",
            name="uq_agent_capability_approval_grant_fingerprint",
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_agent_capability_approval_fingerprint_digest",
        ),
        sa.CheckConstraint(
            "expires_at > approved_at", name="ck_agent_capability_approval_window"
        ),
    )
    op.create_index(
        "idx_agent_capability_approval_org",
        "agent_capability_approvals",
        ["organization_id", "grant_id"],
    )
    op.create_index(
        "ix_agent_capability_approvals_deleted_at",
        "agent_capability_approvals",
        ["deleted_at"],
    )


def _create_artifacts() -> None:
    op.create_table(
        "agent_artifacts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("artifact_id", sa.String(255), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=False),
        sa.Column("task_id", sa.String(255), nullable=True),
        sa.Column("attempt_id", sa.String(255), nullable=True),
        _tenant_column(),
        sa.Column("kind", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("trust", sa.String(30), nullable=False),
        sa.Column("producer", sa.String(255), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        *_optional_prompt_binding_columns(),
        _contract_version_column(),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("artifact_id"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"]),
        sa.ForeignKeyConstraint(["task_id"], ["agent_run_tasks.task_id"]),
        sa.ForeignKeyConstraint(["attempt_id"], ["agent_task_attempts.attempt_id"]),
        sa.CheckConstraint(
            "status IN ('draft', 'final', 'invalidated')",
            name="ck_agent_artifact_status",
        ),
        sa.CheckConstraint(
            f"trust IN ({TRUST_CLASSIFICATION_VALUES_SQL})",
            name="ck_agent_artifact_trust",
        ),
        sa.CheckConstraint(
            "producer_kind IN ('model_turn', 'system')",
            name="ck_agent_artifact_producer_kind",
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64", name="ck_agent_artifact_digest"
        ),
        sa.CheckConstraint(
            PROMPT_BINDING_ALL_OR_NOTHING_SQL,
            name="ck_agent_artifact_prompt_binding_all_or_nothing",
        ),
        sa.CheckConstraint(
            PRODUCER_KIND_BICONDITIONAL_SQL,
            name="ck_agent_artifact_producer_kind_biconditional",
        ),
    )
    op.create_index(
        "idx_agent_artifact_run_task", "agent_artifacts", ["run_id", "task_id"]
    )
    op.create_index(
        "idx_agent_artifact_org_status",
        "agent_artifacts",
        ["organization_id", "status", "created_at"],
    )
    op.create_index("ix_agent_artifacts_deleted_at", "agent_artifacts", ["deleted_at"])


def _create_tool_invocations() -> None:
    op.create_table(
        "agent_tool_invocations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tool_invocation_id", sa.String(255), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=False),
        sa.Column("task_id", sa.String(255), nullable=False),
        sa.Column("attempt_id", sa.String(255), nullable=False),
        _tenant_column(),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column("tool_version", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("capability_scope", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("input_trust", sa.String(30), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("output_trust", sa.String(30), nullable=True),
        sa.Column("output_sha256", sa.String(64), nullable=True),
        sa.Column("capability_decision_effect", sa.String(10), nullable=False),
        sa.Column("capability_grant_id", sa.String(255), nullable=True),
        sa.Column("capability_approval_id", sa.String(255), nullable=True),
        sa.Column("capability_denial_reason", sa.String(64), nullable=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("error_code", sa.String(255), nullable=True),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_optional_prompt_binding_columns(),
        _contract_version_column(),
        *_audit_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tool_invocation_id"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"]),
        sa.ForeignKeyConstraint(["task_id"], ["agent_run_tasks.task_id"]),
        sa.ForeignKeyConstraint(["attempt_id"], ["agent_task_attempts.attempt_id"]),
        sa.ForeignKeyConstraint(
            ["capability_grant_id"], ["agent_capability_grants.grant_id"]
        ),
        sa.ForeignKeyConstraint(
            ["capability_approval_id"], ["agent_capability_approvals.approval_id"]
        ),
        sa.UniqueConstraint(
            "attempt_id",
            "idempotency_key",
            name="uq_agent_tool_invocation_idempotency",
        ),
        sa.CheckConstraint(
            "status IN ('cancelled', 'denied', 'failed', 'requested', 'running', "
            "'succeeded', 'timed_out')",
            name="ck_agent_tool_invocation_status",
        ),
        sa.CheckConstraint(
            f"input_trust IN ({TRUST_CLASSIFICATION_VALUES_SQL})",
            name="ck_agent_tool_invocation_input_trust",
        ),
        sa.CheckConstraint(
            f"output_trust IS NULL OR output_trust IN ({TRUST_CLASSIFICATION_VALUES_SQL})",
            name="ck_agent_tool_invocation_output_trust",
        ),
        sa.CheckConstraint(
            PROMPT_BINDING_ALL_OR_NOTHING_SQL,
            name="ck_agent_tool_invocation_prompt_binding_all_or_nothing",
        ),
        sa.CheckConstraint(
            PRODUCER_KIND_BICONDITIONAL_SQL,
            name="ck_agent_tool_invocation_producer_kind_biconditional",
        ),
        sa.CheckConstraint(
            "capability_decision_effect IN ('allow', 'deny')",
            name="ck_agent_tool_invocation_capability_decision_effect",
        ),
        sa.CheckConstraint(
            "capability_decision_effect = 'allow' OR status = 'denied'",
            name="ck_agent_tool_invocation_capability_denial",
        ),
        sa.CheckConstraint(
            "capability_decision_effect <> 'allow' OR capability_grant_id IS NOT NULL",
            name="ck_agent_tool_invocation_allow_requires_grant",
        ),
        sa.CheckConstraint(
            "capability_decision_effect <> 'deny' "
            "OR capability_denial_reason IS NOT NULL",
            name="ck_agent_tool_invocation_deny_requires_reason",
        ),
        sa.CheckConstraint(
            "capability_decision_effect = 'deny' OR capability_denial_reason IS NULL",
            name="ck_agent_tool_invocation_allow_forbids_reason",
        ),
        sa.CheckConstraint(
            "capability_decision_effect = 'allow' OR capability_approval_id IS NULL",
            name="ck_agent_tool_invocation_deny_forbids_approval",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= requested_at",
            name="ck_agent_tool_invocation_completed_after_requested",
        ),
        sa.CheckConstraint(
            "length(input_sha256) = 64", name="ck_agent_tool_invocation_input_digest"
        ),
        sa.CheckConstraint(
            "output_sha256 IS NULL OR length(output_sha256) = 64",
            name="ck_agent_tool_invocation_output_digest",
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_agent_tool_invocation_request_fingerprint_digest",
        ),
    )
    op.create_index(
        "idx_agent_tool_invocation_run_task_attempt",
        "agent_tool_invocations",
        ["run_id", "task_id", "attempt_id"],
    )
    op.create_index(
        "idx_agent_tool_invocation_org_status",
        "agent_tool_invocations",
        ["organization_id", "status", "created_at"],
    )
    op.create_index(
        "idx_agent_tool_invocation_run_status",
        "agent_tool_invocations",
        ["run_id", "status"],
    )
    op.create_index(
        "idx_agent_tool_invocation_attempt",
        "agent_tool_invocations",
        ["attempt_id", "requested_at"],
    )
    op.create_index(
        "idx_agent_tool_invocation_denied",
        "agent_tool_invocations",
        ["organization_id", "capability_decision_effect", "requested_at"],
    )
    op.create_index(
        "ix_agent_tool_invocations_deleted_at",
        "agent_tool_invocations",
        ["deleted_at"],
    )


def _create_evidence() -> None:
    op.create_table(
        "agent_evidence",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("evidence_id", sa.String(255), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=False),
        sa.Column("task_id", sa.String(255), nullable=False),
        _tenant_column(),
        sa.Column("source_type", sa.String(255), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("snapshot_artifact_id", sa.String(255), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("locator", sa.String(1024), nullable=False),
        sa.Column("locator_scheme", sa.String(16), nullable=False),
        sa.Column("locator_start", sa.BigInteger(), nullable=False),
        sa.Column("locator_end", sa.BigInteger(), nullable=False),
        sa.Column("trust", sa.String(30), nullable=False),
        *_required_prompt_binding_columns(),
        sa.Column("producer_tool_invocation_id", sa.String(255), nullable=True),
        sa.Column("parent_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="When the database accepted the row, distinct from acquired_at",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evidence_id"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"]),
        sa.ForeignKeyConstraint(["task_id"], ["agent_run_tasks.task_id"]),
        sa.ForeignKeyConstraint(
            ["snapshot_artifact_id"], ["agent_artifacts.artifact_id"]
        ),
        sa.ForeignKeyConstraint(
            ["producer_tool_invocation_id"],
            ["agent_tool_invocations.tool_invocation_id"],
        ),
        sa.UniqueConstraint(
            "run_id",
            "snapshot_artifact_id",
            "locator",
            name="uq_agent_evidence_span",
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64", name="ck_agent_evidence_digest"
        ),
        sa.CheckConstraint(
            "length(locator) > 0", name="ck_agent_evidence_locator_present"
        ),
        sa.CheckConstraint(
            "locator_scheme IN ('bytes', 'char', 'line')",
            name="ck_agent_evidence_locator_scheme",
        ),
        sa.CheckConstraint(
            "locator_start >= 0", name="ck_agent_evidence_locator_start_non_negative"
        ),
        sa.CheckConstraint(
            "locator_end > locator_start", name="ck_agent_evidence_locator_half_open"
        ),
        sa.CheckConstraint(
            "locator_scheme <> 'line' OR locator_start >= 1",
            name="ck_agent_evidence_locator_line_one_based",
        ),
        sa.CheckConstraint(
            f"trust IN ({TRUST_CLASSIFICATION_VALUES_SQL})",
            name="ck_agent_evidence_trust",
        ),
    )
    op.create_index(
        "idx_agent_evidence_org_run", "agent_evidence", ["organization_id", "run_id"]
    )


def _create_claim_supports() -> None:
    op.create_table(
        "agent_claim_supports",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("claim_support_id", sa.String(255), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=False),
        sa.Column("artifact_id", sa.String(255), nullable=False),
        sa.Column("claim_id", sa.String(255), nullable=False),
        _tenant_column(),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column(
            "evidence_count",
            sa.Integer(),
            nullable=False,
            comment="len(evidence_ids), computed once by ClaimSupportRepository",
        ),
        sa.Column("absent_evidence_reason", sa.String(30), nullable=True),
        sa.Column("evaluator_id", sa.String(255), nullable=False),
        sa.Column("evaluator_version", sa.String(100), nullable=False),
        *_required_prompt_binding_columns(),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="When the database accepted the row, distinct from evaluated_at",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_support_id"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"]),
        sa.ForeignKeyConstraint(["artifact_id"], ["agent_artifacts.artifact_id"]),
        sa.CheckConstraint(
            "status IN ('disputed', 'partially_supported', 'supported', 'unsupported')",
            name="ck_agent_claim_support_status",
        ),
        sa.CheckConstraint(
            "absent_evidence_reason IS NULL OR absent_evidence_reason IN "
            "('capability_denied', 'no_source_found', 'not_attempted', "
            "'retrieval_failed', 'source_inaccessible')",
            name="ck_agent_claim_support_absent_reason_value",
        ),
        sa.CheckConstraint(
            "evidence_count >= 0",
            name="ck_agent_claim_support_evidence_count_non_negative",
        ),
        sa.CheckConstraint(
            "status NOT IN ('supported', 'partially_supported', 'disputed') "
            "OR evidence_count > 0",
            name="ck_agent_claim_support_evidence_bearing_requires_evidence",
        ),
        sa.CheckConstraint(
            "status <> 'unsupported' OR evidence_count > 0 "
            "OR absent_evidence_reason IS NOT NULL",
            name="ck_agent_claim_support_unsupported_requires_reason_or_evidence",
        ),
        sa.CheckConstraint(
            "absent_evidence_reason IS NULL OR evidence_count = 0",
            name="ck_agent_claim_support_reason_only_when_no_evidence",
        ),
        sa.CheckConstraint(
            "absent_evidence_reason IS NULL OR status = 'unsupported'",
            name="ck_agent_claim_support_reason_only_when_unsupported",
        ),
    )
    op.create_index(
        "idx_agent_claim_support_org_run",
        "agent_claim_supports",
        ["organization_id", "run_id"],
    )
    op.create_index(
        "idx_agent_claim_support_claim",
        "agent_claim_supports",
        ["claim_id", "evaluated_at"],
    )
    op.create_index(
        "idx_agent_claim_support_gate",
        "agent_claim_supports",
        ["organization_id", "run_id", "status"],
    )
    op.create_index(
        "idx_agent_claim_support_latest",
        "agent_claim_supports",
        ["run_id", "artifact_id", "claim_id", "evaluated_at"],
    )


def _install_append_only_triggers() -> None:
    # `reject_append_only_mutation()` already exists — created by
    # `c3f5a1d7b209`, which this migration is chained after — so it is reused
    # rather than redefined.
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
    """Create the tool invocation, artifact, evidence, and capability tables."""
    _create_capability_grants()
    _create_capability_approvals()
    _create_artifacts()
    _create_tool_invocations()
    _create_evidence()
    _create_claim_supports()

    if _is_postgresql():
        _install_append_only_triggers()
        _enable_tenant_rls()


def downgrade() -> None:
    """Remove the tool invocation, artifact, evidence, and capability tables."""
    if _is_postgresql():
        for table in TENANT_SCOPED_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        for table in APPEND_ONLY_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")

    op.drop_table("agent_claim_supports")
    op.drop_table("agent_evidence")
    op.drop_table("agent_tool_invocations")
    op.drop_table("agent_artifacts")
    op.drop_table("agent_capability_approvals")
    op.drop_table("agent_capability_grants")
