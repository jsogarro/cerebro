"""Regression tests for the golden research dataset contract."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from src.services.evaluation_regression import (
    AgentOutput,
    GoldenDatasetCase,
    GoldenDatasetRegressionRunner,
    load_golden_dataset,
    score_agent_output,
)

DATASET_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "golden_dataset.json"


def test_golden_dataset_fixture_has_expected_contract() -> None:
    raw = _load_raw_dataset()
    cases = raw.get("cases")

    assert isinstance(cases, list)
    assert len(cases) >= 50

    case_ids: set[str] = set()
    for case_data in cases:
        assert isinstance(case_data, dict)
        case = cast(Mapping[str, object], case_data)
        case_id = case.get("id")

        assert isinstance(case_id, str)
        assert case_id not in case_ids
        case_ids.add(case_id)
        assert isinstance(case.get("query"), str)
        assert _non_empty_list(case.get("domains"))
        assert _non_empty_list(case.get("expected_citations"))
        assert _non_empty_list(case.get("expected_insights"))
        assert _non_empty_list(case.get("trusted_sources"))
        assert isinstance(case.get("quality_thresholds"), dict)


def test_load_golden_dataset_returns_typed_cases() -> None:
    dataset = load_golden_dataset(DATASET_PATH)

    assert dataset.version == "2026-05-03"
    assert len(dataset.cases) >= 50
    assert dataset.cases[0].case_id == "golden-001"
    assert dataset.cases[0].quality_thresholds.min_citations == 1
    assert dataset.cases[0].quality_thresholds.min_insights == 2


def test_score_agent_output_passes_when_expected_evidence_is_present() -> None:
    case = load_golden_dataset(DATASET_PATH).cases[0]

    result = score_agent_output(case, _complete_output(case))

    assert result.passed is True
    assert result.citation_matches == len(case.expected_citations)
    assert result.insight_matches == len(case.expected_insights)


def test_regression_runner_passes_complete_outputs() -> None:
    dataset = load_golden_dataset(DATASET_PATH)
    runner = GoldenDatasetRegressionRunner(dataset)

    result = runner.run(_complete_output)

    assert result.passed is True
    assert result.total_cases == len(dataset.cases)
    assert result.pass_rate == 1.0
    assert result.failed_cases == ()


def test_regression_runner_fails_when_more_than_ten_percent_regresses() -> None:
    dataset = load_golden_dataset(DATASET_PATH)
    runner = GoldenDatasetRegressionRunner(dataset)

    result = runner.run(lambda _case: AgentOutput(text="Unrelated answer.", citations=()))

    assert result.passed is False
    assert result.degradation > 0.10
    assert len(result.failed_cases) == len(dataset.cases)


def _complete_output(case: GoldenDatasetCase) -> AgentOutput:
    return AgentOutput(
        text=" ".join(case.expected_insights),
        citations=case.expected_citations,
    )


def _load_raw_dataset() -> Mapping[str, object]:
    loaded = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return cast(Mapping[str, object], loaded)


def _non_empty_list(value: object) -> bool:
    return isinstance(value, list) and len(value) > 0
