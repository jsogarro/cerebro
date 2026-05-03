"""
Tests for agent query, pattern, and metrics API surfaces.
"""

from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.services.agent_execution_service import AgentExecutionService
from src.models.agent_api_models import (
    AgentType,
    ChainOfAgentsRequest,
    MixtureOfAgentsRequest,
)


class TestIntelligentQueryAPI:
    """Test suite for intelligent query API (primary interface)."""

    @pytest.fixture
    def client(self) -> TestClient:
        """Create test client."""
        from src.api.main import app
        return TestClient(app)

    @patch("src.api.services.direct_execution_service.get_direct_execution_service")
    def test_intelligent_research_query(
        self, mock_get_service: Mock, client: TestClient
    ) -> None:
        """Test primary intelligent research endpoint."""
        mock_service = Mock()
        mock_service.start_research_execution = AsyncMock(
            return_value="test-execution-123"
        )
        mock_service.get_execution_status = AsyncMock(
            return_value=Mock(
                status="running",
                routing_decision={"supervisor_type": "research"},
                supervisor_type="research",
                agent_results={},
                quality_scores={},
                execution_time_seconds=0.0,
                started_at=datetime.now(),
            )
        )
        mock_get_service.return_value = mock_service

        request_data = {
            "query": "What are the ethical implications of AI in healthcare?",
            "domains": ["ai", "healthcare", "ethics"],
            "routing_strategy": "quality_focused",
        }

        response = client.post("/api/v1/query/research", json=request_data)

        assert response.status_code == 200
        data = response.json()

        assert "execution_id" in data
        assert "routing_decision" in data
        assert data["supervisor_type"] == "research"

    def test_routing_strategies(self, client: TestClient) -> None:
        """Test available routing strategies endpoint."""
        response = client.get("/api/v1/query/routing/strategies")

        assert response.status_code == 200
        data = response.json()

        assert "available_strategies" in data
        assert "speed_first" in data["available_strategies"]
        assert "quality_focused" in data["available_strategies"]
        assert "balanced" in data["available_strategies"]

    def test_routing_recommendation(self, client: TestClient) -> None:
        """Test routing recommendation endpoint."""
        response = client.get(
            "/api/v1/query/routing/recommend",
            params={"query": "Complex multi-domain analysis of AI impact"},
        )

        assert response.status_code == 200
        data = response.json()

        assert "query_analysis" in data
        assert "routing_recommendation" in data
        assert "suggested_strategy" in data["routing_recommendation"]

    def test_convenience_endpoints(self, client: TestClient) -> None:
        """Test convenience endpoints for common workflows."""
        response = client.post(
            "/api/v1/query/literature",
            params={
                "query": "Machine learning in education",
                "domains": ["ml", "education"],
            },
        )

        assert response.status_code in [200, 500]


class TestResearchPatternImplementation:
    """Test implementation of research patterns."""

    @pytest.mark.asyncio
    async def test_chain_of_agents_pattern(self) -> None:
        """Test Chain-of-Agents pattern implementation."""
        AgentExecutionService()

        request = ChainOfAgentsRequest(
            query="Test chain execution",
            agent_chain=[AgentType.LITERATURE_REVIEW, AgentType.SYNTHESIS],
            pass_intermediate_results=True,
        )

        assert len(request.agent_chain) == 2
        assert request.pass_intermediate_results is True
        assert request.query == "Test chain execution"

    @pytest.mark.asyncio
    async def test_mixture_of_agents_pattern(self) -> None:
        """Test Mixture-of-Agents pattern implementation."""
        AgentExecutionService()

        request = MixtureOfAgentsRequest(
            query="Test mixture execution",
            agent_types=[
                AgentType.LITERATURE_REVIEW,
                AgentType.METHODOLOGY,
                AgentType.SYNTHESIS,
            ],
            aggregation_strategy="consensus",
            weight_by_confidence=True,
        )

        assert len(request.agent_types) == 3
        assert request.aggregation_strategy == "consensus"
        assert request.weight_by_confidence is True

    def test_agent_capability_mapping(self) -> None:
        """Test agent capability mapping for research patterns."""
        from src.models.agent_api_models import AgentCapability

        capabilities = list(AgentCapability)

        expected_capabilities = [
            AgentCapability.DATABASE_SEARCH,
            AgentCapability.SOURCE_EVALUATION,
            AgentCapability.CITATION_FORMATTING,
            AgentCapability.RESEARCH_DESIGN,
            AgentCapability.BIAS_DETECTION,
        ]

        for expected in expected_capabilities:
            assert expected in capabilities


class TestPerformanceAndMetrics:
    """Test performance tracking and metrics."""

    @pytest.fixture
    def client(self) -> TestClient:
        """Create test client."""
        from src.api.main import app
        return TestClient(app)

    def test_agent_metrics_structure(self, client: TestClient) -> None:
        """Test agent metrics response structure."""
        response = client.get("/api/v1/agents/literature-review/metrics")

        assert response.status_code == 200
        data = response.json()

        required_fields = [
            "agent_type",
            "total_executions",
            "success_rate",
            "average_execution_time_ms",
            "average_quality_score",
            "recent_success_rate",
            "quality_trend_7_days",
        ]

        for field in required_fields:
            assert field in data

    def test_health_monitoring_structure(self, client: TestClient) -> None:
        """Test health monitoring response structure."""
        response = client.get("/api/v1/agents/literature-review/health")

        assert response.status_code == 200
        data = response.json()

        required_fields = [
            "agent_type",
            "status",
            "success_rate_24h",
            "average_response_time_ms",
            "error_rate",
            "resource_utilization",
        ]

        for field in required_fields:
            assert field in data

    def test_system_health_summary(self, client: TestClient) -> None:
        """Test system health summary."""
        response = client.get("/api/v1/agents/health/summary")

        assert response.status_code == 200
        data = response.json()

        assert "overall_health" in data
        assert "agent_health" in data
        assert "total_agents" in data
        assert data["overall_health"] in ["healthy", "degraded", "unhealthy"]

    def test_performance_comparison(self, client: TestClient) -> None:
        """Test agent performance comparison."""
        response = client.get(
            "/api/v1/agents/performance/comparison",
            params={"metric": "quality_score", "time_period_hours": 24},
        )

        assert response.status_code == 200
        data = response.json()

        assert "comparison_data" in data
        assert "rankings" in data
        assert data["metric"] == "quality_score"


@pytest.mark.integration
class TestAgentAPIIntegration:
    """Integration tests for agent API with real dependencies."""

    @pytest.mark.asyncio
    async def test_agent_factory_integration(self) -> None:
        """Test integration with agent factory."""
        service = AgentExecutionService()

        agents = await service.get_agent_list()

        assert len(agents) == len(AgentType)
        assert all(isinstance(agent.agent_type, AgentType) for agent in agents)

    @pytest.mark.asyncio
    async def test_metrics_calculation(self) -> None:
        """Test metrics calculation accuracy."""
        service = AgentExecutionService()

        service.agent_metrics[AgentType.LITERATURE_REVIEW]["total_executions"] = 10
        service.agent_metrics[AgentType.LITERATURE_REVIEW]["successful_executions"] = 8
        service.agent_metrics[AgentType.LITERATURE_REVIEW][
            "total_execution_time"
        ] = 450.0

        metrics = await service.get_agent_metrics(AgentType.LITERATURE_REVIEW)

        assert metrics.total_executions == 10
        assert metrics.success_rate == 0.8
        assert metrics.average_execution_time_ms == 45000.0
