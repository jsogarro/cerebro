"""Durable persistence for the run, task, and attempt lifecycle.

These tables are the authoritative record of an execution. They mirror the
canonical Pydantic contracts in ``src.core.contracts`` field for field; the
contracts remain the place where legal status transitions and timestamp
invariants are validated. Persisting a row never bypasses that validation —
callers build the contract, then write it.

Two columns exist on every table beyond the contract shape:

``organization_id``
    The enforced tenant boundary, matching the posture of the existing
    tenant-scoped tables (nullable UUID column plus a Postgres row-level
    security policy). ``tenant_id`` on a run is the contract-level tenant
    identity carried through the event stream; ``organization_id`` is what the
    database filters on.

``contract_schema_version``
    The contract envelope version the row was written under, so a run admitted
    before a contract revision keeps its original semantics after deployment.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.core.contracts import AttemptStatus, RunStatus, TaskStatus
from src.models.db.base import UUID, BaseModel

CONTRACT_SCHEMA_VERSION = "1.0"


def status_check(*, column: str, statuses: type[StrEnum], name: str) -> CheckConstraint:
    """Constrain a status column to exactly the contract's state machine.

    Deriving the allowed values from the contract enum means the database and
    the state machine cannot drift apart in application code. The migration
    spells the same values out literally, because a migration must describe the
    schema as it was at that revision.
    """

    allowed = ", ".join(f"'{status.value}'" for status in sorted(statuses))
    return CheckConstraint(f"{column} IN ({allowed})", name=name)


class AgentRun(BaseModel):
    """One durable workflow execution, pinned to the versions it started with."""

    __tablename__ = "agent_runs"

    run_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(),
        nullable=True,
        index=True,
        comment="Tenant organization boundary identifier",
    )

    workflow_definition_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow_definition_version: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    routing_policy_id: Mapped[str] = mapped_column(String(255), nullable=False)
    routing_policy_version: Mapped[str] = mapped_column(String(100), nullable=False)

    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=RunStatus.CREATED.value, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_event_sequence: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
        comment="High-water mark used to allocate the next run event sequence",
    )

    contract_schema_version: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CONTRACT_SCHEMA_VERSION,
        server_default=CONTRACT_SCHEMA_VERSION,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_agent_run_tenant_idempotency"
        ),
        status_check(column="status", statuses=RunStatus, name="ck_agent_run_status"),
        CheckConstraint(
            "last_event_sequence >= 0", name="ck_agent_run_sequence_non_negative"
        ),
        Index("idx_agent_run_org_status", "organization_id", "status", "created_at"),
        Index("idx_agent_run_tenant_status", "tenant_id", "status", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<AgentRun(run_id={self.run_id}, status={self.status})>"


class AgentRunTask(BaseModel):
    """A logical unit of work within a run; retries create new attempts."""

    __tablename__ = "agent_run_tasks"

    task_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    run_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_runs.run_id"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(),
        nullable=True,
        index=True,
        comment="Tenant organization boundary identifier",
    )

    task_key: Mapped[str] = mapped_column(String(255), nullable=False)
    task_type: Mapped[str] = mapped_column(String(255), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    dependency_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    assigned_worker_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaskStatus.PENDING.value, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    contract_schema_version: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CONTRACT_SCHEMA_VERSION,
        server_default=CONTRACT_SCHEMA_VERSION,
    )

    __table_args__ = (
        UniqueConstraint(
            "run_id", "idempotency_key", name="uq_agent_run_task_idempotency"
        ),
        UniqueConstraint("run_id", "task_key", name="uq_agent_run_task_key"),
        status_check(
            column="status", statuses=TaskStatus, name="ck_agent_run_task_status"
        ),
        Index("idx_agent_run_task_run_status", "run_id", "status"),
        Index(
            "idx_agent_run_task_org_status",
            "organization_id",
            "status",
            "created_at",
        ),
    )

    def __repr__(self) -> str:
        return f"<AgentRunTask(task_id={self.task_id}, status={self.status})>"


class AgentTaskAttempt(BaseModel):
    """One execution attempt for a task.

    ``journaled_result`` holds the nondeterministic output that recovery must
    not recompute — a provider response, a tool result, a generated identifier.
    Replaying a run reads it back instead of re-invoking the effect.

    ``run_id`` is derivable through ``task_id`` and is stored anyway: recovery
    and tenant-scoped scans read attempts by run, and the row must be filterable
    by the same boundary as everything else without a join.
    """

    __tablename__ = "agent_task_attempts"

    attempt_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    task_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_run_tasks.task_id"),
        nullable=False,
        index=True,
    )
    run_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_runs.run_id"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(),
        nullable=True,
        index=True,
        comment="Tenant organization boundary identifier",
    )

    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    executor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AttemptStatus.CREATED.value, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    journaled_result: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Nondeterministic attempt output replayed instead of recomputed",
    )

    contract_schema_version: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CONTRACT_SCHEMA_VERSION,
        server_default=CONTRACT_SCHEMA_VERSION,
    )

    __table_args__ = (
        UniqueConstraint(
            "task_id", "idempotency_key", name="uq_agent_task_attempt_idempotency"
        ),
        UniqueConstraint("task_id", "ordinal", name="uq_agent_task_attempt_ordinal"),
        CheckConstraint("ordinal >= 1", name="ck_agent_task_attempt_ordinal"),
        status_check(
            column="status", statuses=AttemptStatus, name="ck_agent_task_attempt_status"
        ),
        Index("idx_agent_task_attempt_run_status", "run_id", "status"),
        Index(
            "idx_agent_task_attempt_org_status",
            "organization_id",
            "status",
            "created_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentTaskAttempt(attempt_id={self.attempt_id}, "
            f"ordinal={self.ordinal}, status={self.status})>"
        )
