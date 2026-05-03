"""
Integration tests for A/B Testing System with Agent Framework APIs.

These tests verify that the A/B testing integration properly connects
with the completed Agent Framework APIs and enables systematic optimization.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.ai_brain.experimentation.integration.agent_framework_integration import (
    AgentExperimentType,
    AgentFrameworkExperimentor,
)


class TestAgentFrameworkExperimentor:
    """Test the Agent Framework Experimentor integration."""
    
    @pytest.fixture
    def experimentor(self) -> AgentFrameworkExperimentor:
        """Create an experimentor instance for testing."""
        with (
            patch(
                "src.ai_brain.experimentation.integration.agent_framework_integration.AgentExecutionService"
            ),
            patch(
                "src.ai_brain.experimentation.integration.agent_framework_integration.MASRRoutingService"
            ),
            patch(
                "src.ai_brain.experimentation.integration.agent_framework_integration.SupervisorCoordinationService"
            ),
            patch(
                "src.ai_brain.experimentation.integration.agent_framework_integration.TalkHierSessionService"
            ),
            patch(
                "src.ai_brain.experimentation.integration.agent_framework_integration.AgentFrameworkExperimentor._start_background_tasks"
            ),
        ):
            experimentor = AgentFrameworkExperimentor()
            experimentor.experiment_manager.create_experiment = AsyncMock()
            return experimentor
    
    @pytest.mark.asyncio
    async def test_create_routing_experiment(
        self, experimentor: AgentFrameworkExperimentor
    ) -> None:
        """Test creating a routing strategy experiment."""
        # Create experiment
        exp_id = await experimentor.create_routing_experiment(
            name="Test Routing Experiment",
            strategies=["cost_efficient", "quality_focused", "balanced"],
            target_domains=["research", "analytics"],
            duration_days=7
        )
        
        # Verify experiment was created
        assert exp_id.startswith("routing_")
        assert exp_id in experimentor.active_experiments
        
        # Check configuration
        config = experimentor.active_experiments[exp_id]
        assert config.experiment_type == AgentExperimentType.ROUTING_STRATEGY
        assert len(config.variants) == 3
        assert "cost_efficient" in config.variants
        assert config.query_domains == ["research", "analytics"]
    
    @pytest.mark.asyncio
    async def test_create_api_pattern_experiment(
        self, experimentor: AgentFrameworkExperimentor
    ) -> None:
        """Test creating an API pattern experiment."""
        exp_id = await experimentor.create_api_pattern_experiment(
            name="Test API Pattern",
            primary_weight=0.9,
            bypass_weight=0.1
        )
        
        assert exp_id.startswith("api_pattern_")
        assert exp_id in experimentor.active_experiments
        
        config = experimentor.active_experiments[exp_id]
        assert config.experiment_type == AgentExperimentType.API_PATTERN
        assert "primary_heavy" in config.variants
        assert "balanced" in config.variants
    
    @pytest.mark.asyncio
    async def test_create_talkhier_experiment(
        self, experimentor: AgentFrameworkExperimentor
    ) -> None:
        """Test creating a TalkHier optimization experiment."""
        exp_id = await experimentor.create_talkhier_optimization_experiment(
            name="Test TalkHier",
            min_rounds=1,
            max_rounds=3
        )
        
        assert exp_id.startswith("talkhier_")
        config = experimentor.active_experiments[exp_id]
        assert config.experiment_type == AgentExperimentType.TALKHIER_ROUNDS
        assert len(config.variants) == 3  # 1, 2, 3 rounds
    
    @pytest.mark.asyncio
    async def test_execute_with_experiment(
        self, experimentor: AgentFrameworkExperimentor
    ) -> None:
        """Test executing a query with active experiments."""
        # Create a routing experiment
        exp_id = await experimentor.create_routing_experiment(
            name="Test Execution",
            strategies=["balanced", "cost_efficient"]
        )
        experimentor.allocation_engine.allocate_variant = AsyncMock(
            return_value=SimpleNamespace(variant_id="balanced")
        )
        
        experimentor.masr_service.get_routing_decision = AsyncMock(
            return_value=SimpleNamespace(
                supervisor_allocations=[SimpleNamespace(supervisor_type="research")],
                estimated_cost=0.01,
                model_dump=lambda: {"supervisor_type": "research"},
            )
        )
        experimentor.supervisor_service.execute_supervisor_task = AsyncMock(
            return_value=SimpleNamespace(
                quality_score=0.85,
                result="Test result",
            )
        )
        
        # Execute with experiment
        result = await experimentor.execute_with_experiment(
            query="Test query about AI",
            user_id="test_user",
            context={"domain": "research", "complexity": "medium"}
        )
        
        # Verify execution
        assert result["success"] is True
        assert "experiments" in result
        assert result["experiments"]["request_id"] is not None
        assert exp_id in result["experiments"]["assignments"]
        
        # Check that result was buffered
        assert len(experimentor.results_buffer) > 0
        buffered_result = experimentor.results_buffer[0]
        assert buffered_result.experiment_id == exp_id
        assert buffered_result.success is True
    
    @pytest.mark.asyncio
    async def test_variant_assignment(
        self, experimentor: AgentFrameworkExperimentor
    ) -> None:
        """Test variant assignment using allocation strategies."""
        # Create experiment with thompson sampling
        exp_id = await experimentor.create_routing_experiment(
            name="Test Assignment",
            strategies=["strategy_a", "strategy_b"]
        )
        
        config = experimentor.active_experiments[exp_id]
        config.allocation_strategy = "thompson_sampling"
        
        experimentor.allocation_engine.allocate_variant = AsyncMock(
            return_value=SimpleNamespace(variant_id="strategy_a")
        )
        
        # Assign variant
        variant = await experimentor._assign_variant(
            exp_id, "test_user", {}
        )
        
        assert variant == "strategy_a"
        experimentor.allocation_engine.allocate_variant.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_experiment_results(
        self, experimentor: AgentFrameworkExperimentor
    ) -> None:
        """Test getting experiment results with statistical analysis."""
        # Create experiment and add mock performance data
        exp_id = await experimentor.create_routing_experiment(
            name="Test Results",
            strategies=["control", "treatment"]
        )
        
        # Add mock performance data
        experimentor.variant_performance[exp_id] = {
            "control": {
                "successes": 80,
                "failures": 20,
                "total_quality": 80.0,
                "total_cost": 1.0,
                "total_latency": 10000.0,
                "count": 100
            },
            "treatment": {
                "successes": 90,
                "failures": 10,
                "total_quality": 90.0,
                "total_cost": 0.8,
                "total_latency": 8000.0,
                "count": 100
            }
        }
        
        # Mock statistical analysis
        experimentor.statistical_engine.comprehensive_analysis = AsyncMock(return_value={
            "p_value": 0.03,
            "effect_size": 0.15,
            "winning_variant": "treatment",
            "confidence_interval": [0.05, 0.25]
        })
        
        # Get results
        results: dict[str, Any] = await experimentor.get_experiment_results(
            exp_id,
            include_statistical_analysis=True
        )
        
        assert results["experiment_id"] == exp_id
        assert "control" in results["variants"]
        assert "treatment" in results["variants"]
        assert results["variants"]["treatment"]["success_rate"] == 0.9
        assert "statistical_analysis" in results
        assert results["statistical_analysis"]["p_value"] == 0.03
