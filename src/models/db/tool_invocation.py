"""Durable persistence for ``agent_tool_invocations``.

Mirrors ``src.core.contracts.provenance.ToolInvocation``. Rows are mutable
(``BaseModel``): an invocation moves through
``requested`` -> ``running`` -> a terminal status on the same row, matching
``agent_task_attempts``.

``capability_decision_effect`` and the nullable ``capability_grant_id`` /
``capability_approval_id`` columns record the outcome of the capability check
that governed this invocation (``src.core.contracts.capabilities.
CapabilityDecision``), so a denied decision can never coexist with a
non-denied invocation status — ``ck_agent_tool_invocation_capability_denial``
enforces that at the database level. The full decision — including its
``denial_reason`` — is not duplicated here; the tool-execution boundary (Wave
4 packet 4C) is expected to publish it through the run event stream via
``RunEventRepository.append_event``, which is the one event mechanism this
persistence layer supports.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.core.contracts import ToolInvocationStatus, TrustClassification
from src.models.db.base import UUID, BaseModel
from src.models.db.provenance_columns import (
    OptionalPromptBindingMixin,
    nullable_status_check,
    producer_kind_biconditional_check,
    prompt_binding_all_or_nothing_check,
)
from src.models.db.run_lifecycle import CONTRACT_SCHEMA_VERSION, status_check

CAPABILITY_DECISION_EFFECTS_SQL = "('allow', 'deny')"


class AgentToolInvocation(BaseModel, OptionalPromptBindingMixin):
    """An auditable request to one versioned tool capability."""

    __tablename__ = "agent_tool_invocations"

    tool_invocation_id: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True
    )
    run_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("agent_runs.run_id"), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("agent_run_tasks.task_id"), nullable=False, index=True
    )
    attempt_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_task_attempts.attempt_id"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(),
        nullable=True,
        index=True,
        comment="Tenant organization boundary identifier",
    )

    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    capability_scope: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)

    input: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    input_trust: Mapped[str] = mapped_column(String(30), nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_trust: Mapped[str | None] = mapped_column(String(30), nullable=True)

    capability_decision_effect: Mapped[str] = mapped_column(String(10), nullable=False)
    capability_grant_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("agent_capability_grants.grant_id"),
        nullable=True,
    )
    capability_approval_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("agent_capability_approvals.approval_id"),
        nullable=True,
    )

    error_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    contract_schema_version: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CONTRACT_SCHEMA_VERSION,
        server_default=CONTRACT_SCHEMA_VERSION,
    )

    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            "idempotency_key",
            name="uq_agent_tool_invocation_idempotency",
        ),
        status_check(
            column="status",
            statuses=ToolInvocationStatus,
            name="ck_agent_tool_invocation_status",
        ),
        status_check(
            column="input_trust",
            statuses=TrustClassification,
            name="ck_agent_tool_invocation_input_trust",
        ),
        nullable_status_check(
            column="output_trust",
            statuses=TrustClassification,
            name="ck_agent_tool_invocation_output_trust",
        ),
        prompt_binding_all_or_nothing_check(
            name="ck_agent_tool_invocation_prompt_binding_all_or_nothing"
        ),
        producer_kind_biconditional_check(
            name="ck_agent_tool_invocation_producer_kind_biconditional"
        ),
        CheckConstraint(
            f"capability_decision_effect IN {CAPABILITY_DECISION_EFFECTS_SQL}",
            name="ck_agent_tool_invocation_capability_decision_effect",
        ),
        CheckConstraint(
            "capability_decision_effect = 'allow' OR status = 'denied'",
            name="ck_agent_tool_invocation_capability_denial",
        ),
        Index(
            "idx_agent_tool_invocation_run_task_attempt",
            "run_id",
            "task_id",
            "attempt_id",
        ),
        Index(
            "idx_agent_tool_invocation_org_status",
            "organization_id",
            "status",
            "created_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentToolInvocation(tool_invocation_id={self.tool_invocation_id}, "
            f"status={self.status})>"
        )


__all__ = ["AgentToolInvocation"]
