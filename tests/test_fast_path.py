"""
Test Fast Path Implementation

Tests for single-agent fast path bypass, including:
- Fast path classifier boundaries
- Escalation valve when quality fails
- Budget enforcement after memory/adaptive adjustments
"""

from unittest.mock import patch

import pytest

from src.ai_brain.router.masr import MASRouter
from src.ai_brain.router.query_analyzer import (
    ComplexityAnalysis,
    ComplexityFactors,
    ComplexityLevel,
)
from src.ai_brain.router.routing_types import CollaborationMode, RoutingStrategy


def make_complexity(
    level: ComplexityLevel = ComplexityLevel.SIMPLE,
    domains: list[str] | None = None,
    subtask_count: int = 1,
    uncertainty: float = 0.2,
    priority_level: str = "normal",
) -> ComplexityAnalysis:
    """Helper to create ComplexityAnalysis with sensible defaults."""
    factors = ComplexityFactors(
        linguistic_complexity=0.1 if level == ComplexityLevel.SIMPLE else 0.5,
        reasoning_depth=0.1 if level == ComplexityLevel.SIMPLE else 0.7,
        domain_breadth=0.1,
        data_requirements=0.1,
        output_complexity=0.1,
        time_sensitivity=0.1,
        quality_requirements=0.1,
    )

    return ComplexityAnalysis(
        score=0.2 if level == ComplexityLevel.SIMPLE else 0.8,
        level=level,
        factors=factors,
        domains=domains or ["research"],
        subtask_count=subtask_count,
        uncertainty=uncertainty,
        priority_level=priority_level,
    )


class TestFastPathClassifier:
    """Test fast path classification logic."""

    @pytest.fixture
    def router(self):
        """Create MASRouter instance."""
        config = {"default_strategy": "cost_efficient"}
        return MASRouter(config=config)

    def test_fast_path_selected_for_trivial_query(self, router):
        """Fast path should be selected for SIMPLE/single-domain/low-uncertainty queries."""
        complexity = make_complexity()

        should_use = router._should_use_fast_path(complexity)
        assert should_use is True, "Trivial query should use fast path"

    def test_fast_path_rejected_for_multi_domain(self, router):
        """Fast path should NOT be used for multi-domain queries."""
        complexity = make_complexity(domains=["research", "content"])

        should_use = router._should_use_fast_path(complexity)
        assert should_use is False, "Multi-domain should NOT use fast path"

    def test_fast_path_accepts_uncertainty_boundary(self, router):
        """The analyzer floors simple queries at exactly 0.3 - must qualify."""
        complexity = make_complexity(uncertainty=0.3)
        assert router._should_use_fast_path(complexity) is True

    def test_fast_path_rejected_for_high_uncertainty(self, router):
        """Fast path should NOT be used for high uncertainty queries."""
        complexity = make_complexity(uncertainty=0.8)

        should_use = router._should_use_fast_path(complexity)
        assert should_use is False, "High uncertainty should NOT use fast path"

    def test_fast_path_rejected_for_critical_priority(self, router):
        """Fast path should NOT be used for critical priority queries."""
        complexity = make_complexity(priority_level="critical")

        should_use = router._should_use_fast_path(complexity)
        assert should_use is False, "Critical priority should NOT use fast path"

    def test_fast_path_rejected_for_complex_query(self, router):
        """Fast path should NOT be used for COMPLEX queries."""
        complexity = make_complexity(level=ComplexityLevel.COMPLEX)

        should_use = router._should_use_fast_path(complexity)
        assert should_use is False, "Complex query should NOT use fast path"

    def test_fast_path_rejected_for_moderate_despite_all_other_signals(self, router):
        """SIMPLE-level is the hard guard: a MODERATE query with every other
        signal green (single domain, single subtask, low uncertainty,
        non-critical) must still NOT fast-path."""
        complexity = make_complexity(
            level=ComplexityLevel.MODERATE,
            domains=["research"],
            subtask_count=1,
            uncertainty=0.2,
            priority_level="normal",
        )
        assert router._should_use_fast_path(complexity) is False

    def test_fast_path_rejected_just_above_uncertainty_ceiling(self, router):
        """Uncertainty just above the ceiling must reject (the accept case at
        exactly 0.3 is covered by test_fast_path_accepts_uncertainty_boundary)."""
        complexity = make_complexity(uncertainty=0.31)
        assert router._should_use_fast_path(complexity) is False

    @patch("src.core.config.get_settings")
    def test_fast_path_respects_feature_flag(self, mock_settings, router):
        """Fast path should respect MASR_FAST_PATH_ENABLED flag."""
        mock_settings.return_value.MASR_FAST_PATH_ENABLED = False

        complexity = make_complexity()

        should_use = router._should_use_fast_path(complexity)
        assert should_use is False, "Feature flag disabled should prevent fast path"


class TestStrategyBudgets:
    """Test per-strategy agent-count budgets."""

    @pytest.fixture
    def router(self):
        """Create MASRouter instance."""
        config = {"default_strategy": "cost_efficient"}
        return MASRouter(config=config)

    def test_debate_roles_track_worker_count_when_budget_below_three(self, router):
        """A DEBATE budget below 3 must not leave worker_types longer than
        worker_count (would make the supervisor expect an unallocated role)."""
        from unittest.mock import patch

        with patch.object(router, "_get_strategy_budget", return_value=2):
            allocation = router._allocate_agents(
                make_complexity(level=ComplexityLevel.MODERATE),
                CollaborationMode.DEBATE,
                None,
                None,
            )
        assert allocation.worker_count == len(allocation.worker_types)
        assert allocation.worker_count == 2

    def test_cost_efficient_budget_caps(self, router):
        """COST_EFFICIENT strategy should have tight budget caps."""
        router.routing_strategy = RoutingStrategy.COST_EFFICIENT

        # PARALLEL mode should cap at 2 for COST_EFFICIENT
        budget = router._get_strategy_budget(
            RoutingStrategy.COST_EFFICIENT, CollaborationMode.PARALLEL
        )
        assert budget == 2, "COST_EFFICIENT PARALLEL should cap at 2"

        # HIERARCHICAL mode should also cap at 2
        budget = router._get_strategy_budget(
            RoutingStrategy.COST_EFFICIENT, CollaborationMode.HIERARCHICAL
        )
        assert budget == 2, "COST_EFFICIENT HIERARCHICAL should cap at 2"

    def test_quality_focused_budget_caps(self, router):
        """QUALITY_FOCUSED strategy should have generous budget caps."""
        # HIERARCHICAL mode should cap at 10 for QUALITY_FOCUSED
        budget = router._get_strategy_budget(
            RoutingStrategy.QUALITY_FOCUSED, CollaborationMode.HIERARCHICAL
        )
        assert budget == 10, "QUALITY_FOCUSED HIERARCHICAL should cap at 10"

        # PARALLEL mode should cap at 4
        budget = router._get_strategy_budget(
            RoutingStrategy.QUALITY_FOCUSED, CollaborationMode.PARALLEL
        )
        assert budget == 4, "QUALITY_FOCUSED PARALLEL should cap at 4"

    def test_budget_enforced_after_adjustments(self, router):
        """Budget cap should be applied AFTER memory/adaptive adjustments."""
        router.routing_strategy = RoutingStrategy.COST_EFFICIENT

        complexity = make_complexity(
            level=ComplexityLevel.MODERATE,
            domains=["research", "content", "analytics"],
            subtask_count=5,
            uncertainty=0.5,
        )

        # Simulate adaptive recommendation trying to increase to 5
        allocation = router._allocate_agents(
            complexity_analysis=complexity,
            collaboration_mode=CollaborationMode.PARALLEL,
            episodic_prior=None,
            adaptive_recommendation=5,  # Adaptive wants 5
        )

        # Budget should cap at 2 for COST_EFFICIENT PARALLEL
        assert allocation.worker_count == 2, (
            "Budget cap should limit to 2 despite adaptive wanting 5"
        )

    def test_debate_budget_enforcement(self, router):
        """DEBATE mode should respect budget caps."""
        router.routing_strategy = RoutingStrategy.COST_EFFICIENT

        complexity = make_complexity(
            level=ComplexityLevel.MODERATE,
            uncertainty=0.8,  # High uncertainty → DEBATE
        )

        allocation = router._allocate_agents(
            complexity_analysis=complexity,
            collaboration_mode=CollaborationMode.DEBATE,
            episodic_prior=None,
            adaptive_recommendation=None,
        )

        # DEBATE normally fixed at 3, budget cap is also 3 for COST_EFFICIENT
        assert allocation.worker_count == 3, "DEBATE should use 3 workers"
