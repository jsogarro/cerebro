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
