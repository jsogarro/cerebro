"""
Feedback optimizer and end-to-end integration tests for the A/B testing system.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.ai_brain.experimentation.integration.agent_framework_integration import (
    AgentExperimentType,
    AgentFrameworkExperimentor,
)
from src.ai_brain.experimentation.monitoring.real_time_dashboard import (
    RealTimeDashboard,
)
from src.ai_brain.experimentation.optimization.feedback_loop_optimizer import (
    FeedbackLoopConfig,
    FeedbackLoopOptimizer,
    OptimizationDecision,
    OptimizationTarget,
)


class TestFeedbackLoopOptimizer:
    """Test the feedback loop optimizer."""

    @pytest.fixture
    def optimizer(self) -> FeedbackLoopOptimizer:
        """Create an optimizer instance for testing."""
        config = FeedbackLoopConfig(
            min_confidence_for_auto_apply=0.95,
            min_confidence_for_recommendation=0.80,
            evaluation_interval_hours=1,
        )
        with (
            patch(
                "src.ai_brain.experimentation.optimization.feedback_loop_optimizer.AgentFrameworkExperimentor"
            ),
            patch(
                "src.ai_brain.experimentation.optimization.feedback_loop_optimizer.SupervisionFeedbackLearner"
            ),
            patch(
                "src.ai_brain.experimentation.optimization.feedback_loop_optimizer.FeedbackLoopOptimizer._start_optimization_loop"
            ),
        ):
            return FeedbackLoopOptimizer(config)

    @pytest.mark.asyncio
    async def test_generate_routing_optimization(
        self, optimizer: FeedbackLoopOptimizer
    ) -> None:
        """Test generating routing strategy optimization decisions."""
        optimizer._get_current_routing_weights = AsyncMock(
            return_value={
                "cost_weight": 0.5,
                "quality_weight": 0.5,
            }
        )
        optimizer.feedback_system.get_optimal_routing_weights = AsyncMock(
            return_value={
                "cost_weight": 0.3,
                "quality_weight": 0.7,
            }
        )
        optimizer._estimate_performance = AsyncMock(side_effect=[0.8, 0.85])

        decision = await optimizer._generate_routing_optimization()

        assert decision is not None
        assert decision.target == OptimizationTarget.ROUTING_WEIGHTS
        assert decision.expected_improvement > 0
        assert decision.risk_level == "low"

    @pytest.mark.asyncio
    async def test_apply_high_confidence_optimization(
        self, optimizer: FeedbackLoopOptimizer
    ) -> None:
        """Test auto-applying high confidence optimizations."""
        decision = OptimizationDecision(
            target=OptimizationTarget.ROUTING_WEIGHTS,
            current_value={"cost": 0.5},
            recommended_value={"cost": 0.3},
            confidence=0.96,
            expected_improvement=10.0,
            risk_level="low",
            rationale="Test optimization",
            experiment_evidence=["exp_001"],
        )
        optimizer._auto_apply_optimization = AsyncMock()

        await optimizer._apply_optimization(decision)

        optimizer._auto_apply_optimization.assert_called_once_with(decision)

    @pytest.mark.asyncio
    async def test_recommend_medium_confidence_optimization(
        self, optimizer: FeedbackLoopOptimizer
    ) -> None:
        """Test recommending medium confidence optimizations."""
        decision = OptimizationDecision(
            target=OptimizationTarget.QUALITY_THRESHOLDS,
            current_value=0.8,
            recommended_value=0.85,
            confidence=0.82,
            expected_improvement=5.0,
            risk_level="medium",
            rationale="Test recommendation",
            experiment_evidence=["exp_002"],
        )
        optimizer._recommend_optimization = AsyncMock()

        await optimizer._apply_optimization(decision)

        optimizer._recommend_optimization.assert_called_once_with(decision)

    @pytest.mark.asyncio
    async def test_gradual_rollout(self, optimizer: FeedbackLoopOptimizer) -> None:
        """Test gradual rollout of optimizations."""
        decision = OptimizationDecision(
            target=OptimizationTarget.API_PATTERN_THRESHOLD,
            current_value=0.9,
            recommended_value=0.7,
            confidence=0.95,
            expected_improvement=8.0,
            risk_level="medium",
            rationale="Test rollout",
            experiment_evidence=["exp_003"],
        )
        optimizer._apply_partial_optimization = AsyncMock()
        optimizer._check_rollout_performance = AsyncMock(return_value=False)
        optimizer.config.enable_gradual_rollout = True
        optimizer.config.initial_rollout_percentage = 10.0
        optimizer.config.rollout_increment = 30.0

        await optimizer._apply_partial_optimization(decision, 10.0)

        optimizer._apply_partial_optimization.assert_called_once()

    @pytest.mark.asyncio
    async def test_rollback_on_degradation(
        self, optimizer: FeedbackLoopOptimizer
    ) -> None:
        """Test rolling back optimizations on performance degradation."""
        decision = OptimizationDecision(
            target=OptimizationTarget.SUPERVISOR_CONFIG,
            current_value={"mode": "sequential"},
            recommended_value={"mode": "parallel"},
            confidence=0.95,
            expected_improvement=10.0,
            risk_level="low",
            rationale="Test",
            experiment_evidence=[],
        )
        optimizer.active_optimizations["supervisor_config"] = decision
        optimizer.rollback_states["supervisor_config"] = {"mode": "sequential"}
        optimizer.baseline_performance["supervisor_config"] = {"performance": 0.85}
        optimizer._get_current_performance = AsyncMock(return_value=0.75)
        optimizer._rollback_optimization = AsyncMock()

        await optimizer._check_for_degradation()

        optimizer._rollback_optimization.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_experiment_results(
        self, optimizer: FeedbackLoopOptimizer
    ) -> None:
        """Test processing experiment results for learning."""
        results = {
            "variants": {
                "control": {"sample_size": 500},
                "treatment": {"sample_size": 500},
            },
            "statistical_analysis": {
                "p_value": 0.02,
                "winning_variant": "treatment",
            },
        }
        optimizer._get_variant_config = AsyncMock(
            return_value={
                "routing_strategy": "quality_focused",
                "parameters": {"quality_weight": 0.7},
            }
        )
        optimizer._process_routing_learnings = AsyncMock()

        await optimizer._process_experiment_results(
            "exp_001",
            AgentExperimentType.ROUTING_STRATEGY.value,
            results,
        )

        optimizer._process_routing_learnings.assert_called_once()


class TestEndToEndIntegration:
    """Test end-to-end integration of the A/B testing system."""

    @pytest.mark.asyncio
    async def test_full_experiment_lifecycle(self) -> None:
        """Test complete experiment lifecycle from creation to optimization."""
        with (
            patch(
                "src.ai_brain.experimentation.integration.agent_framework_integration.AgentExecutionService"
            ),
            patch(
                "src.ai_brain.experimentation.integration.agent_framework_integration.MASRRoutingService"
            ),
            patch(
                "src.ai_brain.experimentation.integration.agent_framework_integration.AgentFrameworkExperimentor._start_background_tasks"
            ),
        ):
            experimentor = AgentFrameworkExperimentor()
            experimentor.experiment_manager.create_experiment = AsyncMock()

        with patch(
            "src.ai_brain.experimentation.monitoring.real_time_dashboard.RealTimeDashboard._start_background_tasks"
        ):
            dashboard = RealTimeDashboard()

        with patch(
            "src.ai_brain.experimentation.optimization.feedback_loop_optimizer.FeedbackLoopOptimizer._start_optimization_loop"
        ), patch(
            "src.ai_brain.experimentation.optimization.feedback_loop_optimizer.AgentFrameworkExperimentor"
        ):
            optimizer = FeedbackLoopOptimizer()

        experimentor.masr_service.get_routing_decision = AsyncMock(
            return_value=SimpleNamespace(
                supervisor_allocations=[SimpleNamespace(supervisor_type="research")],
                estimated_cost=0.01,
                model_dump=lambda: {"supervisor_type": "research"},
            )
        )
        experimentor.supervisor_service.execute_supervisor_task = AsyncMock(
            return_value=SimpleNamespace(
                quality_score=0.9,
                result="Success",
            )
        )

        exp_id = await experimentor.create_routing_experiment(
            name="Integration Test",
            strategies=["balanced", "quality_focused"],
        )
        experimentor.allocation_engine.allocate_variant = AsyncMock(
            return_value=SimpleNamespace(variant_id="balanced")
        )
        with patch.object(dashboard, "_broadcast_to_dashboard", AsyncMock()):
            await dashboard.register_experiment(exp_id, {"type": "routing"})

        for i in range(10):
            result: dict[str, Any] = await experimentor.execute_with_experiment(
                query=f"Test query {i}",
                user_id=f"user_{i}",
                context={"domain": "research"},
            )
            assert result["success"] is True

        with patch.object(dashboard, "_broadcast_to_dashboard", AsyncMock()):
            await dashboard.update_experiment_metrics(
                experiment_id=exp_id,
                variant_metrics={
                    "balanced": {"quality_score": 0.82},
                    "quality_focused": {"quality_score": 0.88},
                },
                sample_sizes={"balanced": 5, "quality_focused": 5},
                statistical_analysis={
                    "p_value": 0.04,
                    "winning_variant": "quality_focused",
                },
            )

        optimizer.baseline_performance["routing"] = {"performance": 0.8}
        await optimizer._generate_optimization_decisions()

        assert exp_id in experimentor.active_experiments
        assert exp_id in dashboard.active_experiments
        assert len(experimentor.results_buffer) == 10

        final_results: dict[str, Any] = await experimentor.stop_experiment(exp_id)
        assert final_results["stop_reason"] == "manual_stop"
        assert exp_id not in experimentor.active_experiments
