"""Append-only persistence for ``agent_claim_supports``.

Mirrors ``src.core.contracts.provenance.ClaimSupport`` and its four-state
model (``supported`` / ``partially_supported`` / ``disputed`` /
``unsupported``). A re-evaluation of the same ``claim_id`` writes a **new**
row rather than updating the old one — ``claim_id`` is therefore indexed but
deliberately not unique — so the append-only treatment
(``@register_append_only`` plus the migration's trigger) applies here exactly
as it does to ``agent_evidence``.

``evidence_count`` is a denormalized ``len(evidence_ids)`` that exists solely
so the four-state model can be expressed as CHECK constraints rather than an
application convention: Postgres CHECK constraints cannot portably inspect a
JSON array's length without a dialect-specific function, and this table's
schema-characterization tests run on SQLite as well as Postgres (see
``tests/unit/models/db/test_durable_lifecycle_schema.py`` for the existing
precedent), so a plain integer column keeps the CHECKs portable. Nothing
computes ``evidence_count`` except ``ClaimSupportRepository`` — it is never
accepted as a caller-supplied argument — which is what keeps the column from
drifting out of agreement with ``evidence_ids``, the same structural
guarantee ``RunEventRepository`` uses for ``sequence`` (allocated
internally, never supplied).

The seven CHECK constraints below are the four-state model restated as
database invariants, one per branch of
``ClaimSupport.validate_evidence_links``:

1. ``status`` is one of the four contract values.
2. ``absent_evidence_reason`` is ``NULL`` or one of the five typed reasons.
3. ``evidence_count`` is never negative — mutation-checked and found
   unreachable through (4)-(6): every valid ``status`` already forces
   ``evidence_count`` to be either ``> 0`` (an evidence-bearing status, via
   (4)) or exactly ``0`` (``unsupported`` with a reason, via (6)), which
   rules out negative either way. Kept anyway as the same defence-in-depth
   posture ``decide_capability`` takes at the contract layer: if (4) or (6)
   is ever relaxed, this is the constraint that still stops a negative
   count reaching the row.
4. ``supported``/``partially_supported``/``disputed`` require
   ``evidence_count > 0``.
5. ``unsupported`` with zero evidence requires an ``absent_evidence_reason``.
6. ``absent_evidence_reason`` may be set only when there is no evidence.
7. ``absent_evidence_reason`` may be set only when ``status`` is
   ``unsupported`` — mutation-checked and found unreachable through (6): a
   reason paired with a non-``unsupported`` status already requires
   ``evidence_count > 0`` (via (4), since that status is evidence-bearing),
   and (6) then rejects an evidence_count that is both positive and
   reason-bearing. Kept anyway, same posture as (3): if (4) or (6) is ever
   relaxed, this is what still stops a ``supported`` row from also citing
   an absent-evidence reason.

``PromptBinding`` is **required** here, as on ``Evidence``: every judgment
names the evaluator prompt that produced it, so the four binding columns are
``NOT NULL`` directly rather than through
``provenance_columns.OptionalPromptBindingMixin``.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.core.contracts import AbsentEvidenceReason, ClaimSupportStatus
from src.models.db.append_only import register_append_only
from src.models.db.base import UUID, Base
from src.models.db.run_lifecycle import status_check

_EVIDENCE_BEARING_STATUSES_SQL = "('supported', 'partially_supported', 'disputed')"


@register_append_only
class AgentClaimSupport(Base):
    """One versioned evaluator judgment connecting a claim to evidence."""

    __tablename__ = "agent_claim_supports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, default=uuid.uuid4, nullable=False
    )
    claim_support_id: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True
    )
    run_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("agent_runs.run_id"), nullable=False, index=True
    )
    artifact_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_artifacts.artifact_id"),
        nullable=False,
        index=True,
    )
    claim_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(),
        nullable=True,
        index=True,
        comment="Tenant organization boundary identifier",
    )

    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    evidence_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="len(evidence_ids), computed once by ClaimSupportRepository so "
        "the four-state CHECKs below can read an integer instead of a JSON "
        "array length",
    )
    absent_evidence_reason: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )

    evaluator_id: Mapped[str] = mapped_column(String(255), nullable=False)
    evaluator_version: Mapped[str] = mapped_column(String(100), nullable=False)

    prompt_id: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    template_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    rendered_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When the database accepted the row, distinct from evaluated_at",
    )

    __table_args__ = (
        # (1) status is one of the four contract values.
        status_check(
            column="status",
            statuses=ClaimSupportStatus,
            name="ck_agent_claim_support_status",
        ),
        # (2) absent_evidence_reason is NULL or one of the five typed reasons.
        CheckConstraint(
            "absent_evidence_reason IS NULL OR absent_evidence_reason IN "
            "('"
            + "', '".join(sorted(reason.value for reason in AbsentEvidenceReason))
            + "')",
            name="ck_agent_claim_support_absent_reason_value",
        ),
        # (3) evidence_count is never negative.
        CheckConstraint(
            "evidence_count >= 0",
            name="ck_agent_claim_support_evidence_count_non_negative",
        ),
        # (4) evidence-bearing statuses require at least one evidence row.
        CheckConstraint(
            f"status NOT IN {_EVIDENCE_BEARING_STATUSES_SQL} OR evidence_count > 0",
            name="ck_agent_claim_support_evidence_bearing_requires_evidence",
        ),
        # (5) unsupported with no evidence must say why.
        CheckConstraint(
            "status <> 'unsupported' OR evidence_count > 0 "
            "OR absent_evidence_reason IS NOT NULL",
            name="ck_agent_claim_support_unsupported_requires_reason_or_evidence",
        ),
        # (6) a reason is set only when there is no evidence to cite.
        CheckConstraint(
            "absent_evidence_reason IS NULL OR evidence_count = 0",
            name="ck_agent_claim_support_reason_only_when_no_evidence",
        ),
        # (7) a reason is set only on an unsupported row.
        CheckConstraint(
            "absent_evidence_reason IS NULL OR status = 'unsupported'",
            name="ck_agent_claim_support_reason_only_when_unsupported",
        ),
        Index("idx_agent_claim_support_org_run", "organization_id", "run_id"),
        Index("idx_agent_claim_support_claim", "claim_id", "evaluated_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentClaimSupport(claim_support_id={self.claim_support_id}, "
            f"status={self.status})>"
        )


__all__ = ["AgentClaimSupport"]
