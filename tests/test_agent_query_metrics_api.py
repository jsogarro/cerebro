"""
Tests for agent query, pattern, and metrics API surfaces.
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from src.api.services.agent_execution_service import AgentExecutionService
from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_jwt_service
from src.middleware.tenant_context import TenantContext, get_tenant_context
from src.models.agent_api_models import (
    AgentExecutionResponse,
    AgentType,
    ChainOfAgentsRequest,
    MixtureOfAgentsRequest,
)

AUTH_USER_ID = "user-1"
AUTH_ORG_ID = "11111111-1111-1111-1111-111111111111"
AUTH_TOKEN = "test-token"


class _TestJWTService:
    """Return the fixture tenant identity at the application auth boundary."""

    async def validate_token(self, token: str) -> TokenPayload:
        """Validate the deterministic test bearer token."""
        assert token == AUTH_TOKEN
        now = datetime.now(UTC)
        return TokenPayload(
            sub=AUTH_USER_ID,
            email="test@example.com",
            organization_id=AUTH_ORG_ID,
            jti="test-jti",
            iat=now,
            exp=now + timedelta(minutes=5),
        )


async def _wait_until_mixture(predicate) -> None:
    while not predicate():
        await asyncio.sleep(0)


class TestIntelligentQueryAPI:
    """Test suite for intelligent query API (primary interface)."""

    @pytest.fixture
    def client(self) -> Iterator[TestClient]:
        """Create a raw-ASGI client with a deterministic execution fake."""
        from src.api.main import app
        from src.api.services.direct_execution_service import (
            get_application_direct_execution_service,
        )

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
        # The query routes require an authenticated tenant; these tests are
        # about routing behaviour, so the tenant is supplied directly.
        app.dependency_overrides[get_application_direct_execution_service] = lambda: (
            mock_service
        )
        app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
            user_id=AUTH_USER_ID,
            organization_id=AUTH_ORG_ID,
        )
        app.dependency_overrides[get_jwt_service] = _TestJWTService
        try:
            yield TestClient(app, headers={"Authorization": f"Bearer {AUTH_TOKEN}"})
        finally:
            app.dependency_overrides.pop(
                get_application_direct_execution_service,
                None,
            )
            app.dependency_overrides.pop(get_tenant_context, None)
            app.dependency_overrides.pop(get_jwt_service, None)

    def test_intelligent_research_query(self, client: TestClient) -> None:
        """Test primary intelligent research endpoint."""
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

        assert response.status_code == 200
        assert response.json()["execution_id"] == "test-execution-123"


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

    @pytest.mark.asyncio
    async def test_mixture_reports_total_wall_clock_duration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bounded waves must report elapsed execution, not the longest result."""
        service = AgentExecutionService()
        start = datetime(2026, 7, 27, 12, 0, 0)
        completed = start + timedelta(seconds=13)

        class ControlledDatetime:
            timestamps = iter([start, completed])

            @classmethod
            def now(cls) -> datetime:
                return next(cls.timestamps)

        def response_for(
            agent_type: AgentType, execution_time_seconds: float
        ) -> AgentExecutionResponse:
            return AgentExecutionResponse(
                execution_id=f"fixture-{agent_type.value}",
                agent_type=agent_type,
                status="completed",
                output={"fixture": agent_type.value},
                confidence=0.8,
                quality_score=0.8,
                execution_time_seconds=execution_time_seconds,
                started_at=start,
                completed_at=completed,
            )

        service.execute_single_agent = AsyncMock(
            side_effect=[
                response_for(AgentType.LITERATURE_REVIEW, 5.0),
                response_for(AgentType.METHODOLOGY, 7.0),
            ]
        )
        monkeypatch.setattr(
            "src.api.services.agent_execution_service.datetime", ControlledDatetime
        )

        response = await service.execute_mixture_of_agents(
            MixtureOfAgentsRequest(
                query="Test mixture duration",
                agent_types=[AgentType.LITERATURE_REVIEW, AgentType.METHODOLOGY],
                max_parallel=1,
            )
        )

        assert response.total_execution_time_seconds == 13.0
        assert response.parallel_efficiency == pytest.approx(12 / 13)

    @pytest.mark.asyncio
    async def test_mixture_deadline_stops_admission_and_drains_cancelled_work(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = AgentExecutionService()
        started: list[AgentType] = []
        cancelled: list[AgentType] = []
        release_current_wave = asyncio.Event()
        release_cleanup = asyncio.Event()

        class ControlledTimeout:
            def __init__(self) -> None:
                self.task: asyncio.Task | None = None
                self.expired = False

            async def __aenter__(self):
                self.task = asyncio.current_task()
                return self

            async def __aexit__(self, exc_type, _exc, _traceback) -> bool:
                if self.expired and exc_type is asyncio.CancelledError:
                    raise TimeoutError
                return False

            def expire(self) -> None:
                assert self.task is not None
                self.expired = True
                self.task.cancel()

        controlled_timeout = ControlledTimeout()
        monkeypatch.setattr(
            "src.api.services.agent_execution_service.asyncio.timeout_at",
            lambda _deadline: controlled_timeout,
        )

        def response_for(agent_type: AgentType) -> AgentExecutionResponse:
            completed_at = datetime.now()
            return AgentExecutionResponse(
                execution_id=f"fixture-{agent_type.value}",
                agent_type=agent_type,
                status="completed",
                output={"fixture": agent_type.value},
                confidence=0.8,
                quality_score=0.8,
                execution_time_seconds=1.0,
                started_at=completed_at,
                completed_at=completed_at,
            )

        async def execute_fixture_agent(
            agent_type: AgentType,
            _request,
            *,
            timeout_seconds: float | None = None,
        ) -> AgentExecutionResponse:
            assert timeout_seconds is not None
            started.append(agent_type)
            try:
                if len(started) == 2:
                    if controlled_timeout.task is not None:
                        controlled_timeout.expire()
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
                    if controlled_timeout.task is None:
                        release_current_wave.set()
                await release_current_wave.wait()
                return response_for(agent_type)
            except asyncio.CancelledError:
                cancelled.append(agent_type)
                await release_cleanup.wait()
                raise

        service.execute_single_agent = AsyncMock(side_effect=execute_fixture_agent)
        execution = asyncio.create_task(
            service.execute_mixture_of_agents(
                MixtureOfAgentsRequest(
                    query="Respect one mixture deadline",
                    agent_types=[
                        AgentType.LITERATURE_REVIEW,
                        AgentType.METHODOLOGY,
                        AgentType.SYNTHESIS,
                    ],
                    timeout_seconds=60,
                    max_parallel=2,
                )
            )
        )

        try:
            await _wait_until_mixture(lambda: len(cancelled) == 2 or execution.done())
            assert started == [
                AgentType.LITERATURE_REVIEW,
                AgentType.METHODOLOGY,
            ]
            assert len(cancelled) == 2
            assert not execution.done()
            release_cleanup.set()
            response = await execution
            assert response.status == "failed"
            assert response.errors == [""]
        finally:
            release_current_wave.set()
            release_cleanup.set()
            if not execution.done():
                await execution

    @pytest.mark.asyncio
    async def test_mixture_later_wave_receives_only_remaining_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = AgentExecutionService()
        loop = asyncio.get_running_loop()
        monotonic_now = 200.0
        received_timeouts: list[float | None] = []
        monkeypatch.setattr(loop, "time", lambda: monotonic_now)

        async def execute_fixture_agent(
            agent_type: AgentType,
            _request,
            *,
            timeout_seconds: float | None = None,
        ) -> AgentExecutionResponse:
            nonlocal monotonic_now
            received_timeouts.append(timeout_seconds)
            completed_at = datetime.now()
            monotonic_now += 25.0
            return AgentExecutionResponse(
                execution_id=f"fixture-{agent_type.value}",
                agent_type=agent_type,
                status="completed",
                output={"fixture": agent_type.value},
                confidence=0.8,
                quality_score=0.8,
                execution_time_seconds=25.0,
                started_at=completed_at,
                completed_at=completed_at,
            )

        service.execute_single_agent = AsyncMock(side_effect=execute_fixture_agent)
        response = await service.execute_mixture_of_agents(
            MixtureOfAgentsRequest(
                query="Pass the remaining mixture budget",
                agent_types=[AgentType.LITERATURE_REVIEW, AgentType.METHODOLOGY],
                timeout_seconds=60,
                max_parallel=1,
            )
        )

        assert response.status == "completed"
        assert received_timeouts == [60.0, 35.0]

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
    def client(self) -> Iterator[TestClient]:
        """Create a raw-ASGI client with the established backend override."""
        from src.api.main import app
        from src.api.services.agent_execution_service import (
            get_application_agent_execution_service,
        )

        service = AgentExecutionService()
        app.dependency_overrides[get_application_agent_execution_service] = lambda: (
            service
        )
        app.dependency_overrides[get_jwt_service] = _TestJWTService
        try:
            yield TestClient(app, headers={"Authorization": f"Bearer {AUTH_TOKEN}"})
        finally:
            app.dependency_overrides.pop(
                get_application_agent_execution_service,
                None,
            )
            app.dependency_overrides.pop(get_jwt_service, None)

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
        service.agent_metrics[AgentType.LITERATURE_REVIEW]["total_execution_time"] = (
            450.0
        )

        metrics = await service.get_agent_metrics(AgentType.LITERATURE_REVIEW)

        assert metrics.total_executions == 10
        assert metrics.success_rate == 0.8
        assert metrics.average_execution_time_ms == 45000.0
