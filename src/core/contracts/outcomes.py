"""Evaluation outcomes and append-only run events."""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from .base import (
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
    completed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_error_status(self) -> Self:
        if self.status is EvaluationStatus.ERROR and self.error_code is None:
            raise ValueError("Evaluator errors require error_code")
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
