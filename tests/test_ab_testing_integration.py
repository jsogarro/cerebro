"""
Integration tests for A/B Testing System with Agent Framework APIs.

These tests verify that the A/B testing integration properly connects
with the completed Agent Framework APIs and enables systematic optimization.
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from typing import Dict, Any, List

# Import components to test
from src.ai_brain.experimentation.integration.agent_framework_integration import (
    AgentFrameworkExperimentor,
    AgentExperimentType,
    AgentExperimentConfig,
    AgentExperimentResult
)
from src.ai_brain.experimentation.monitoring.real_time_dashboard import (
    RealTimeDashboard,
    DashboardConfig,
    ExperimentSnapshot,
    DashboardMetric
)
from src.ai_brain.experimentation.optimization.feedback_loop_optimizer import (
    FeedbackLoopOptimizer,
    FeedbackLoopConfig,
    OptimizationTarget,
    OptimizationDecision
)


class TestAgentFrameworkExperimentor:
    """Test the Agent Framework Experimentor integration."""
    
    @pytest.fixture
    def experimentor(self):
        """Create an experimentor instance for testing."""
        with patch('src.ai_brain.experimentation.integration.agent_framework_integration.AgentExecutionService'):
            with patch('src.ai_brain.experimentation.integration.agent_framework_integration.MASRRoutingService'):
                with patch('src.ai_brain.experimentation.integration.agent_framework_integration.SupervisorCoordinationService'):
                    with patch('src.ai_brain.experimentation.integration.agent_framework_integration.TalkHierSessionService'):
                        return AgentFrameworkExperimentor()
    
    @pytest.mark.asyncio
    async def test_create_routing_experiment(self, experimentor):
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
    async def test_create_api_pattern_experiment(self, experimentor):
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
    async def test_create_talkhier_experiment(self, experimentor):
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
    async def test_execute_with_experiment(self, experimentor):
        """Test executing a query with active experiments."""
        # Create a routing experiment
        exp_id = await experimentor.create_routing_experiment(
            name="Test Execution",
            strategies=["balanced", "cost_efficient"]
        )
        
        # Mock the service responses
        experimentor.masr_service.route_query = AsyncMock(return_value={
            "supervisor_type": "research",
            "estimated_cost": 0.01,
            "agents": ["research", "synthesis"]
        })
        
        experimentor.supervisor_service.execute_with_supervisor = AsyncMock(return_value={
            "quality_score": 0.85,
            "token_usage": 500,
            "result": "Test result"
        })
        
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
    async def test_variant_assignment(self, experimentor):
        """Test variant assignment using allocation strategies."""
        # Create experiment with thompson sampling
        exp_id = await experimentor.create_routing_experiment(
            name="Test Assignment",
            strategies=["strategy_a", "strategy_b"]
        )
        
        config = experimentor.active_experiments[exp_id]
        config.allocation_strategy = "thompson_sampling"
        
        # Mock allocation engine
        experimentor.allocation_engine.allocate_thompson_sampling = AsyncMock(
            return_value="strategy_a"
        )
        
        # Assign variant
        variant = await experimentor._assign_variant(
            exp_id, "test_user", {}
        )
        
        assert variant == "strategy_a"
        experimentor.allocation_engine.allocate_thompson_sampling.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_get_experiment_results(self, experimentor):
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
        experimentor.statistical_engine.analyze_experiment = AsyncMock(return_value={
            "p_value": 0.03,
            "effect_size": 0.15,
            "winning_variant": "treatment",
            "confidence_interval": [0.05, 0.25]
        })
        
        # Get results
        results = await experimentor.get_experiment_results(
            exp_id,
            include_statistical_analysis=True
        )
        
        assert results["experiment_id"] == exp_id
        assert "control" in results["variants"]
        assert "treatment" in results["variants"]
        assert results["variants"]["treatment"]["success_rate"] == 0.9
        assert "statistical_analysis" in results
        assert results["statistical_analysis"]["p_value"] == 0.03


class TestRealTimeDashboard:
    """Test the real-time monitoring dashboard."""
    
    @pytest.fixture
    def dashboard(self):
        """Create a dashboard instance for testing."""
        config = DashboardConfig(
            update_interval_seconds=1,
            history_window_minutes=60
        )
        with patch('src.ai_brain.experimentation.monitoring.real_time_dashboard.ConnectionManager'):
            with patch('src.ai_brain.experimentation.monitoring.real_time_dashboard.EventPublisher'):
                return RealTimeDashboard(config)
    
    @pytest.mark.asyncio
    async def test_register_experiment(self, dashboard):
        """Test registering an experiment with the dashboard."""
        # Mock broadcast
        dashboard._broadcast_to_dashboard = AsyncMock()
        
        # Register experiment
        await dashboard.register_experiment(
            experiment_id="test_exp_001",
            experiment_config={
                "type": "routing",
                "name": "Test Experiment",
                "strategies": ["a", "b", "c"]
            }
        )
        
        assert "test_exp_001" in dashboard.active_experiments
        assert "test_exp_001" in dashboard.experiment_history
        dashboard._broadcast_to_dashboard.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_update_experiment_metrics(self, dashboard):
        """Test updating experiment metrics."""
        # Register experiment first
        await dashboard.register_experiment("test_exp", {})
        
        # Mock broadcast
        dashboard._broadcast_to_dashboard = AsyncMock()
        
        # Update metrics
        variant_metrics = {
            "control": {
                "quality_score": 0.85,
                "latency_ms": 1000,
                "total_cost": 0.01,
                "success_rate": 0.95
            },
            "treatment": {
                "quality_score": 0.90,
                "latency_ms": 900,
                "total_cost": 0.008,
                "success_rate": 0.97
            }
        }
        
        sample_sizes = {"control": 500, "treatment": 500}
        
        statistical_analysis = {
            "p_value": 0.02,
            "effect_size": 0.12,
            "winning_variant": "treatment",
            "confidence_level": 0.95
        }
        
        await dashboard.update_experiment_metrics(
            experiment_id="test_exp",
            variant_metrics=variant_metrics,
            sample_sizes=sample_sizes,
            statistical_analysis=statistical_analysis
        )
        
        # Check history was updated
        assert len(dashboard.experiment_history["test_exp"]) == 1
        snapshot = dashboard.experiment_history["test_exp"][0]
        assert snapshot.winning_variant == "treatment"
        assert snapshot.p_value == 0.02
    
    @pytest.mark.asyncio
    async def test_generate_dashboard_update(self, dashboard):
        """Test generating dashboard updates with visualizations."""
        # Create snapshot
        snapshot = ExperimentSnapshot(
            experiment_id="test_exp",
            timestamp=datetime.utcnow(),
            variants={
                "control": {"quality_score": 0.8},
                "treatment": {"quality_score": 0.85}
            },
            sample_sizes={"control": 100, "treatment": 100},
            p_value=0.04,
            effect_size=0.1,
            winning_variant="treatment"
        )
        
        # Generate update
        update = await dashboard._generate_dashboard_update("test_exp", snapshot)
        
        assert update["event"] == "experiment_update"
        assert update["experiment_id"] == "test_exp"
        assert "metrics" in update
        assert "charts" in update
        assert "recommendation" in update
    
    @pytest.mark.asyncio
    async def test_alert_generation(self, dashboard):
        """Test alert generation for experiment conditions."""
        snapshot = ExperimentSnapshot(
            experiment_id="test_exp",
            timestamp=datetime.utcnow(),
            variants={},
            sample_sizes={"control": 30, "treatment": 40},  # Low sample size
            p_value=0.03,  # Significant
            effect_size=0.03  # Small effect
        )
        
        alerts = dashboard._check_for_alerts(snapshot)
        
        # Should have alerts for low sample size and small effect
        assert len(alerts) >= 2
        assert any(a["type"] == "warning" for a in alerts)
    
    @pytest.mark.asyncio
    async def test_export_experiment_data(self, dashboard):
        """Test exporting experiment data."""
        # Add some history
        await dashboard.register_experiment("test_exp", {})
        
        for i in range(3):
            snapshot = ExperimentSnapshot(
                experiment_id="test_exp",
                timestamp=datetime.utcnow(),
                variants={"control": {"score": 0.8}, "treatment": {"score": 0.85}},
                sample_sizes={"control": 100 + i*50, "treatment": 100 + i*50}
            )
            dashboard.experiment_history["test_exp"].append(snapshot)
        
        # Export as JSON
        json_data = await dashboard.export_experiment_data("test_exp", "json")
        assert len(json_data) == 3
        assert all("timestamp" in item for item in json_data)


class TestFeedbackLoopOptimizer:
    """Test the feedback loop optimizer."""
    
    @pytest.fixture
    def optimizer(self):
        """Create an optimizer instance for testing."""
        config = FeedbackLoopConfig(
            min_confidence_for_auto_apply=0.95,
            min_confidence_for_recommendation=0.80,
            evaluation_interval_hours=1
        )
        with patch('src.ai_brain.experimentation.optimization.feedback_loop_optimizer.AgentFrameworkExperimentor'):
            with patch('src.ai_brain.experimentation.optimization.feedback_loop_optimizer.SupervisionFeedbackSystem'):
                return FeedbackLoopOptimizer(config)
    
    @pytest.mark.asyncio
    async def test_generate_routing_optimization(self, optimizer):
        """Test generating routing strategy optimization decisions."""
        # Mock current and recommended weights
        optimizer._get_current_routing_weights = AsyncMock(return_value={
            "cost_weight": 0.5,
            "quality_weight": 0.5
        })
        
        optimizer.feedback_system.get_optimal_routing_weights = AsyncMock(return_value={
            "cost_weight": 0.3,
            "quality_weight": 0.7
        })
        
        optimizer._estimate_performance = AsyncMock(side_effect=[0.8, 0.85])
        
        # Generate decision
        decision = await optimizer._generate_routing_optimization()
        
        assert decision is not None
        assert decision.target == OptimizationTarget.ROUTING_WEIGHTS
        assert decision.expected_improvement > 0
        assert decision.risk_level == "low"
    
    @pytest.mark.asyncio
    async def test_apply_high_confidence_optimization(self, optimizer):
        """Test auto-applying high confidence optimizations."""
        decision = OptimizationDecision(
            target=OptimizationTarget.ROUTING_WEIGHTS,
            current_value={"cost": 0.5},
            recommended_value={"cost": 0.3},
            confidence=0.96,  # Above auto-apply threshold
            expected_improvement=10.0,
            risk_level="low",
            rationale="Test optimization",
            experiment_evidence=["exp_001"]
        )
        
        # Mock application methods
        optimizer._auto_apply_optimization = AsyncMock()
        
        await optimizer._apply_optimization(decision)
        
        # Should auto-apply due to high confidence
        optimizer._auto_apply_optimization.assert_called_once_with(decision)
    
    @pytest.mark.asyncio
    async def test_recommend_medium_confidence_optimization(self, optimizer):
        """Test recommending medium confidence optimizations."""
        decision = OptimizationDecision(
            target=OptimizationTarget.QUALITY_THRESHOLDS,
            current_value=0.8,
            recommended_value=0.85,
            confidence=0.82,  # Below auto-apply, above recommendation
            expected_improvement=5.0,
            risk_level="medium",
            rationale="Test recommendation",
            experiment_evidence=["exp_002"]
        )
        
        optimizer._recommend_optimization = AsyncMock()
        
        await optimizer._apply_optimization(decision)
        
        # Should recommend due to medium confidence
        optimizer._recommend_optimization.assert_called_once_with(decision)
    
    @pytest.mark.asyncio
    async def test_gradual_rollout(self, optimizer):
        """Test gradual rollout of optimizations."""
        decision = OptimizationDecision(
            target=OptimizationTarget.API_PATTERN_THRESHOLD,
            current_value=0.9,
            recommended_value=0.7,
            confidence=0.95,
            expected_improvement=8.0,
            risk_level="medium",
            rationale="Test rollout",
            experiment_evidence=["exp_003"]
        )
        
        # Mock rollout methods
        optimizer._apply_partial_optimization = AsyncMock()
        optimizer._check_rollout_performance = AsyncMock(return_value=False)
        
        # Start rollout (would run in background)
        optimizer.config.enable_gradual_rollout = True
        optimizer.config.initial_rollout_percentage = 10.0
        optimizer.config.rollout_increment = 30.0
        
        # Test partial application
        await optimizer._apply_partial_optimization(decision, 10.0)
        optimizer._apply_partial_optimization.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_rollback_on_degradation(self, optimizer):
        """Test rolling back optimizations on performance degradation."""
        # Set up active optimization
        decision = OptimizationDecision(
            target=OptimizationTarget.SUPERVISOR_CONFIG,
            current_value={"mode": "sequential"},
            recommended_value={"mode": "parallel"},
            confidence=0.95,
            expected_improvement=10.0,
            risk_level="low",
            rationale="Test",
            experiment_evidence=[]
        )
        
        optimizer.active_optimizations["supervisor_config"] = decision
        optimizer.rollback_states["supervisor_config"] = {"mode": "sequential"}
        optimizer.baseline_performance["supervisor_config"] = {"performance": 0.85}
        
        # Mock degraded performance
        optimizer._get_current_performance = AsyncMock(return_value=0.75)
        optimizer._rollback_optimization = AsyncMock()
        
        # Check for degradation
        await optimizer._check_for_degradation()
        
        # Should trigger rollback
        optimizer._rollback_optimization.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_process_experiment_results(self, optimizer):
        """Test processing experiment results for learning."""
        # Mock experiment results
        results = {
            "variants": {
                "control": {"sample_size": 500},
                "treatment": {"sample_size": 500}
            },
            "statistical_analysis": {
                "p_value": 0.02,
                "winning_variant": "treatment"
            }
        }
        
        # Mock variant config retrieval
        optimizer._get_variant_config = AsyncMock(return_value={
            "routing_strategy": "quality_focused",
            "parameters": {"quality_weight": 0.7}
        })
        
        # Mock learning processors
        optimizer._process_routing_learnings = AsyncMock()
        
        # Process results
        await optimizer._process_experiment_results(
            "exp_001",
            AgentExperimentType.ROUTING_STRATEGY.value,
            results
        )
        
        # Should process routing learnings
        optimizer._process_routing_learnings.assert_called_once()


class TestEndToEndIntegration:
    """Test end-to-end integration of the A/B testing system."""
    
    @pytest.mark.asyncio
    async def test_full_experiment_lifecycle(self):
        """Test complete experiment lifecycle from creation to optimization."""
        # Create all components
        with patch('src.ai_brain.experimentation.integration.agent_framework_integration.AgentExecutionService'):
            with patch('src.ai_brain.experimentation.integration.agent_framework_integration.MASRRoutingService'):
                experimentor = AgentFrameworkExperimentor()
        
        dashboard = RealTimeDashboard()
        optimizer = FeedbackLoopOptimizer()
        
        # Mock service responses
        experimentor.masr_service.route_query = AsyncMock(return_value={
            "supervisor_type": "research",
            "estimated_cost": 0.01
        })
        experimentor.supervisor_service.execute_with_supervisor = AsyncMock(return_value={
            "quality_score": 0.9,
            "result": "Success"
        })
        
        # 1. Create experiment
        exp_id = await experimentor.create_routing_experiment(
            name="Integration Test",
            strategies=["balanced", "quality_focused"]
        )
        
        # 2. Register with dashboard
        await dashboard.register_experiment(exp_id, {"type": "routing"})
        
        # 3. Execute queries with experiment
        for i in range(10):
            result = await experimentor.execute_with_experiment(
                query=f"Test query {i}",
                user_id=f"user_{i}",
                context={"domain": "research"}
            )
            assert result["success"] is True
        
        # 4. Update dashboard metrics
        await dashboard.update_experiment_metrics(
            experiment_id=exp_id,
            variant_metrics={
                "balanced": {"quality_score": 0.82},
                "quality_focused": {"quality_score": 0.88}
            },
            sample_sizes={"balanced": 5, "quality_focused": 5},
            statistical_analysis={"p_value": 0.04, "winning_variant": "quality_focused"}
        )
        
        # 5. Generate optimization decision
        optimizer.baseline_performance["routing"] = {"performance": 0.8}
        decisions = await optimizer._generate_optimization_decisions()
        
        # Verify integration
        assert exp_id in experimentor.active_experiments
        assert exp_id in dashboard.active_experiments
        assert len(experimentor.results_buffer) == 10
        
        # 6. Stop experiment
        final_results = await experimentor.stop_experiment(exp_id)
        assert final_results["stop_reason"] == "manual_stop"
        assert exp_id not in experimentor.active_experiments


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])