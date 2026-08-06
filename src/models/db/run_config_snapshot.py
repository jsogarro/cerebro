"""Immutable configuration snapshot captured when a run is admitted.

A run outlives deployments. Without a snapshot, resuming a run after a prompt
edit, an evaluator revision, or a policy change silently changes what the run
means. This table freezes the whole compiled configuration and the exact
component versions the run was admitted under, so a resumed or replayed run
keeps its original semantics.

The record is append-only: exactly one snapshot per run, never updated, never
deleted. ``content_sha256`` is the hash of the canonical serialization, which
makes tampering detectable independently of the enforcement below.
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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.models.db.append_only import register_append_only
from src.models.db.base import UUID, Base
from src.models.db.run_lifecycle import CONTRACT_SCHEMA_VERSION


@register_append_only
class AgentRunConfigSnapshot(Base):
    """The frozen configuration and version pins one run was admitted with."""

    __tablename__ = "agent_run_config_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, default=uuid.uuid4, nullable=False
    )
    config_snapshot_id: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True
    )
    run_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_runs.run_id"),
        nullable=False,
        unique=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(),
        nullable=True,
        index=True,
        comment="Tenant organization boundary identifier",
    )

    authority_id: Mapped[str] = mapped_column(String(255), nullable=False)
    authority_version: Mapped[str] = mapped_column(String(100), nullable=False)

    workflow_definition_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow_definition_version: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    routing_policy_id: Mapped[str] = mapped_column(String(255), nullable=False)
    routing_policy_version: Mapped[str] = mapped_column(String(100), nullable=False)

    pinned_versions: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        comment="Serialized PinnedVersions contract: prompt, evaluator, "
        "artifact-schema, policy, and tool versions pinned for this run",
    )
    configuration: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        comment="Frozen compiled execution configuration for this run",
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    contract_schema_version: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CONTRACT_SCHEMA_VERSION,
        server_default=CONTRACT_SCHEMA_VERSION,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "length(content_sha256) = 64", name="ck_agent_run_config_snapshot_digest"
        ),
        Index("idx_agent_run_config_snapshot_authority", "authority_id", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentRunConfigSnapshot(run_id={self.run_id}, "
            f"authority={self.authority_id}@{self.authority_version})>"
        )
