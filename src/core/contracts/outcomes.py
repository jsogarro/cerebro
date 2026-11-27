"""Evaluation outcomes and append-only run events."""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from .base import (
    ContentSha256,
    ContractId,
    ContractModel,
    IdempotencyKey,
    JsonObject,
    Version,
)


class EvaluationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    ABSTAINED = "abstained"


class EvaluationResult(ContractModel):
    """One dimension-specific evaluator outcome; scores are not calibrated."""

    evaluation_result_id: ContractId
    run_id: ContractId
    task_id: ContractId | None = None
    artifact_id: ContractId | None = None
    evaluator_id: ContractId
    evaluator_version: Version
    dimension: ContractId
    status: EvaluationStatus
    deterministic: bool
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    error_code: ContractId | None = None
    details: JsonObject = Field(default_factory=dict)
    rationale: str | None = Field(default=None, min_length=1, max_length=8000)
    rubric_sha256: ContentSha256 | None = None
    judge_model_id: ContractId | None = None
    # Longer than Version: carries a dataset label of the form name@version:sha12.
    dataset_version: str | None = Field(default=None, min_length=1, max_length=160)
    # The claim or sub-artifact one row judges, when an evaluator emits one per subject.
    subject_id: ContractId | None = None
    input_sha256: ContentSha256 | None = None
    # What caused this evaluation pass; free-form, unique per pass, never parsed.
    trigger: str | None = Field(default=None, min_length=1, max_length=255)
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_judge_fields(self) -> Self:
        if self.deterministic and (self.judge_model_id or self.rubric_sha256):
            raise ValueError("A deterministic result cannot name a judge or rubric")
        if (self.judge_model_id is None) != (self.rubric_sha256 is None):
            raise ValueError("judge_model_id and rubric_sha256 must be set together")
        return self

    @model_validator(mode="after")
    def validate_error_status(self) -> Self:
        # Neither outcome is a verdict; both must carry their cause so they are
        # never read as a pass and abstention rates decompose by reason.
        if (
            self.status in {EvaluationStatus.ERROR, EvaluationStatus.ABSTAINED}
            and self.error_code is None
        ):
            raise ValueError("Evaluator errors and abstentions require error_code")
        return self


class RunEvent(ContractModel):
    """Append-only event with per-run ordering and duplicate suppression keys."""

    event_id: ContractId
    run_id: ContractId
    aggregate_type: ContractId
    aggregate_id: ContractId
    sequence: int = Field(ge=1)
    event_type: ContractId
    event_type_version: Version
    occurred_at: AwareDatetime
    producer: ContractId
    deduplication_key: IdempotencyKey
    correlation_id: ContractId | None = None
    causation_event_id: ContractId | None = None
    payload: JsonObject = Field(default_factory=dict)
