"""An evaluator outcome that is not a verdict must say why.

ERROR and ABSTAINED are the two outcomes that must never be read as a pass,
and abstention rates are only useful if they decompose by cause, so both
require a reason code.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.core.contracts import EvaluationResult, EvaluationStatus


def _result(status: EvaluationStatus, error_code: str | None) -> EvaluationResult:
    return EvaluationResult(
        evaluation_result_id="evaluation-001",
        run_id="run-001",
        evaluator_id="citation-resolution",
        evaluator_version="1.0.0",
        dimension="citation_resolution",
        status=status,
        deterministic=True,
        error_code=error_code,
        completed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.parametrize("status", [EvaluationStatus.ERROR, EvaluationStatus.ABSTAINED])
def test_non_verdict_outcomes_require_a_reason_code(status: EvaluationStatus) -> None:
    with pytest.raises(ValidationError, match="require error_code"):
        _result(status, None)

    assert _result(status, "evaluation.no_claims").error_code == "evaluation.no_claims"


@pytest.mark.parametrize("status", [EvaluationStatus.PASSED, EvaluationStatus.FAILED])
def test_verdicts_do_not_require_a_reason_code(status: EvaluationStatus) -> None:
    assert _result(status, None).error_code is None


def _deterministic(**fields: object) -> EvaluationResult:
    base: dict[str, object] = {
        "evaluation_result_id": "evaluation-001",
        "run_id": "run-001",
        "evaluator_id": "claims.coverage",
        "evaluator_version": "1.0.0",
        "dimension": "claims.coverage",
        "status": EvaluationStatus.PASSED,
        "deterministic": True,
        "completed_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    return EvaluationResult(**{**base, **fields})  # type: ignore[arg-type]


def test_provenance_fields_are_optional_so_existing_constructions_stay_valid() -> None:
    result = _deterministic()

    assert (result.trigger, result.subject_id, result.input_sha256) == (None,) * 3


def test_a_deterministic_result_cannot_name_a_judge() -> None:
    with pytest.raises(ValidationError, match="deterministic"):
        _deterministic(judge_model_id="judge-1", rubric_sha256="a" * 64)


def test_a_judge_model_and_its_rubric_travel_together() -> None:
    with pytest.raises(ValidationError, match="together"):
        _deterministic(deterministic=False, judge_model_id="judge-1")
    with pytest.raises(ValidationError, match="together"):
        _deterministic(deterministic=False, rubric_sha256="a" * 64)

    judged = _deterministic(
        deterministic=False, judge_model_id="judge-1", rubric_sha256="a" * 64
    )
    assert judged.judge_model_id == "judge-1"


def test_rationale_is_bounded() -> None:
    with pytest.raises(ValidationError):
        _deterministic(rationale="x" * 8001)
