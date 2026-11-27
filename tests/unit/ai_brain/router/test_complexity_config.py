"""Complexity weights and level cutoffs configured in settings reach the analyzer."""

import pytest

from src.ai_brain.router.factory import settings_to_router_config
from src.ai_brain.router.query_analyzer import (
    ComplexityLevel,
    QueryComplexityAnalyzer,
)
from src.core.config import get_settings


def _analyzer(**overrides: dict[str, float]) -> QueryComplexityAnalyzer:
    settings = get_settings().model_copy(update=overrides)
    return QueryComplexityAnalyzer(
        settings_to_router_config(settings)["complexity_analyzer"]
    )


@pytest.mark.parametrize(
    ("score", "level"),
    [
        (0.119, ComplexityLevel.SIMPLE),
        (0.12, ComplexityLevel.MODERATE),
        (0.219, ComplexityLevel.MODERATE),
        (0.22, ComplexityLevel.COMPLEX),
    ],
)
def test_default_settings_keep_the_calibrated_cutoffs(score, level):
    assert _analyzer()._determine_complexity_level(score) is level


def test_configured_thresholds_move_the_cutoffs():
    analyzer = _analyzer(
        MASR_COMPLEXITY_THRESHOLDS={"simple": 0.5, "moderate": 0.9, "complex": 1.0}
    )
    assert analyzer._determine_complexity_level(0.4) is ComplexityLevel.SIMPLE
    assert analyzer._determine_complexity_level(0.6) is ComplexityLevel.MODERATE


def test_configured_weights_reach_the_analyzer():
    weights = {**get_settings().MASR_COMPLEXITY_WEIGHTS, "reasoning": 0.5}
    assert _analyzer(MASR_COMPLEXITY_WEIGHTS=weights).weights["reasoning"] == 0.5


def test_settings_defaults_are_the_calibrated_cutoffs():
    thresholds = get_settings().MASR_COMPLEXITY_THRESHOLDS
    assert (thresholds["simple"], thresholds["moderate"]) == (0.12, 0.22)


@pytest.mark.parametrize(
    "thresholds",
    [
        {"simple": 0.1},
        {"moderate": 0.2},
        {"simple": 0.5, "moderate": 0.2},
        {"simple": 0.2, "moderate": 0.2},
    ],
)
def test_incomplete_or_inverted_thresholds_fail_settings_validation(thresholds):
    from pydantic import ValidationError

    from src.core.config import Settings

    with pytest.raises(ValidationError, match="MASR_COMPLEXITY_THRESHOLDS"):
        Settings(MASR_COMPLEXITY_THRESHOLDS=thresholds)
