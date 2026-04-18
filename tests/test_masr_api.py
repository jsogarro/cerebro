"""
Tests for MASR Dynamic Routing API

Tests all MASR routing intelligence endpoints based on
"MasRouter: Learning to Route LLMs" research patterns.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from src.ai_brain.models.masr import (
    CollaborationMode,
    ModelTier,
    QueryAnalysis,
    QueryComplexity,
    QueryDomain,
    RoutingDecision,
    RoutingStrategy,
)

from src.api.services.masr_routing_service import MASRRoutingService
from src.models.masr_api_models import (
    AvailableStrategy,
    ComplexityAnalysisRequest,
    ComplexityAnalysisResponse,
    CostEstimationRequest,
    CostEstimationResponse,
    ModelInfo,
    ModelsListResponse,
    RouterStatus,
    RoutingDecisionResponse,
    RoutingFeedback,
    RoutingRequest,
    StrategiesListResponse,
    StrategyComparison,
    StrategyEvaluationRequest,
    StrategyEvaluationResponse,
)


@pytest.fixture
def routing_service():
    """Create a routing service instance for testing"""
    return MASRRoutingService()


@pytest.fixture
def mock_query_analysis():
    """Create a mock query analysis result"""
    return QueryAnalysis(
        query="How does climate change affect biodiversity?",
        domain=QueryDomain.RESEARCH,
        complexity=QueryComplexity.MODERATE,
        uncertainty_level=0.3,
        requires_citations=True,
        requires_visualization=False,
        estimated_tokens=500
    )


@pytest.fixture
def mock_routing_decision():
    """Create a mock routing decision"""
    return RoutingDecision(
        domain=QueryDomain.RESEARCH,
        strategy=RoutingStrategy.BALANCED,
        model_tier=ModelTier.STANDARD,
        collaboration_mode=CollaborationMode.HIERARCHICAL,
        agents=[
            {
                "type": "literature_review",
                "supervisor_type": "research",
                "worker_count": 3,
                "refinement_rounds": 2,
                "estimated_latency_ms": 1500
            },
            {
                "type": "synthesis",
                "supervisor_type": "research",
                "worker_count": 2,
                "refinement_rounds": 1,
                "estimated_latency_ms": 1000
            }
        ],
        estimated_cost=0.15,
        estimated_latency=2.5,
        confidence_score=0.85
    )


class TestRoutingDecision:
    """Test routing decision endpoint"""
    
    @pytest.mark.asyncio
    async def test_get_routing_decision_success(self, routing_service, mock_query_analysis, mock_routing_decision):
        """Test successful routing decision"""
        # Mock the router methods
        with (
            patch.object(routing_service.router, 'analyze_query', new_callable=AsyncMock) as mock_analyze,
            patch.object(routing_service.router, 'select_strategy', new_callable=AsyncMock) as mock_select,
            patch.object(routing_service.router, 'route', new_callable=AsyncMock) as mock_route,
        ):
            mock_analyze.return_value = mock_query_analysis
            mock_select.return_value = RoutingStrategy.BALANCED
            mock_route.return_value = mock_routing_decision

            request = RoutingRequest(
                query="How does climate change affect biodiversity?",
                context={"user_id": "test123"},
                max_cost=1.0,
                min_quality=0.8
            )

            response = await routing_service.get_routing_decision(request)

            assert isinstance(response, RoutingDecisionResponse)
            assert response.domain == QueryDomain.RESEARCH
            assert response.strategy == RoutingStrategy.BALANCED
            assert response.complexity == QueryComplexity.MODERATE
            assert len(response.supervisor_allocations) == 2
            assert response.estimated_cost == 0.15
            assert response.estimated_latency_ms == 2500
            assert 0 <= response.confidence_score <= 1
            assert response.routing_id is not None
            assert response.reasoning is not None
    
    @pytest.mark.asyncio
    async def test_get_routing_decision_with_strategy_override(self, routing_service, mock_query_analysis, mock_routing_decision):
        """Test routing decision with strategy override"""
        mock_routing_decision.strategy = RoutingStrategy.COST_EFFICIENT
        
        with (
            patch.object(routing_service.router, 'analyze_query', new_callable=AsyncMock) as mock_analyze,
            patch.object(routing_service.router, 'route', new_callable=AsyncMock) as mock_route,
        ):
            mock_analyze.return_value = mock_query_analysis
            mock_route.return_value = mock_routing_decision

            request = RoutingRequest(
                query="Simple query",
                strategy=RoutingStrategy.COST_EFFICIENT
            )

            response = await routing_service.get_routing_decision(request)

            assert response.strategy == RoutingStrategy.COST_EFFICIENT
            # Verify select_strategy was not called since we provided override
            mock_route.assert_called_once()


class TestCostEstimation:
    """Test cost estimation endpoint"""
    
    @pytest.mark.asyncio
    async def test_estimate_cost_with_breakdown(self, routing_service, mock_query_analysis, mock_routing_decision):
        """Test cost estimation with detailed breakdown"""
        with (
            patch.object(routing_service.router, 'analyze_query', new_callable=AsyncMock) as mock_analyze,
            patch.object(routing_service.router, 'select_strategy', new_callable=AsyncMock) as mock_select,
            patch.object(routing_service.router, 'route', new_callable=AsyncMock) as mock_route,
            patch.object(routing_service.cost_optimizer, 'calculate_total_cost') as mock_cost,
        ):
            mock_analyze.return_value = mock_query_analysis
            mock_select.return_value = RoutingStrategy.BALANCED
            mock_route.return_value = mock_routing_decision
            mock_cost.return_value = {
                "model_cost": 0.10,
                "coordination_overhead": 0.03,
                "memory_cost": 0.02,
                "total_cost": 0.15
            }

            request = CostEstimationRequest(
                query="Test query",
                include_breakdown=True,
                include_confidence=True
            )

            response = await routing_service.estimate_cost(request)

            assert isinstance(response, CostEstimationResponse)
            assert response.estimated_cost == 0.15
            assert response.breakdown is not None
            assert response.breakdown.model_costs == 0.10
            assert response.breakdown.coordination_overhead == 0.03
            assert response.breakdown.memory_operations == 0.02
            assert response.breakdown.confidence_interval is not None
            assert len(response.recommendations) > 0
            assert response.confidence_score > 0


class TestStrategyEvaluation:
    """Test strategy evaluation endpoint"""
    
    @pytest.mark.asyncio
    async def test_evaluate_strategies(self, routing_service, mock_query_analysis, mock_routing_decision):
        """Test strategy evaluation and comparison"""
        with (
            patch.object(routing_service.router, 'analyze_query', new_callable=AsyncMock) as mock_analyze,
            patch.object(routing_service.router, 'route', new_callable=AsyncMock) as mock_route,
        ):
            mock_analyze.return_value = mock_query_analysis

            # Mock different decisions for different strategies
            def route_side_effect(query, strategy_override=None, **kwargs):
                decision = Mock(spec=RoutingDecision)
                decision.domain = QueryDomain.RESEARCH
                decision.strategy = strategy_override or RoutingStrategy.BALANCED
                decision.model_tier = ModelTier.STANDARD
                decision.collaboration_mode = CollaborationMode.HIERARCHICAL
                decision.agents = mock_routing_decision.agents

                if strategy_override == RoutingStrategy.COST_EFFICIENT:
                    decision.estimated_cost = 0.05
                    decision.estimated_latency = 3.0
                elif strategy_override == RoutingStrategy.QUALITY_FOCUSED:
                    decision.estimated_cost = 0.30
                    decision.estimated_latency = 4.0
                else:
                    decision.estimated_cost = 0.15
                    decision.estimated_latency = 2.5

                return decision

            mock_route.side_effect = route_side_effect

            request = StrategyEvaluationRequest(
                query="Test query",
                strategies=[
                    RoutingStrategy.COST_EFFICIENT,
                    RoutingStrategy.BALANCED,
                    RoutingStrategy.QUALITY_FOCUSED
                ]
            )

            response = await routing_service.evaluate_strategies(request)

            assert isinstance(response, StrategyEvaluationResponse)
            assert len(response.comparisons) == 3
            assert response.recommended_strategy is not None
            assert response.reasoning is not None
            assert response.trade_offs is not None

            # Check that comparisons have expected fields
            for comparison in response.comparisons:
                assert isinstance(comparison, StrategyComparison)
                assert comparison.strategy in request.strategies
                assert comparison.estimated_cost > 0
                assert 0 <= comparison.estimated_quality <= 1
                assert comparison.estimated_latency_ms > 0
                assert len(comparison.pros) > 0
                assert len(comparison.cons) > 0
                assert 0 <= comparison.recommendation_score <= 1


class TestComplexityAnalysis:
    """Test complexity analysis endpoint"""
    
    @pytest.mark.asyncio
    async def test_analyze_complexity_with_features(self, routing_service, mock_query_analysis):
        """Test complexity analysis with feature breakdown"""
        with patch.object(routing_service.router, 'analyze_query', new_callable=AsyncMock) as mock_analyze:
            mock_analyze.return_value = mock_query_analysis
            
            request = ComplexityAnalysisRequest(
                query="How does climate change affect biodiversity in tropical rainforests?",
                include_features=True,
                include_recommendations=True
            )
            
            response = await routing_service.analyze_complexity(request)
            
            assert isinstance(response, ComplexityAnalysisResponse)
            assert response.complexity == QueryComplexity.MODERATE
            assert 0 <= response.complexity_score <= 1
            assert response.features is not None
            assert response.features.query_length > 0
            assert response.features.reasoning_depth > 0
            assert len(response.features.data_requirements) > 0
            assert response.features.coordination_needs is not None
            assert 0 <= response.features.uncertainty_level <= 1
            assert response.recommended_approach is not None
            assert len(response.routing_recommendations) > 0


class TestFeedbackSubmission:
    """Test feedback submission endpoint"""
    
    @pytest.mark.asyncio
    async def test_submit_feedback_success(self, routing_service, mock_routing_decision):
        """Test successful feedback submission"""
        # Add a routing decision to history
        routing_id = "test-routing-123"
        routing_service.routing_history[routing_id] = mock_routing_decision
        
        with patch.object(routing_service.feedback_learner, 'submit_feedback', new_callable=AsyncMock) as mock_submit:
            feedback = RoutingFeedback(
                routing_id=routing_id,
                actual_cost=0.12,
                actual_latency_ms=2300,
                quality_score=0.88,
                user_satisfaction=0.9,
                error_occurred=False
            )
            
            response = await routing_service.submit_feedback(feedback)
            
            assert response["status"] == "accepted"
            assert response["routing_id"] == routing_id
            assert response["feedback_processed"] is True
            assert response["learning_updated"] is True
            
            mock_submit.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_submit_feedback_unknown_routing_id(self, routing_service):
        """Test feedback submission with unknown routing ID"""
        feedback = RoutingFeedback(
            routing_id="unknown-id",
            actual_cost=0.10,
            actual_latency_ms=1000,
            quality_score=0.85
        )
        
        with pytest.raises(ValueError, match="Unknown routing ID"):
            await routing_service.submit_feedback(feedback)


class TestStrategiesList:
    """Test strategies list endpoint"""
    
    @pytest.mark.asyncio
    async def test_get_available_strategies(self, routing_service):
        """Test getting list of available strategies"""
        response = await routing_service.get_available_strategies()
        
        assert isinstance(response, StrategiesListResponse)
        assert len(response.strategies) == len(RoutingStrategy)
        assert response.default_strategy == RoutingStrategy.BALANCED
        assert response.total_count == len(RoutingStrategy)
        
        for strategy in response.strategies:
            assert isinstance(strategy, AvailableStrategy)
            assert strategy.strategy in RoutingStrategy
            assert strategy.name is not None
            assert strategy.description is not None
            assert strategy.optimization_focus is not None
            assert len(strategy.use_cases) > 0
            assert strategy.trade_offs is not None


class TestModelsList:
    """Test models list endpoint"""
    
    @pytest.mark.asyncio
    async def test_get_available_models(self, routing_service):
        """Test getting list of available models"""
        response = await routing_service.get_available_models()
        
        assert isinstance(response, ModelsListResponse)
        assert response.total_count > 0
        assert len(response.models) == response.total_count
        assert len(response.providers) > 0
        assert len(response.tiers) > 0
        
        for model in response.models:
            assert isinstance(model, ModelInfo)
            assert model.provider is not None
            assert model.model_id is not None
            assert model.tier in ModelTier
            assert model.cost_per_token > 0
            assert model.max_tokens > 0
            assert len(model.capabilities) > 0
            assert model.average_latency_ms > 0
            assert 0 <= model.quality_score <= 1


class TestRouterStatus:
    """Test router status endpoint"""
    
    @pytest.mark.asyncio
    async def test_get_router_status(self, routing_service):
        """Test getting router health and performance status"""
        with patch.object(routing_service.feedback_learner, 'get_metrics', new_callable=AsyncMock) as mock_metrics:
            mock_metrics.return_value = {
                "total_feedback": 100,
                "average_prediction_error": 0.05,
                "learning_rate": 0.01
            }
            
            # Add some metrics data
            routing_service.performance_metrics["balanced"]["total_requests"] = 50
            routing_service.performance_metrics["balanced"]["success_rate"] = 0.95
            routing_service.performance_metrics["balanced"]["average_latency_ms"] = 2000
            
            response = await routing_service.get_router_status()
            
            assert isinstance(response, RouterStatus)
            assert response.status in ["healthy", "degraded", "unhealthy"]
            assert response.uptime_seconds >= 0
            assert response.total_routes >= 0
            assert response.average_latency_ms >= 0
            assert 0 <= response.success_rate <= 1
            assert response.active_supervisors >= 0
            assert len(response.performance_metrics) > 0
            assert len(response.model_availability) > 0
            assert response.learning_metrics is not None


class TestServiceHelpers:
    """Test service helper methods"""
    
    def test_calculate_confidence(self, routing_service, mock_query_analysis, mock_routing_decision):
        """Test confidence score calculation"""
        confidence = routing_service._calculate_confidence(mock_query_analysis, mock_routing_decision)
        assert 0 <= confidence <= 1
        
        # Test with high uncertainty
        mock_query_analysis.uncertainty_level = 0.9
        confidence_high_uncertainty = routing_service._calculate_confidence(mock_query_analysis, mock_routing_decision)
        assert confidence_high_uncertainty < confidence
    
    def test_generate_reasoning(self, routing_service, mock_query_analysis, mock_routing_decision):
        """Test reasoning generation"""
        reasoning = routing_service._generate_reasoning(mock_query_analysis, mock_routing_decision)
        assert isinstance(reasoning, str)
        assert "MODERATE" in reasoning
        assert "RESEARCH" in reasoning
        assert "BALANCED" in reasoning
    
    def test_estimate_quality(self, routing_service, mock_query_analysis):
        """Test quality estimation"""
        quality_balanced = routing_service._estimate_quality(RoutingStrategy.BALANCED, mock_query_analysis)
        quality_focused = routing_service._estimate_quality(RoutingStrategy.QUALITY_FOCUSED, mock_query_analysis)
        quality_efficient = routing_service._estimate_quality(RoutingStrategy.COST_EFFICIENT, mock_query_analysis)
        
        assert quality_focused > quality_balanced > quality_efficient
        assert all(0 <= q <= 1 for q in [quality_balanced, quality_focused, quality_efficient])
    
    def test_get_strategy_pros_cons(self, routing_service):
        """Test getting strategy pros and cons"""
        for strategy in RoutingStrategy:
            pros = routing_service._get_strategy_pros(strategy)
            cons = routing_service._get_strategy_cons(strategy)
            
            assert isinstance(pros, list)
            assert isinstance(cons, list)
            assert len(pros) > 0
            assert len(cons) > 0


class TestErrorHandling:
    """Test error handling in service"""
    
    @pytest.mark.asyncio
    async def test_routing_decision_error(self, routing_service):
        """Test error handling in routing decision"""
        with patch.object(routing_service.router, 'analyze_query', new_callable=AsyncMock) as mock_analyze:
            mock_analyze.side_effect = Exception("Analysis failed")
            
            request = RoutingRequest(query="Test query")
            
            with pytest.raises(Exception, match="Analysis failed"):
                await routing_service.get_routing_decision(request)
    
    @pytest.mark.asyncio
    async def test_cost_estimation_error(self, routing_service):
        """Test error handling in cost estimation"""
        with patch.object(routing_service.router, 'analyze_query', new_callable=AsyncMock) as mock_analyze:
            mock_analyze.side_effect = Exception("Cost calculation failed")
            
            request = CostEstimationRequest(query="Test query")
            
            with pytest.raises(Exception, match="Cost calculation failed"):
                await routing_service.estimate_cost(request)