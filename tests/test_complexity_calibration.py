"""Regression tests for query-complexity calibration.

The analyzer previously scored nearly every query "simple" (level thresholds of
0.3/0.7 against a factor-score distribution that rarely exceeded ~0.2). After
recalibration, queries span simple/moderate/complex sensibly.
"""

import pytest

from src.ai_brain.router.query_analyzer import ComplexityLevel, QueryComplexityAnalyzer


@pytest.fixture
def analyzer() -> QueryComplexityAnalyzer:
    return QueryComplexityAnalyzer()


async def test_trivial_query_is_simple(analyzer) -> None:
    result = await analyzer.analyze("What is 2+2?", {})
    assert result.level == ComplexityLevel.SIMPLE


async def test_multi_part_query_is_at_least_moderate(analyzer) -> None:
    result = await analyzer.analyze(
        "Compare and contrast two machine learning approaches for fraud "
        "detection, with tradeoffs.",
        {},
    )
    assert result.level in (ComplexityLevel.MODERATE, ComplexityLevel.COMPLEX)
    assert result.level != ComplexityLevel.SIMPLE


async def test_deep_technical_query_is_not_simple(analyzer) -> None:
    """The pre-fix bug: this scored 0.21 -> simple."""
    result = await analyzer.analyze(
        "Design a provably-correct distributed consensus protocol, formally "
        "verify safety and liveness under Byzantine faults, and analyze "
        "performance across network partitions and varying node counts.",
        {},
    )
    assert result.level in (ComplexityLevel.MODERATE, ComplexityLevel.COMPLEX)
    assert result.level != ComplexityLevel.SIMPLE


async def test_data_and_output_heavy_query_is_complex(analyzer) -> None:
    result = await analyzer.analyze(
        "Analyze historical revenue data and current market metrics to build a "
        "detailed forecast report with charts and strategic recommendations.",
        {},
    )
    assert result.level == ComplexityLevel.COMPLEX


def test_level_thresholds_are_ordered(analyzer) -> None:
    assert analyzer._determine_complexity_level(0.05) == ComplexityLevel.SIMPLE
    assert analyzer._determine_complexity_level(0.15) == ComplexityLevel.MODERATE
    assert analyzer._determine_complexity_level(0.40) == ComplexityLevel.COMPLEX
