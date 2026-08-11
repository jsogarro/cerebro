"""Durable persistence for task-scoped capability grants and their approvals.

Mirrors ``src.core.contracts.capabilities.CapabilityGrant`` and
``ApprovalRef``. Both tables are mutable ``BaseModel`` rows, not append-only:
a grant or approval is a standing permission an operator can revise, unlike
the evidence and claim-support records these grants ultimately authorize.

``ck_agent_capability_grant_approval_required`` is the persistence half of the
contract's construction-time guarantee
(``CapabilityGrant.validate_grant``): a grant whose ``sensitivity`` is
``external_write`` or ``exfiltration`` always requires approval, and no code
path — Python or raw SQL — can insert a row that waives it.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.core.contracts import SensitivityClass, TrustClassification
from src.models.db.base import UUID, BaseModel
from src.models.db.run_lifecycle import CONTRACT_SCHEMA_VERSION, status_check

APPROVAL_REQUIRED_SENSITIVITIES_SQL = "('external_write', 'exfiltration')"


class AgentCapabilityGrant(BaseModel):
    """One task-scoped permission to invoke one tool under stated constraints."""

    __tablename__ = "agent_capability_grants"

    grant_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    run_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("agent_runs.run_id"), nullable=False, index=True
    )
    task_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("agent_run_tasks.task_id"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(),
        nullable=True,
        index=True,
        comment="Tenant organization boundary identifier",
    )

    capability_scope: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_versions: Mapped[list[str]] = mapped_column(
        # JSON here mirrors the pattern used for `dependency_ids` on
        # `agent_run_tasks`: an ordered, app-validated list of versions rather
        # than a separate join table.
        JSON,
        nullable=False,
    )
    sensitivity: Mapped[str] = mapped_column(String(30), nullable=False)
    max_input_trust: Mapped[str] = mapped_column(String(30), nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False)

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    contract_schema_version: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CONTRACT_SCHEMA_VERSION,
        server_default=CONTRACT_SCHEMA_VERSION,
    )

    __table_args__ = (
        status_check(
            column="sensitivity",
            statuses=SensitivityClass,
            name="ck_agent_capability_grant_sensitivity",
        ),
        status_check(
            column="max_input_trust",
            statuses=TrustClassification,
            name="ck_agent_capability_grant_max_input_trust",
        ),
        CheckConstraint(
            "expires_at > issued_at", name="ck_agent_capability_grant_window"
        ),
        CheckConstraint(
            f"sensitivity NOT IN {APPROVAL_REQUIRED_SENSITIVITIES_SQL} "
            "OR requires_approval",
            name="ck_agent_capability_grant_approval_required",
        ),
        Index("idx_agent_capability_grant_run_task", "run_id", "task_id", "tool_name"),
        Index(
            "idx_agent_capability_grant_org",
            "organization_id",
            "run_id",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentCapabilityGrant(grant_id={self.grant_id}, "
            f"tool_name={self.tool_name})>"
        )


class AgentCapabilityApproval(BaseModel):
    """One human approval bound to one grant and one exact request fingerprint.

    The ``(grant_id, request_fingerprint)`` uniqueness is what makes an
    approval unreplayable against a different request: the fingerprint covers
    every field of the request that determines authorization, so two distinct
    requests can never share a row here.
    """

    __tablename__ = "agent_capability_approvals"

    approval_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    grant_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_capability_grants.grant_id"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(),
        nullable=True,
        index=True,
        comment="Tenant organization boundary identifier; copied from the parent grant",
    )

    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    contract_schema_version: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CONTRACT_SCHEMA_VERSION,
        server_default=CONTRACT_SCHEMA_VERSION,
    )

    __table_args__ = (
        UniqueConstraint(
            "grant_id",
            "request_fingerprint",
            name="uq_agent_capability_approval_grant_fingerprint",
        ),
        CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_agent_capability_approval_fingerprint_digest",
        ),
        CheckConstraint(
            "expires_at > approved_at", name="ck_agent_capability_approval_window"
        ),
        Index("idx_agent_capability_approval_org", "organization_id", "grant_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentCapabilityApproval(approval_id={self.approval_id}, "
            f"grant_id={self.grant_id})>"
        )


__all__ = ["AgentCapabilityApproval", "AgentCapabilityGrant"]
