"""Rewrite tenant policies, FORCE row-level security, add maintenance policies.

Revision ID: f1c8a4d7e2b6
Revises: b8e3f1a5c7d9
Create Date: 2026-11-27

Three changes to the 21 tenant tables, all PostgreSQL only:

* every ``tenant_isolation_<table>`` policy takes the ``NULLIF(..., '')::uuid``
  form. Fourteen were created with a bare cast, which raises on a pooled
  connection whose last transaction set the tenant and ended (the setting then
  reads back ``''``) instead of returning no rows;
* ``FORCE ROW LEVEL SECURITY`` so the policies also bind a table's owner;
* policies ``TO cerebro_maint`` that admit exactly the cross-tenant reads the
  background jobs need, nothing wider. Other tables have none: the maintenance
  role sees no rows there.

``cerebro_maint`` is created NOLOGIN here when absent so the policies can name
it; ``docker/roles.sql`` later gives it its password and LOGIN.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1c8a4d7e2b6"
down_revision: str | Sequence[str] | None = "b8e3f1a5c7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "research_projects",
    "agent_runs",
    "agent_run_tasks",
    "agent_task_attempts",
    "agent_run_config_snapshots",
    "agent_run_events",
    "agent_run_event_outbox",
    "agent_capability_grants",
    "agent_capability_approvals",
    "agent_artifacts",
    "agent_tool_invocations",
    "agent_evidence",
    "agent_claim_supports",
    "agent_execution_authorities",
    "uploaded_documents",
    "usage_ledger",
    "document_critiques",
    "critique_findings",
    "similarity_checks",
    "similarity_matches",
    "egress_events",
    "generated_reports",
)
# Created without any policy; this migration adds one.
NEW_POLICY_TABLES = ("generated_reports",)
# The sweeps' old GUC-gated policies admitted every role; the maintenance role
# replaces them, so the application role can no longer reach other tenants' rows.
_PURGE_POLICIES = (
    ("uploaded_documents", "expires_at <= now()"),
    ("egress_events", "created_at <= now() - interval '1 day'"),
)
# Created before the NULLIF form was adopted; downgrade restores their bare cast.
BARE_CAST_TABLES = (
    "research_projects",
    "agent_runs",
    "agent_run_tasks",
    "agent_task_attempts",
    "agent_run_config_snapshots",
    "agent_run_events",
    "agent_run_event_outbox",
    "agent_capability_grants",
    "agent_capability_approvals",
    "agent_artifacts",
    "agent_tool_invocations",
    "agent_evidence",
    "agent_claim_supports",
    "agent_execution_authorities",
)
_NULLIF = "organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid"
_BARE = "organization_id = current_setting('app.current_org_id', true)::uuid"

# Only the columns the relay writes: a row must never change tenant.
_OUTBOX_PRIVILEGES = (
    "SELECT, UPDATE (status, claimed_at, claimed_by, attempts, available_at, "
    "last_error, delivered_at)"
)

# (table, command, USING predicate, privileges to grant)
_MAINTENANCE_POLICIES = (
    ("agent_runs", "SELECT", "true", "SELECT"),
    ("agent_run_tasks", "SELECT", "true", "SELECT"),
    ("agent_task_attempts", "SELECT", "true", "SELECT"),
    ("agent_run_event_outbox", "SELECT", "true", _OUTBOX_PRIVILEGES),
    ("agent_run_event_outbox", "UPDATE", "true", _OUTBOX_PRIVILEGES),
    ("agent_execution_authorities", "SELECT", "true", "SELECT"),
    ("agent_run_config_snapshots", "SELECT", "true", "SELECT"),
    ("uploaded_documents", "SELECT", "expires_at <= now()", "SELECT, DELETE"),
    ("uploaded_documents", "DELETE", "expires_at <= now()", "SELECT, DELETE"),
    # One day is the lower bound any retention window can have.
    ("egress_events", "SELECT", "created_at <= now() - interval '1 day'", "SELECT, DELETE"),
    ("egress_events", "DELETE", "created_at <= now() - interval '1 day'", "SELECT, DELETE"),
)


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _policy_name(table: str, command: str) -> str:
    return f"maintenance_{command.lower()}_{table}"


def upgrade() -> None:
    if not _is_postgresql():
        return
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cerebro_maint') THEN
                CREATE ROLE cerebro_maint NOLOGIN NOBYPASSRLS;
            END IF;
        END
        $$
        """
    )
    for table in NEW_POLICY_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_{table} ON {table} USING ({_NULLIF})"
        )
    for table in TENANT_TABLES:
        op.execute(f"ALTER POLICY tenant_isolation_{table} ON {table} USING ({_NULLIF})")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for table, _predicate in _PURGE_POLICIES:
        for command in ("select", "delete"):
            op.execute(f"DROP POLICY purge_expired_{command}_{table} ON {table}")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cerebro_app') THEN
                REVOKE ALL ON alembic_version FROM cerebro_app;
            END IF;
        END
        $$
        """
    )
    for table, command, predicate, privileges in _MAINTENANCE_POLICIES:
        op.execute(
            f"CREATE POLICY {_policy_name(table, command)} ON {table} "
            f"FOR {command} TO cerebro_maint USING ({predicate})"
        )
        op.execute(f"GRANT {privileges} ON {table} TO cerebro_maint")


def downgrade() -> None:
    if not _is_postgresql():
        return
    for table, command, _predicate, _privileges in reversed(_MAINTENANCE_POLICIES):
        op.execute(f"DROP POLICY IF EXISTS {_policy_name(table, command)} ON {table}")
    for table in sorted({entry[0] for entry in _MAINTENANCE_POLICIES}):
        op.execute(f"REVOKE ALL ON {table} FROM cerebro_maint")
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        predicate = _BARE if table in BARE_CAST_TABLES else _NULLIF
        op.execute(f"ALTER POLICY tenant_isolation_{table} ON {table} USING ({predicate})")
    for table in NEW_POLICY_TABLES:
        op.execute(f"DROP POLICY tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for table, predicate in _PURGE_POLICIES:
        for command in ("select", "delete"):
            op.execute(
                f"CREATE POLICY purge_expired_{command}_{table} ON {table} "
                f"FOR {command.upper()} USING ({predicate} "
                "AND current_setting('app.purge_expired', true) = 'on')"
            )
