"""Characterization tests for comparative comparison matrix extraction."""

import pytest

from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent
from src.agents.comparative_matrix_builder import ComparisonMatrixBuilder


def test_matrix_builder_normalizes_legacy_matrix_values() -> None:
    builder = ComparisonMatrixBuilder()

    matrix = {
        "A": {"quality": 0.9, "cost": 0.3},
        "B": {"quality": 0.7, "cost": 0.8},
    }

    assert builder.normalize_comparison_matrix(matrix) == {
        "A": {"quality": 1.0, "cost": 0.0},
        "B": {"quality": 0.0, "cost": 1.0},
    }


def test_matrix_builder_calculates_legacy_rankings_and_completeness() -> None:
    builder = ComparisonMatrixBuilder()
    matrix = {
        "A": {"quality": 1.0, "cost": 0.0},
        "B": {"quality": 0.0, "cost": 1.0},
        "C": {"quality": 0.5, "cost": 0.5},
    }

    rankings = builder.calculate_rankings(matrix, ["quality", "cost"])

    assert rankings == {
        "quality": ["A", "C", "B"],
        "cost": ["B", "C", "A"],
        "overall": ["A", "B", "C"],
    }
    assert builder.assess_matrix_completeness(matrix) == 1.0


def test_matrix_builder_preserves_visual_data_shape() -> None:
    builder = ComparisonMatrixBuilder()

    visual_data = builder.generate_visual_data(
        {"A": {"quality": 1.0, "cost": 0.0}},
        ["quality", "cost"],
    )

    assert visual_data == {
        "chart_type": "radar",
        "labels": ["quality", "cost"],
        "data_points": {"A": [1.0, 0.0]},
    }


@pytest.mark.asyncio
async def test_matrix_builder_preserves_statistical_ranking_shape() -> None:
    builder = ComparisonMatrixBuilder()

    rankings = await builder.calculate_statistical_rankings(
        {
            "A": {"quality": 0.9, "cost": 0.5},
            "B": {"quality": 0.4, "cost": 0.8},
        },
        {"success": True, "data_quality": "high", "tests_performed": ["basic"]},
    )

    assert rankings["method"] == "statistical"
    assert rankings["tests_used"] == ["basic"]
    assert rankings["overall_with_confidence"][0]["item"] == "A"


def test_comparative_agent_initializes_matrix_builder() -> None:
    agent = ComparativeAnalysisAgent()

    assert isinstance(agent.matrix_builder, ComparisonMatrixBuilder)
    assert agent._calculate_rankings(
        {"A": {"quality": 1.0}, "B": {"quality": 0.0}},
        ["quality"],
    ) == {"quality": ["A", "B"], "overall": ["A", "B"]}
