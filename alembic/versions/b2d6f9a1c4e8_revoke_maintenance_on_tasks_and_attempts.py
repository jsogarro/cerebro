"""Take the maintenance role's read of tasks and attempts away.

Revision ID: b2d6f9a1c4e8
Revises: a9c3e5f7b1d2
Create Date: 2026-11-27

Recovery lists the interrupted runs as ``cerebro_maint`` and then rebuilds each
one, tasks and attempts included, under that run's own tenant, so no cross-tenant
job reads these two tables any more.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b2d6f9a1c4e8"
down_revision: str | Sequence[str] | None = "a9c3e5f7b1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("agent_run_tasks", "agent_task_attempts")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS maintenance_select_{table} ON {table}")
        op.execute(f"REVOKE ALL ON {table} FROM cerebro_maint")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _TABLES:
        op.execute(
            f"CREATE POLICY maintenance_select_{table} ON {table} "
            "FOR SELECT TO cerebro_maint USING (true)"
        )
        op.execute(f"GRANT SELECT ON {table} TO cerebro_maint")
