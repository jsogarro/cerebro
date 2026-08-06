"""Append-only persistence for ``agent_evidence``.

Mirrors ``src.core.contracts.provenance.Evidence``: a stable locator into an
immutable source snapshot. Once written, an evidence row must never change —
a claim's support is only meaningful if the thing it points at cannot move
out from under it — so this table follows the same append-only treatment as
``agent_run_events`` (``@register_append_only`` plus the migration's
database trigger).

``producer_kind`` and ``prompt_binding`` follow the same rule as
``ToolInvocation``/``Artifact`` (see ``provenance_columns.
OptionalPromptBindingMixin``): a ``model_turn`` record names its prompt, a
``system`` record — an acquisition tool, a deterministic evaluator — does
not, and the biconditional is a database CHECK, not just a Pydantic
validator. Unlike the other two tables, ``producer_kind`` here has **no
server default**: a default would let a caller omit it and have a
deterministic record silently pass as model-produced, which is the exact
inversion of what prompt pinning protects. This table therefore declares its
own five columns (``producer_kind`` plus the four binding fields, all
nullable except ``producer_kind`` itself) rather than reusing
``OptionalPromptBindingMixin``, whose ``producer_kind`` does default.

``locator`` is sized to ``MAX_LOCATOR_LENGTH`` from
``src.core.contracts.locators`` — the frozen grammar's own bound — so the
column width cannot silently drift from the contract that defines it.

``locator_scheme``/``locator_start``/``locator_end`` are the canonical span
parsed out of ``locator`` by ``EvidenceRepository`` (via
``src.core.contracts.locators.parse_locator``) and denormalized onto their
own columns, so the grammar's own invariants — half-open, strictly
increasing, 1-based line numbers — are database CHECKs rather than only a
Pydantic validator a raw-SQL write path could bypass.

The evidence/artifact digest agreement (``Evidence.content_sha256`` must
match its ``snapshot_artifact_id``'s ``agent_artifacts.content_sha256``) is
not expressible as a single-table CHECK; it is enforced by
``EvidenceRepository`` at write time.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
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

from src.core.contracts import ProducerKind, TrustClassification
from src.core.contracts.locators import LOCATOR_CANONICAL_SCHEMES, MAX_LOCATOR_LENGTH
from src.models.db.append_only import register_append_only
from src.models.db.base import UUID, Base
from src.models.db.provenance_columns import (
    producer_kind_biconditional_check,
    prompt_binding_all_or_nothing_check,
)
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
    locator_scheme: Mapped[str] = mapped_column(String(16), nullable=False)
    locator_start: Mapped[int] = mapped_column(BigInteger, nullable=False)
    locator_end: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trust: Mapped[str] = mapped_column(String(30), nullable=False)

    producer_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    prompt_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    template_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rendered_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

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
        CheckConstraint(
            "locator_scheme IN ('"
            + "', '".join(sorted(LOCATOR_CANONICAL_SCHEMES))
            + "')",
            name="ck_agent_evidence_locator_scheme",
        ),
        CheckConstraint(
            "locator_start >= 0", name="ck_agent_evidence_locator_start_non_negative"
        ),
        CheckConstraint(
            "locator_end > locator_start", name="ck_agent_evidence_locator_half_open"
        ),
        # `line` spans are 1-based per the frozen locator grammar; `bytes`
        # and `char` are 0-based, so this only constrains the `line` scheme.
        CheckConstraint(
            "locator_scheme <> 'line' OR locator_start >= 1",
            name="ck_agent_evidence_locator_line_one_based",
        ),
        status_check(
            column="trust", statuses=TrustClassification, name="ck_agent_evidence_trust"
        ),
        # Mutation-checked and found unreachable: `producer_kind_biconditional_check`
        # only accepts 'system' or 'model_turn' — any other value fails both of
        # its disjuncts regardless of prompt-binding state, so this value-domain
        # CHECK never independently fires. Kept as the same defence-in-depth
        # posture as the claim-support CHECKs above: if the biconditional is
        # ever relaxed, this is what still bounds the column's value set.
        status_check(
            column="producer_kind",
            statuses=ProducerKind,
            name="ck_agent_evidence_producer_kind",
        ),
        prompt_binding_all_or_nothing_check(
            name="ck_agent_evidence_prompt_binding_all_or_nothing"
        ),
        producer_kind_biconditional_check(
            name="ck_agent_evidence_producer_kind_biconditional"
        ),
        Index("idx_agent_evidence_org_run", "organization_id", "run_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentEvidence(evidence_id={self.evidence_id}, locator={self.locator})>"
        )


__all__ = ["AgentEvidence"]
