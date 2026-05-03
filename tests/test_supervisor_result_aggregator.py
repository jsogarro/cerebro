"""Characterization tests for supervisor result aggregation extraction."""

import pytest

from src.api.services.supervisor_coordination_service import (
    SupervisorCoordinationService,
)
from src.api.services.supervisor_result_aggregator import ResultAggregator


def test_result_aggregator_quality_score_for_empty_result() -> None:
    aggregator = ResultAggregator()

    assert aggregator.calculate_quality_score(None, 0.8) == 0.0


def test_result_aggregator_consistency_matches_legacy_formula() -> None:
    aggregator = ResultAggregator()
    results = {
        "research": {"quality_score": 0.8},
        "content": {"quality_score": 0.9},
    }

    assert aggregator.calculate_consistency(results) == pytest.approx(0.975)


@pytest.mark.asyncio
async def test_result_aggregator_synthesizes_weighted_results() -> None:
    aggregator = ResultAggregator()
    results = {
        "research": {"result": "Research output", "quality_score": 0.86},
        "content": {"result": "Content output", "quality_score": 0.9},
    }

    synthesized, consensus = await aggregator.synthesize_results(
        results,
        {"research": 2.0},
    )

    assert synthesized == (
        "Synthesized result combining: "
        "research (weight=2.0): Research output; "
        "content (weight=1.0): Content output"
    )
    assert consensus is True


@pytest.mark.asyncio
async def test_service_keeps_result_aggregator_delegates() -> None:
    service = SupervisorCoordinationService()

    consistency = service._calculate_consistency(
        {
            "research": {"quality_score": 0.7},
            "content": {"quality_score": 0.9},
        }
    )
    synthesized, consensus = await service._synthesize_results(
        {"research": {"result": "A", "quality_score": 0.7}},
        {},
    )

    assert isinstance(service.result_aggregator, ResultAggregator)
    assert consistency == pytest.approx(0.9)
    assert synthesized == "Synthesized result combining: research (weight=1.0): A"
    assert consensus is True
