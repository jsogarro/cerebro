"""Append-only persistence for ``agent_evaluation_results``.

Mirrors ``src.core.contracts.EvaluationResult``. A re-evaluation appends a new
row, so a run's verdict is never edited after the fact; the canonical view of a
run is the latest row per ``(evaluator_id, evaluator_version, dimension,
subject_id)`` ordered by the database's ``recorded_at``, never a pod's
``completed_at`` (clocks skew, and an older ``error`` must not outrank a later
pass).

A run with no row for a dimension was not evaluated. Readers must present that
as ``not_attempted``, never as a pass; nothing in this table stands in for it.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.core.contracts import EvaluationStatus
from src.models.db.append_only import register_append_only
from src.models.db.base import UUID, Base
from src.models.db.run_lifecycle import CONTRACT_SCHEMA_VERSION, status_check


@register_append_only
class AgentEvaluationResult(Base):
    """One dimension-specific evaluator outcome for a run."""

    __tablename__ = "agent_evaluation_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, default=uuid.uuid4, nullable=False
    )
    evaluation_result_id: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True
    )
    run_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("agent_runs.run_id"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("agent_run_tasks.task_id"), nullable=True
    )
    artifact_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("agent_artifacts.artifact_id"), nullable=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(),
        nullable=True,
        index=True,
        comment="Tenant organization boundary identifier",
    )
    evaluator_id: Mapped[str] = mapped_column(String(255), nullable=False)
    evaluator_version: Mapped[str] = mapped_column(String(100), nullable=False)
    dimension: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    deterministic: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    rubric_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    judge_model_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(160), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trigger: Mapped[str] = mapped_column(String(255), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When the database accepted the row, distinct from completed_at",
    )
    contract_schema_version: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CONTRACT_SCHEMA_VERSION
    )

    __table_args__ = (
        status_check(
            column="status",
            statuses=EvaluationStatus,
            name="ck_agent_evaluation_result_status",
        ),
        # Error and abstention are not verdicts and must carry their cause.
        CheckConstraint(
            "status NOT IN ('error', 'abstained') "
            "OR (error_code IS NOT NULL AND length(error_code) > 0)",
            name="ck_agent_evaluation_result_non_verdict_requires_reason",
        ),
        # Explicit IS NULL: a bare range over NULL is NULL, which a CHECK accepts.
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name="ck_agent_evaluation_result_score_range",
        ),
        CheckConstraint(
            "NOT deterministic OR (judge_model_id IS NULL AND rubric_sha256 IS NULL)",
            name="ck_agent_evaluation_result_deterministic_has_no_judge",
        ),
        CheckConstraint(
            "(judge_model_id IS NULL) = (rubric_sha256 IS NULL)",
            name="ck_agent_evaluation_result_judge_fields_together",
        ),
        CheckConstraint(
            "length(trigger) > 0", name="ck_agent_evaluation_result_trigger_non_empty"
        ),
        Index(
            "idx_agent_evaluation_result_org_run",
            "organization_id",
            "run_id",
            "completed_at",
        ),
        # The verdict-cache lookup for a judge that has already seen this input.
        Index(
            "idx_agent_evaluation_result_cache",
            "organization_id",
            "evaluator_id",
            "evaluator_version",
            "input_sha256",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentEvaluationResult(id={self.evaluation_result_id}, "
            f"status={self.status})>"
        )


__all__ = ["AgentEvaluationResult"]
