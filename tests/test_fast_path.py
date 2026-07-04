"""
Test Fast Path Implementation

Tests for single-agent fast path bypass, including:
- Fast path classifier boundaries
- Escalation valve when quality fails
- Budget enforcement after memory/adaptive adjustments
- Delegation contract validation
"""

from unittest.mock import MagicMock, patch

import pytest

from src.agents.delegation_contract import DelegationContract
from src.agents.models import AgentTask
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


class TestDelegationContract:
    """Test delegation contract validation."""

    def test_valid_contract_passes_validation(self):
        """Complete contract should pass validation."""
        contract = DelegationContract(
            objective="Extract key financial metrics from the earnings report",
            output_format="JSON with fields: revenue, profit, eps, guidance",
            tool_guidance="Use PDF parser tool; focus on financial tables",
            task_boundaries="Q4 2023 earnings only; ignore forward-looking statements",
        )

        errors = contract.validate(mode="strict")
        assert len(errors) == 0, "Valid contract should have no errors"

    def test_missing_objective_fails_validation(self):
        """Missing objective should fail validation."""
        contract = DelegationContract(
            objective="",  # Missing
            output_format="JSON format",
            tool_guidance="Use available tools",
            task_boundaries="Stay in scope",
        )

        errors = contract.validate(mode="strict")
        assert len(errors) > 0, "Missing objective should produce errors"
        assert any("objective" in err for err in errors)

    def test_missing_output_format_fails_validation(self):
        """Missing output_format should fail validation."""
        contract = DelegationContract(
            objective="Extract financial data from report",
            output_format="",  # Missing
            tool_guidance="Use PDF parser",
            task_boundaries="Q4 2023 only",
        )

        errors = contract.validate(mode="strict")
        assert len(errors) > 0, "Missing output_format should produce errors"
        assert any("output_format" in err for err in errors)

    def test_auto_fill_defaults(self):
        """Auto-fill should provide sensible defaults for missing fields."""
        contract = DelegationContract(
            objective="",
            output_format="",
            tool_guidance="",
            task_boundaries="",
        )

        filled = contract.auto_fill_defaults(
            agent_type="research", query="What is the capital of France?"
        )

        assert len(filled.objective) > 10, "Auto-filled objective should be non-empty"
        assert len(filled.output_format) > 5, (
            "Auto-filled output_format should be non-empty"
        )
        assert len(filled.tool_guidance) > 5, (
            "Auto-filled tool_guidance should be non-empty"
        )
        assert len(filled.task_boundaries) > 5, (
            "Auto-filled task_boundaries should be non-empty"
        )

    def test_from_agent_task_extraction(self):
        """Should extract contract fields from AgentTask."""
        task = AgentTask(
            id="test-task-1",
            agent_type="research",
            input_data={
                "objective": "Find recent papers on quantum computing",
                "output_format": "Markdown list with citations",
                "query": "quantum computing papers",
            },
            context={
                "tool_guidance": "Use Tavily search, max 5 sources",
                "task_boundaries": "Papers from 2023-2024 only",
            },
        )

        contract = DelegationContract.from_agent_task(task)

        assert contract.objective == "Find recent papers on quantum computing"
        assert contract.output_format == "Markdown list with citations"
        assert contract.tool_guidance == "Use Tavily search, max 5 sources"
        assert contract.task_boundaries == "Papers from 2023-2024 only"


class TestFastPathQualityGate:
    """Test fast path quality gate and escalation."""

    @pytest.fixture
    def service(self):
        """Create DirectExecutionService mock."""
        from src.api.services.direct_execution_service import DirectExecutionService

        return DirectExecutionService(
            masr_router=MagicMock(),
            supervisor_bridge=MagicMock(),
            gemini_service=MagicMock(),
        )

    def test_quality_gate_passes_for_good_response(self, service):
        """Quality gate should pass for adequate responses."""
        response = "This is a comprehensive answer to the query with sufficient detail and proper formatting."

        passes = service._fast_path_passes_quality(response)
        assert passes is True, "Good response should pass quality gate"

    def test_quality_gate_fails_for_short_response(self, service):
        """Quality gate should fail for very short responses."""
        response = "Short"

        passes = service._fast_path_passes_quality(response)
        assert passes is False, "Short response should fail quality gate"

    def test_quality_gate_fails_for_error_response(self, service):
        """Quality gate should fail for error responses."""
        response = "Error: Unable to process the request"

        passes = service._fast_path_passes_quality(response)
        assert passes is False, "Error response should fail quality gate"

    def test_quality_gate_fails_for_apology(self, service):
        """Quality gate should fail for apologetic non-answers."""
        response = "I'm sorry, but I cannot help with that request."

        passes = service._fast_path_passes_quality(response)
        assert passes is False, "Apologetic non-answer should fail quality gate"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
