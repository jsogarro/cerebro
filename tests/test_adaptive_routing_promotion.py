"""Promotion-gate tests for adaptive routing.

The gate is evidence-producing only: even a passing report never changes
runtime configuration or authorizes activation.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.ai_brain.experimentation.eval import adaptive_routing_promotion as gate
from src.ai_brain.experimentation.eval.adaptive_routing_promotion import (
    ArmMeasurement,
    CorpusKind,
    GateStatus,
    PromotionCriteria,
    PromotionPolicySnapshot,
    ReplayCase,
    ReplayCorpus,
    evaluate_promotion,
    write_private_report,
)
from src.ai_brain.router.masr import MASRouter
from src.ai_brain.router.routing_outcome import (
    ADAPTIVE_ARMS,
    ADAPTIVE_OUTCOME_SCHEMA_VERSION,
    ADAPTIVE_POLICY_VERSION,
)
from src.ai_brain.router.routing_types import (
    AdaptiveAllocationProposal,
    CollaborationMode,
)

GENERATED_AT = datetime(2026, 7, 24, 12, tzinfo=UTC)


def _policy_snapshot(**changes: object) -> PromotionPolicySnapshot:
    values: dict[str, object] = {
        "replay_protocol_version": gate.REPLAY_PROTOCOL_VERSION,
        "policy_version": ADAPTIVE_POLICY_VERSION,
        "outcome_schema_version": ADAPTIVE_OUTCOME_SCHEMA_VERSION,
        "ordered_arms": ADAPTIVE_ARMS,
        "minimum_history": 15,
        "minimum_samples_per_arm": 3,
        "performance_threshold": 0.0,
        "maximum_worker_adjust": 2,
        "maximum_parallel_workers": 10,
        "maximum_agents_per_query": 10,
    }
    values.update(changes)
    return PromotionPolicySnapshot(**values)  # type: ignore[arg-type]


def _criteria(**changes: object) -> PromotionCriteria:
    values: dict[str, object] = {
        "criteria_version": "approved-test-v1",
        "policy_version": ADAPTIVE_POLICY_VERSION,
        "outcome_schema_version": ADAPTIVE_OUTCOME_SCHEMA_VERSION,
        "corpus_schema_version": gate.REPLAY_CORPUS_SCHEMA_VERSION,
        "evaluator_name": "neutral-product-evaluator",
        "evaluator_version": "1",
        "policy_snapshot": _policy_snapshot(),
        "required_modes": (CollaborationMode.PARALLEL,),
        "approved_for_promotion": True,
        "training_fraction": 0.5,
        "minimum_total_cases": 6,
        "minimum_training_cases": 3,
        "minimum_heldout_cases": 3,
        "minimum_training_cases_per_mode": 3,
        "minimum_heldout_cases_per_mode": 3,
        "minimum_training_outcomes_per_arm": 3,
        "minimum_mean_quality_delta": -1.0,
        "maximum_mean_cost_delta": 10.0,
        "maximum_mean_latency_delta_ms": 10_000.0,
    }
    values.update(changes)
    return PromotionCriteria(**values)  # type: ignore[arg-type]


def _case(
    index: int,
    *,
    observed_at: datetime | None = None,
    collaboration_mode: CollaborationMode = CollaborationMode.PARALLEL,
) -> ReplayCase:
    return ReplayCase(
        case_id=f"case-{index:02d}",
        observed_at=observed_at or GENERATED_AT + timedelta(minutes=index),
        collaboration_mode=collaboration_mode,
        analytic_worker_count=3,
        arm_measurements={
            arm: ArmMeasurement(
                quality_score=0.70 + (arm + 2) * 0.02,
                measured_cost=0.10 + (arm + 2) * 0.01,
                latency_ms=800 + (arm + 2) * 25,
            )
            for arm in ADAPTIVE_ARMS
        },
    )


def _corpus(**changes: object) -> ReplayCorpus:
    values: dict[str, object] = {
        "corpus_version": "test-corpus-v1",
        "policy_version": ADAPTIVE_POLICY_VERSION,
        "outcome_schema_version": ADAPTIVE_OUTCOME_SCHEMA_VERSION,
        "evaluator_name": "neutral-product-evaluator",
        "evaluator_version": "1",
        "evaluator_available": True,
        "kind": CorpusKind.EVALUATOR,
        "representative": True,
        # Deliberately reversed to prove the gate owns chronological ordering.
        "cases": tuple(reversed([_case(index) for index in range(6)])),
    }
    values.update(changes)
    return ReplayCorpus(**values)  # type: ignore[arg-type]


def _criteria_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = json.loads(json.dumps(asdict(_criteria())))
    payload.update(changes)
    return payload


def _corpus_payload(**changes: object) -> dict[str, object]:
    corpus = _corpus()
    payload: dict[str, object] = {
        "schema_version": corpus.schema_version,
        "corpus_version": corpus.corpus_version,
        "policy_version": corpus.policy_version,
        "outcome_schema_version": corpus.outcome_schema_version,
        "evaluator_name": corpus.evaluator_name,
        "evaluator_version": corpus.evaluator_version,
        "evaluator_available": corpus.evaluator_available,
        "kind": corpus.kind.value,
        "representative": corpus.representative,
        "cases": [
            {
                "case_id": case.case_id,
                "observed_at": case.observed_at.isoformat(),
                "collaboration_mode": case.collaboration_mode.value,
                "analytic_worker_count": case.analytic_worker_count,
                "arm_measurements": {
                    str(arm): asdict(measurement)
                    for arm, measurement in case.arm_measurements.items()
                },
            }
            for case in corpus.cases
        ],
    }
    payload.update(changes)
    return payload


@pytest.mark.asyncio
async def test_report_is_deterministic_for_fixed_seed_and_clock() -> None:
    first = await evaluate_promotion(
        criteria=_criteria(),
        corpus=_corpus(),
        seed=73,
        generated_at=GENERATED_AT,
    )
    second = await evaluate_promotion(
        criteria=_criteria(),
        corpus=_corpus(),
        seed=73,
        generated_at=GENERATED_AT,
    )

    assert first.to_dict() == second.to_dict()
    assert first.activation_approved is False
    assert first.seed == 73


@pytest.mark.asyncio
async def test_heldout_cases_are_never_applied_to_learning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded_ids: list[str] = []
    original = MASRouter.record_routing_outcome

    async def recording_wrapper(self: MASRouter, outcome: object):
        recorded_ids.append(outcome.outcome_id)  # type: ignore[attr-defined]
        return await original(self, outcome)  # type: ignore[arg-type]

    monkeypatch.setattr(MASRouter, "record_routing_outcome", recording_wrapper)
    report = await evaluate_promotion(
        criteria=_criteria(),
        corpus=_corpus(),
        generated_at=GENERATED_AT,
    )

    assert report.metrics is not None
    assert report.metrics.training_cases == 3
    assert report.metrics.heldout_cases == 3
    assert len(recorded_ids) == report.metrics.training_cases * len(ADAPTIVE_ARMS)
    assert len(recorded_ids) == len(set(recorded_ids))
    assert all("promotion-train-" in outcome_id for outcome_id in recorded_ids)
    assert (
        report.metrics.training_last_observed_at
        < report.metrics.heldout_first_observed_at
    )


@pytest.mark.asyncio
async def test_historical_replay_is_not_limited_by_live_outcome_retention() -> None:
    cases = tuple(
        _case(
            index,
            observed_at=GENERATED_AT + timedelta(days=index * 10),
        )
        for index in range(6)
    )

    report = await evaluate_promotion(
        criteria=_criteria(),
        corpus=_corpus(cases=cases),
        generated_at=GENERATED_AT,
    )

    assert report.metrics is not None
    assert report.metrics.training_cases == 3
    assert report.metrics.heldout_cases == 3


@pytest.mark.asyncio
async def test_report_carries_deterministic_target_policy_snapshot_and_digest() -> None:
    report = await evaluate_promotion(
        criteria=_criteria(),
        corpus=_corpus(),
        generated_at=GENERATED_AT,
    )

    assert report.status == GateStatus.INSUFFICIENT_EVIDENCE
    assert report.operator_review_eligible is False
    results = {item.name: item for item in report.criteria}
    assert (
        results["exact_policy_replay_supported"].status
        == GateStatus.INSUFFICIENT_EVIDENCE
    )
    assert report.policy_snapshot == _policy_snapshot()
    assert report.policy_snapshot_sha256 == _policy_snapshot().canonical_sha256()
    assert len(report.policy_snapshot_sha256) == 64
    assert report.policy_snapshot.minimum_history == 15
    assert report.policy_snapshot.minimum_samples_per_arm == 3
    assert report.policy_snapshot.ordered_arms == ADAPTIVE_ARMS
    assert report.metrics is not None
    assert {
        item.arm: item.applied_outcomes
        for item in report.metrics.mode_arm_training_counts
        if item.collaboration_mode == CollaborationMode.PARALLEL.value
    } == dict.fromkeys(ADAPTIVE_ARMS, 3)


@pytest.mark.asyncio
async def test_single_mode_evidence_cannot_pass_multi_mode_scope() -> None:
    report = await evaluate_promotion(
        criteria=_criteria(
            required_modes=(
                CollaborationMode.PARALLEL,
                CollaborationMode.HIERARCHICAL,
            )
        ),
        corpus=_corpus(),
        generated_at=GENERATED_AT,
    )

    results = {item.name: item for item in report.criteria}
    assert report.status == GateStatus.INSUFFICIENT_EVIDENCE
    assert report.metrics is not None
    assert {
        item.collaboration_mode: (item.training_cases, item.heldout_cases)
        for item in report.metrics.mode_case_counts
    }[CollaborationMode.HIERARCHICAL.value] == (0, 0)
    assert (
        results["hierarchical.minimum_training_cases"].status
        == GateStatus.INSUFFICIENT_EVIDENCE
    )
    assert (
        results["hierarchical.arm_0.minimum_training_outcomes"].status
        == GateStatus.INSUFFICIENT_EVIDENCE
    )


@pytest.mark.asyncio
async def test_out_of_scope_fixed_mode_corpus_is_insufficient_before_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_replay(*args: object, **kwargs: object) -> None:
        raise AssertionError("out-of-scope evidence must not be replayed")

    monkeypatch.setattr(gate, "_replay", unexpected_replay)
    cases = list(_corpus().cases)
    cases[0] = replace(cases[0], collaboration_mode=CollaborationMode.DIRECT)

    report = await evaluate_promotion(
        criteria=_criteria(),
        corpus=_corpus(cases=tuple(cases)),
        generated_at=GENERATED_AT,
    )

    assert report.status == GateStatus.INSUFFICIENT_EVIDENCE
    assert report.criteria[0].name == "promotion_scope_compatibility"
    assert report.criteria[0].observed == 1


@pytest.mark.asyncio
async def test_per_arm_shortage_cannot_pass() -> None:
    report = await evaluate_promotion(
        criteria=_criteria(minimum_training_outcomes_per_arm=4),
        corpus=_corpus(),
        generated_at=GENERATED_AT,
    )

    results = {item.name: item for item in report.criteria}
    assert report.status == GateStatus.INSUFFICIENT_EVIDENCE
    assert (
        results["parallel.arm_-2.minimum_training_outcomes"].status
        == GateStatus.INSUFFICIENT_EVIDENCE
    )


def test_policy_snapshot_rejects_unsupported_versions_arms_and_bounds() -> None:
    with pytest.raises(ValueError, match="replay protocol"):
        _policy_snapshot(replay_protocol_version="future")
    with pytest.raises(ValueError, match="ordered_arms"):
        _policy_snapshot(ordered_arms=(-1, 0, 1))
    with pytest.raises(ValueError, match="maximum_worker_adjust"):
        _policy_snapshot(maximum_worker_adjust=1)
    with pytest.raises(ValueError, match="performance_threshold"):
        _policy_snapshot(performance_threshold=float("nan"))


@pytest.mark.asyncio
async def test_extreme_finite_inputs_cannot_overflow_into_promotion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def force_positive_arm(
        self: MASRouter, **kwargs: object
    ) -> AdaptiveAllocationProposal:
        return AdaptiveAllocationProposal(
            experiment_id="adaptive_allocation_parallel",
            analytic_baseline_count=3,
            memory_baseline_count=3,
            proposed_arm=-2,
            proposed_worker_count=1,
            applied_arm=-2,
            applied_worker_count=1,
            allocation_probability=1.0,
            ready=True,
            safety_check_passed=True,
        )

    monkeypatch.setattr(
        MASRouter,
        "_get_adaptive_allocation_adjustment",
        force_positive_arm,
    )
    cases = []
    for case in _corpus().cases:
        measurements = dict(case.arm_measurements)
        measurements[0] = replace(measurements[0], latency_ms=0)
        measurements[-2] = replace(measurements[-2], latency_ms=10**400)
        cases.append(replace(case, arm_measurements=measurements))

    report = await evaluate_promotion(
        criteria=_criteria(),
        corpus=_corpus(cases=tuple(cases)),
        generated_at=GENERATED_AT,
    )

    assert report.status == GateStatus.INSUFFICIENT_EVIDENCE
    assert report.criteria[0].name == "replay_execution"
    assert "Infinity" not in json.dumps(report.to_dict(), allow_nan=False)


@pytest.mark.asyncio
async def test_incompatible_versions_are_insufficient_and_not_replayed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_replay(*args: object, **kwargs: object) -> None:
        raise AssertionError("incompatible evidence must not be replayed")

    monkeypatch.setattr(gate, "_replay", unexpected_replay)
    report = await evaluate_promotion(
        criteria=_criteria(),
        corpus=_corpus(policy_version="different-policy"),
        generated_at=GENERATED_AT,
    )

    assert report.status == GateStatus.INSUFFICIENT_EVIDENCE
    assert report.criteria[0].name == "version_compatibility"
    assert report.activation_approved is False


def test_unsupported_snapshot_versions_are_rejected_before_replay() -> None:
    payload = _criteria_payload()
    payload["policy_snapshot"]["policy_version"] = "future-policy"  # type: ignore[index]

    with pytest.raises(ValueError, match="unsupported adaptive policy"):
        PromotionCriteria.from_dict(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("criteria", "corpus", "criterion"),
    [
        (None, _corpus(), "inputs_present"),
        (_criteria(), None, "inputs_present"),
        (
            _criteria(approved_for_promotion=False),
            _corpus(),
            "criteria_approved",
        ),
        (
            _criteria(),
            _corpus(evaluator_available=False),
            "product_evaluator_available",
        ),
        (
            _criteria(minimum_heldout_cases=4),
            _corpus(),
            "eligible_sample_readiness",
        ),
    ],
)
async def test_missing_or_unapproved_evidence_is_insufficient(
    criteria: PromotionCriteria | None,
    corpus: ReplayCorpus | None,
    criterion: str,
) -> None:
    report = await evaluate_promotion(
        criteria=criteria,
        corpus=corpus,
        generated_at=GENERATED_AT,
    )

    payload = report.to_dict()
    assert payload["status"] == GateStatus.INSUFFICIENT_EVIDENCE
    assert payload["activation_approved"] is False
    assert payload["operator_review_eligible"] is False
    assert payload["criteria"][0]["name"] == criterion
    assert payload["criteria"][0]["reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [CorpusKind.SYNTHETIC, CorpusKind.FIXTURE])
async def test_synthetic_and_fixture_corpora_are_always_non_promotional(
    kind: CorpusKind,
) -> None:
    report = await evaluate_promotion(
        criteria=_criteria(),
        corpus=_corpus(kind=kind),
        generated_at=GENERATED_AT,
    )

    results = {item.name: item for item in report.criteria}
    assert report.status == GateStatus.INSUFFICIENT_EVIDENCE
    assert report.corpus_kind == kind.value
    assert report.synthetic is (kind == CorpusKind.SYNTHETIC)
    assert report.operator_review_eligible is False
    assert report.activation_approved is False
    assert (
        results["corpus_promotional_eligibility"].status
        == GateStatus.INSUFFICIENT_EVIDENCE
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"approved_for_promotion": "false"},
        {"minimum_total_cases": True},
        {"training_fraction": float("nan")},
        {"maximum_mean_cost_delta": float("inf")},
    ],
)
def test_criteria_parser_rejects_coercion_and_non_finite_values(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        PromotionCriteria.from_dict(_criteria_payload(**changes))


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update(evaluator_available="true"),
        lambda payload: payload.update(representative=1),
        lambda payload: payload.update(cases={}),
        lambda payload: payload.pop("outcome_schema_version"),
        lambda payload: payload["cases"][0].update(analytic_worker_count=True),
        lambda payload: payload["cases"][0]["arm_measurements"]["0"].update(
            measured_cost=float("nan")
        ),
        lambda payload: payload["cases"][0]["arm_measurements"]["0"].update(
            latency_ms=True
        ),
    ],
)
def test_corpus_parser_rejects_wrong_shapes_missing_fields_and_invalid_numbers(
    mutator: object,
) -> None:
    payload = _corpus_payload()
    mutator(payload)  # type: ignore[operator]

    with pytest.raises(ValueError):
        ReplayCorpus.from_dict(payload)


@pytest.mark.asyncio
async def test_replay_errors_are_sanitized_as_insufficient_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed_replay(*args: object, **kwargs: object) -> None:
        raise RuntimeError("sensitive-corpus-content")

    monkeypatch.setattr(gate, "_replay", failed_replay)
    report = await evaluate_promotion(
        criteria=_criteria(),
        corpus=_corpus(),
        generated_at=GENERATED_AT,
    )

    assert report.status == GateStatus.INSUFFICIENT_EVIDENCE
    assert report.criteria[0].name == "replay_execution"
    assert "sensitive-corpus-content" not in json.dumps(report.to_dict())


def test_report_writer_rejects_repository_paths_and_uses_private_mode(
    tmp_path: Path,
) -> None:
    report = gate._empty_report(
        criteria=None,
        corpus=None,
        seed=42,
        generated_at=GENERATED_AT,
        result=gate._result(
            "inputs_present",
            GateStatus.INSUFFICIENT_EVIDENCE,
            "corpus",
            "criteria and corpus",
            "missing required input: corpus",
        ),
    )

    with pytest.raises(ValueError, match="cannot be written in the repo"):
        write_private_report(report, Path("promotion-report.json"))

    output = write_private_report(report, tmp_path / "promotion-report.json")
    assert output.stat().st_mode & 0o777 == 0o600
    assert '"activation_approved": false' in output.read_text(encoding="utf-8")
    json.loads(
        output.read_text(encoding="utf-8"),
        parse_constant=gate._reject_json_constant,
    )

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_private_report(report, output)


def test_report_writer_rejects_symlink_without_modifying_target(tmp_path: Path) -> None:
    report = gate._empty_report(
        criteria=None,
        corpus=None,
        seed=42,
        generated_at=GENERATED_AT,
        result=gate._result(
            "inputs_present",
            GateStatus.INSUFFICIENT_EVIDENCE,
            None,
            "criteria and corpus",
            "missing required input",
        ),
    )
    destination = tmp_path / "destination.json"
    destination.write_text("preserve-me", encoding="utf-8")
    link = tmp_path / "report-link.json"
    os.symlink(destination, link)

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_private_report(report, link)

    assert destination.read_text(encoding="utf-8") == "preserve-me"


def test_cli_requires_an_explicit_output_path() -> None:
    with pytest.raises(SystemExit) as error:
        gate.main([])

    assert error.value.code == 2


def test_cli_returns_nonzero_for_insufficient_evidence(tmp_path: Path) -> None:
    exit_code = gate.main(["--output", str(tmp_path / "report.json")])

    assert exit_code == 3


@pytest.mark.parametrize(
    "invalid_payload",
    [
        "{not-json",
        json.dumps(_criteria_payload(approved_for_promotion="true")),
        json.dumps(_criteria_payload(training_fraction=float("nan"))),
        json.dumps(
            {
                key: value
                for key, value in _criteria_payload().items()
                if key != "policy_version"
            }
        ),
    ],
)
def test_cli_writes_sanitized_report_for_invalid_input(
    tmp_path: Path, invalid_payload: str
) -> None:
    criteria_path = tmp_path / "private-input-name.json"
    criteria_path.write_text(invalid_payload, encoding="utf-8")
    output = tmp_path / "report.json"

    exit_code = gate.main(["--criteria", str(criteria_path), "--output", str(output)])

    payload = json.loads(output.read_text(encoding="utf-8"))
    serialized = json.dumps(payload)
    assert exit_code == 3
    assert payload["status"] == GateStatus.INSUFFICIENT_EVIDENCE
    assert payload["criteria"][0]["name"] == "input_validation"
    assert "private-input-name" not in serialized
    assert "not-json" not in serialized
