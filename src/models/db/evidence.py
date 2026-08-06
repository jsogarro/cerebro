"""Append-only persistence for ``agent_evidence``.

Mirrors ``src.core.contracts.provenance.Evidence``: a stable locator into an
immutable source snapshot. Once written, an evidence row must never change —
a claim's support is only meaningful if the thing it points at cannot move
out from under it — so this table follows the same append-only treatment as
``agent_run_events`` (``@register_append_only`` plus the migration's
database trigger).

``PromptBinding`` here is **required**, not optional: unlike
``ToolInvocation``/``Artifact`` (see ``provenance_columns.
OptionalPromptBindingMixin``), every ``Evidence`` row names the prompt or
acquisition step that produced it, so the four binding columns are declared
``NOT NULL`` directly rather than through the optional mixin.

``locator`` is sized to ``MAX_LOCATOR_LENGTH`` from
``src.core.contracts.locators`` — the frozen grammar's own bound — so the
column width cannot silently drift from the contract that defines it.

The evidence/artifact digest agreement (``Evidence.content_sha256`` must
match its ``snapshot_artifact_id``'s ``agent_artifacts.content_sha256``) is
not expressible as a single-table CHECK; it is enforced by
``EvidenceRepository`` at write time.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.core.contracts import TrustClassification
from src.core.contracts.locators import MAX_LOCATOR_LENGTH
from src.models.db.append_only import register_append_only
from src.models.db.base import UUID, Base
from src.models.db.run_lifecycle import status_check


@register_append_only
class AgentEvidence(Base):
    """One immutable, stable locator into a source snapshot."""

    __tablename__ = "agent_evidence"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, default=uuid.uuid4, nullable=False
    )
    evidence_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
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

    source_type: Mapped[str] = mapped_column(String(255), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_artifact_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_artifacts.artifact_id"),
        nullable=False,
        index=True,
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    locator: Mapped[str] = mapped_column(String(MAX_LOCATOR_LENGTH), nullable=False)
    trust: Mapped[str] = mapped_column(String(30), nullable=False)

    prompt_id: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    template_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    rendered_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    producer_tool_invocation_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("agent_tool_invocations.tool_invocation_id"),
        nullable=True,
    )
    parent_evidence_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )

    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When the database accepted the row, distinct from acquired_at",
    )

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "snapshot_artifact_id",
            "locator",
            name="uq_agent_evidence_span",
        ),
        CheckConstraint("length(content_sha256) = 64", name="ck_agent_evidence_digest"),
        CheckConstraint(
            "length(locator) > 0", name="ck_agent_evidence_locator_present"
        ),
        status_check(
            column="trust", statuses=TrustClassification, name="ck_agent_evidence_trust"
        ),
        Index("idx_agent_evidence_org_run", "organization_id", "run_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentEvidence(evidence_id={self.evidence_id}, locator={self.locator})>"
        )


__all__ = ["AgentEvidence"]
