"""The evaluator registry, deterministic ids and the built-in coverage evaluator."""

import ast
import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from src.core.contracts import EvaluationResult, EvaluationStatus
from src.core.evaluation.coverage import ClaimsCoverageEvaluator
from src.core.evaluation.events import (
    EVALUATOR_CONTRACT_VIOLATION_CODE,
    EVALUATOR_EXCEPTION_CODE,
    derive_evaluation_result_id,
)
from src.core.evaluation.registry import (
    NOT_ATTEMPTED,
    ClaimView,
    EvaluationContext,
    EvaluatorRegistry,
    build_default_registry,
    evaluation_states,
)

NOW = datetime(2026, 11, 27, tzinfo=UTC)
ORG = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


def _ctx(
    claims: tuple[ClaimView, ...] = (),
    *,
    artifact_id: str | None = "artifact-1",
    trigger: str = "trigger-1",
) -> EvaluationContext:
    return EvaluationContext(
        run_id="run-1",
        organization_id=ORG,
        task_id=None,
        artifact_id=artifact_id,
        claims=claims,
        trigger=trigger,
        completed_at=NOW,
        session=None,  # type: ignore[arg-type]
    )


class _Fake:
    evaluator_version = "1"
    dimension = "fake"
    reads_claim_support = False

    def __init__(self, evaluator_id: str, *, version: str = "1", reads: bool = False):
        self.evaluator_id = evaluator_id
        self.evaluator_version = version
        self.reads_claim_support = reads

    async def evaluate(self, ctx: EvaluationContext) -> list[EvaluationResult]:
        return []


# --- ids ----------------------------------------------------------------------


def test_exception_codes_are_frozen() -> None:
    assert EVALUATOR_EXCEPTION_CODE == "evaluator_exception"
    assert EVALUATOR_CONTRACT_VIOLATION_CODE == "evaluator_contract_violation"


_BASE_ID_INPUTS: dict[str, Any] = {
    "run_id": "run-1",
    "evaluator_id": "e",
    "evaluator_version": "1",
    "dimension": "d",
    "artifact_id": "a",
    "discriminator": "x",
}


def test_derived_ids_are_stable_and_discriminated() -> None:
    base = derive_evaluation_result_id(**_BASE_ID_INPUTS)

    assert base == derive_evaluation_result_id(**_BASE_ID_INPUTS)
    for field, changed in {
        "run_id": "run-2",
        "evaluator_id": "f",
        "evaluator_version": "2",
        "dimension": "e",
        "artifact_id": None,
        "discriminator": "y",
    }.items():
        assert (
            derive_evaluation_result_id(**{**_BASE_ID_INPUTS, field: changed}) != base
        )


def test_a_version_bump_changes_the_id() -> None:
    # Deliberate: a new evaluator version is a new judgment, never a duplicate.
    ids = {
        derive_evaluation_result_id(**{**_BASE_ID_INPUTS, "evaluator_version": v})
        for v in ("1.0.0", "1.0.1")
    }
    assert len(ids) == 2


def test_the_context_folds_trigger_and_subject_into_the_id() -> None:
    evaluator = _Fake("e")
    ctx = _ctx()

    assert ctx.result_id(evaluator) == ctx.result_id(evaluator)
    assert ctx.result_id(evaluator) != _ctx(trigger="trigger-2").result_id(evaluator)
    assert (
        len(
            {
                ctx.result_id(evaluator),
                ctx.result_id(evaluator, subject_id="c1"),
                ctx.result_id(evaluator, subject_id="c2"),
            }
        )
        == 3
    )


# --- registry -----------------------------------------------------------------


def test_registering_the_same_id_and_version_twice_is_refused() -> None:
    registry = EvaluatorRegistry()
    registry.register(_Fake("e"))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(_Fake("e"))
    registry.register(_Fake("e", version="2"))


def test_evaluators_are_sorted_and_claim_dependents_are_reported() -> None:
    registry = EvaluatorRegistry()
    registry.register(_Fake("b", reads=True))
    registry.register(_Fake("a"))

    assert [e.evaluator_id for e in registry.evaluators()] == ["a", "b"]
    assert registry.claim_dependent_ids() == {"b"}


def test_the_default_registry_holds_only_the_coverage_evaluator() -> None:
    assert [e.evaluator_id for e in build_default_registry().evaluators()] == [
        "claims.coverage"
    ]


def _result(
    evaluator: Any, status: EvaluationStatus, **fields: Any
) -> EvaluationResult:
    return EvaluationResult(
        evaluation_result_id=f"r-{evaluator.evaluator_id}-{status}",
        run_id="run-1",
        evaluator_id=evaluator.evaluator_id,
        evaluator_version=evaluator.evaluator_version,
        dimension=evaluator.dimension,
        status=status,
        deterministic=True,
        completed_at=NOW,
        **fields,
    )


def test_a_dimension_with_no_row_is_not_attempted_never_a_pass() -> None:
    registry = EvaluatorRegistry()
    evaluated, missing = _Fake("a"), _Fake("b")
    registry.register(evaluated)
    registry.register(missing)

    states = evaluation_states(registry, [_result(evaluated, EvaluationStatus.PASSED)])

    assert states[("a", "1", "fake")] == "passed"
    assert states[("b", "1", "fake")] == NOT_ATTEMPTED
    assert NOT_ATTEMPTED not in {s.value for s in EvaluationStatus}


# --- coverage -----------------------------------------------------------------

_UNSUPPORTED_NOT_ATTEMPTED = ClaimView("c", "unsupported", "not_attempted")


def _coverage(ctx: EvaluationContext) -> EvaluationResult:
    (result,) = asyncio.run(ClaimsCoverageEvaluator().evaluate(ctx))
    return result


def _claims(*views: ClaimView) -> tuple[ClaimView, ...]:
    return tuple(
        ClaimView(f"c{i}", v.status, v.absent_evidence_reason)
        for i, v in enumerate(views)
    )


@pytest.mark.parametrize(
    ("ctx", "status", "code"),
    [
        (_ctx(()), EvaluationStatus.ABSTAINED, "no_material_claims"),
        (
            _ctx(_claims(_UNSUPPORTED_NOT_ATTEMPTED), artifact_id=None),
            EvaluationStatus.ABSTAINED,
            "no_final_report",
        ),
        (
            _ctx(_claims(*[_UNSUPPORTED_NOT_ATTEMPTED] * 3)),
            EvaluationStatus.ABSTAINED,
            "claim_support_not_attempted",
        ),
        (
            _ctx(
                _claims(_UNSUPPORTED_NOT_ATTEMPTED, ClaimView("x", "supported", None))
            ),
            EvaluationStatus.FAILED,
            "claim_coverage_gap",
        ),
    ],
)
def test_coverage_never_passes_an_unexamined_run(
    ctx: EvaluationContext, status: EvaluationStatus, code: str
) -> None:
    result = _coverage(ctx)

    assert (result.status, result.error_code) == (status, code)
    assert result.score is None


def test_coverage_passes_only_when_every_claim_was_examined() -> None:
    result = _coverage(_ctx(_claims(ClaimView("x", "supported", None))))

    assert (result.status, result.score) == (EvaluationStatus.PASSED, 1.0)


def test_an_unsupported_claim_with_a_real_reason_counts_as_attempted() -> None:
    # Negative control: "unsupported" alone is not "not attempted".
    result = _coverage(_ctx(_claims(ClaimView("x", "unsupported", "no_source_found"))))

    assert result.status is EvaluationStatus.PASSED


def test_coverage_details_count_the_claims() -> None:
    ctx = _ctx(_claims(_UNSUPPORTED_NOT_ATTEMPTED, ClaimView("x", "supported", None)))

    assert _coverage(ctx).details == {
        "material_claims": 2,
        "attempted": 1,
        "not_attempted": 1,
    }


def test_the_coverage_id_is_reproducible_by_other_callers() -> None:
    ctx = _ctx(_claims(ClaimView("x", "supported", None)))

    assert _coverage(ctx).evaluation_result_id == derive_evaluation_result_id(
        run_id="run-1",
        evaluator_id="claims.coverage",
        evaluator_version="1.0.0",
        dimension="claims.coverage",
        artifact_id="artifact-1",
        discriminator="trigger-1|",
    )


def test_coverage_stamps_its_trigger_and_clock_from_the_context() -> None:
    result = _coverage(_ctx(_claims(ClaimView("x", "supported", None))))

    assert (result.trigger, result.completed_at, result.deterministic) == (
        "trigger-1",
        NOW,
        True,
    )


# --- layering -----------------------------------------------------------------


def test_core_evaluation_does_not_import_experiment_tooling() -> None:
    files = list(
        (Path(__file__).resolve().parents[4] / "src/core/evaluation").glob("*.py")
    )
    assert len(files) >= 4, "the scan must cover the package, not an empty glob"
    for file in files:
        for node in ast.walk(ast.parse(file.read_text())):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            assert not any(
                n == "src.evals" or n.startswith("src.evals.") for n in names
            ), f"{file.name} imports experiment tooling"
