from __future__ import annotations

import pytest

from src.ai_brain.experimentation.statistical.enhanced_statistical_engine import (
    EnhancedStatisticalEngine,
    StatisticalMethod,
    StatisticalTestResult,
)


class _FakeFrequentistAnalyzer:
    def __init__(self, p_values: list[float]) -> None:
        self._p_values = p_values
        self._call_index = 0

    async def analyze_ab_test(
        self,
        control_data: list[float],
        treatment_data: list[float],
        metric_type: str = "continuous",
        alternative: str = "two-sided",
    ) -> StatisticalTestResult:
        p_value = self._p_values[self._call_index]
        self._call_index += 1
        return StatisticalTestResult(
            method=StatisticalMethod.FREQUENTIST_TTEST,
            p_value=p_value,
            confidence_interval=(0.0, 1.0),
            effect_size=0.1,
            statistical_power=0.8,
            test_statistic=1.0,
            is_significant=p_value < 0.05,
            significance_level=0.05,
            sample_size_control=len(control_data),
            sample_size_treatment=len(treatment_data),
        )


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_multiple_comparison_correction_has_no_none_opt_out() -> None:
    engine = EnhancedStatisticalEngine()
    engine.frequentist = _FakeFrequentistAnalyzer([0.03, 0.04])  # type: ignore[assignment]

    results = await engine.comprehensive_analysis(
        {
            "control": [1.0, 1.1, 0.9],
            "variant_a": [1.2, 1.3, 1.1],
            "variant_b": [1.2, 1.4, 1.1],
        },
        multiple_comparison="none",
    )

    assert "none" not in engine.correction_methods
    assert [r.adjusted_p_value for r in results.values()] == [0.06, 0.08]
    assert {r.correction_method for r in results.values()} == {"bonferroni"}
    assert all(not r.is_significant for r in results.values())


@pytest.mark.asyncio  # type: ignore[untyped-decorator]
async def test_single_comparison_still_reports_corrected_p_value() -> None:
    engine = EnhancedStatisticalEngine()
    engine.frequentist = _FakeFrequentistAnalyzer([0.03])  # type: ignore[assignment]

    results = await engine.comprehensive_analysis(
        {
            "control": [1.0, 1.1, 0.9],
            "variant_a": [1.2, 1.3, 1.1],
        }
    )

    result = results["variant_a_vs_control"]
    assert result.adjusted_p_value == pytest.approx(0.03)
    assert result.correction_method == "bonferroni"
    assert result.is_significant is True
