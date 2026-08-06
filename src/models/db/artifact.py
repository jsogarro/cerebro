"""Durable persistence for ``agent_artifacts``.

Mirrors ``src.core.contracts.provenance.Artifact``. Rows are mutable
(``BaseModel``): an artifact's status can move ``draft`` -> ``final`` ->
``invalidated`` on the same row, unlike the append-only evidence and
claim-support tables that cite it.

No separate ``created_at`` column is declared: ``Artifact.created_at`` and
``BaseModel``'s audit ``created_at`` name the same instant — an artifact's
content is immutable from the moment its row is written, so there is nothing
for a second timestamp to distinguish.
"""

import uuid
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.core.contracts import ArtifactStatus, ProducerKind, TrustClassification
from src.models.db.base import UUID, BaseModel
from src.models.db.provenance_columns import (
    OptionalPromptBindingMixin,
    producer_kind_biconditional_check,
    prompt_binding_all_or_nothing_check,
)
from src.models.db.run_lifecycle import CONTRACT_SCHEMA_VERSION, status_check


class AgentArtifact(BaseModel, OptionalPromptBindingMixin):
    """Immutable content metadata stored outside transient agent context."""

    __tablename__ = "agent_artifacts"

    artifact_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    run_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("agent_runs.run_id"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("agent_run_tasks.task_id"), nullable=True, index=True
    )
    attempt_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("agent_task_attempts.attempt_id"),
        nullable=True,
        index=True,
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(),
        nullable=True,
        index=True,
        comment="Tenant organization boundary identifier",
    )

    kind: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    trust: Mapped[str] = mapped_column(String(30), nullable=False)
    producer: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )

    contract_schema_version: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CONTRACT_SCHEMA_VERSION,
        server_default=CONTRACT_SCHEMA_VERSION,
    )

    __table_args__ = (
        status_check(
            column="status", statuses=ArtifactStatus, name="ck_agent_artifact_status"
        ),
        status_check(
            column="trust",
            statuses=TrustClassification,
            name="ck_agent_artifact_trust",
        ),
        # Unreachable given `producer_kind_biconditional_check`'s disjunctive
        # form (verified: same shared function as agent_evidence/
        # agent_claim_supports, not a divergent copy) -- see the fuller note
        # on those tables. Kept as defence in depth; would become load-bearing
        # if the biconditional were ever rewritten in the equality form.
        status_check(
            column="producer_kind",
            statuses=ProducerKind,
            name="ck_agent_artifact_producer_kind",
        ),
        CheckConstraint("length(content_sha256) = 64", name="ck_agent_artifact_digest"),
        prompt_binding_all_or_nothing_check(
            name="ck_agent_artifact_prompt_binding_all_or_nothing"
        ),
        producer_kind_biconditional_check(
            name="ck_agent_artifact_producer_kind_biconditional"
        ),
        Index("idx_agent_artifact_run_task", "run_id", "task_id"),
        Index(
            "idx_agent_artifact_org_status", "organization_id", "status", "created_at"
        ),
    )

    def __repr__(self) -> str:
        return f"<AgentArtifact(artifact_id={self.artifact_id}, kind={self.kind})>"


__all__ = ["AgentArtifact"]
