"""
Tests for supervisor coordination, orchestration, and real-time endpoints.
"""

from collections.abc import Generator
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.models.supervisor_api_models import SupervisionStrategy


@pytest.fixture
def app() -> FastAPI:
    """Create FastAPI app with supervisor routes."""
    from src.api.routes.supervisor_api import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_supervisor_service() -> Generator[Mock, None, None]:
    """Mock supervisor coordination service."""
    with patch("src.api.routes.supervisor_api.supervisor_service") as mock_service:
        yield mock_service


class TestWorkerCoordination:
    """Test worker coordination endpoints."""

    def test_coordinate_workers(
        self, client: TestClient, mock_supervisor_service: Mock
    ) -> None:
        """Test worker coordination request."""
        mock_response = Mock(
            coordination_id="coord-123",
            workers_assigned=[
                {
                    "worker_id": "w1",
                    "worker_type": "analyst",
                    "status": "assigned",
                    "capabilities": [],
                    "performance_score": 0.9,
                    "current_task": None,
                }
            ],
            coordination_plan={"phases": [{"phase": 1, "action": "analyze"}]},
            estimated_completion_time=30,
            status="initiated",
        )

        mock_supervisor_service.coordinate_workers = AsyncMock(
            return_value=mock_response
        )

        request_data = {
            "task": "Analyze market trends",
            "worker_types": ["analyst", "researcher"],
            "coordination_mode": "parallel",
            "refinement_rounds": 2,
        }

        response = client.post(
            "/api/v1/supervisors/analytics/coordinate",
            json=request_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "initiated"
        assert data["estimated_completion_time"] == 30

    def test_coordinate_with_conflict_resolution(
        self, client: TestClient, mock_supervisor_service: Mock
    ) -> None:
        """Test coordination with conflict resolution strategy."""
        mock_supervisor_service.coordinate_workers = AsyncMock(
            return_value=Mock(
                coordination_id="coord-456",
                workers_assigned=[],
                coordination_plan={},
                estimated_completion_time=45,
                status="initiated",
            )
        )

        request_data = {
            "task": "Complex analysis",
            "worker_types": ["analyst"],
            "coordination_mode": "hierarchical",
            "conflict_resolution": "quality_based",
        }

        response = client.post(
            "/api/v1/supervisors/research/coordinate",
            json=request_data,
        )

        assert response.status_code == 200


class TestMultiSupervisorOrchestration:
    """Test multi-supervisor orchestration endpoints."""

    def test_orchestrate_multi_supervisor(
        self, client: TestClient, mock_supervisor_service: Mock
    ) -> None:
        """Test multi-supervisor orchestration."""
        mock_response = Mock(
            orchestration_id="orch-123",
            supervisors_involved=[
                Mock(
                    supervisor_id="sup-1",
                    supervisor_type="research",
                    status="active",
                    capabilities=[],
                    worker_count=5,
                    active_tasks=1,
                    performance_metrics={},
                )
            ],
            individual_results={
                "research": {"result": "Research findings", "quality_score": 0.9}
            },
            synthesized_result="Combined analysis",
            orchestration_time_ms=5000,
            consensus_achieved=True,
            quality_metrics={"average_quality": 0.88},
        )

        mock_supervisor_service.orchestrate_multi_supervisor = AsyncMock(
            return_value=mock_response
        )

        request_data = {
            "query": "Create comprehensive business analysis",
            "supervisor_types": ["research", "analytics", "content"],
            "orchestration_strategy": "collaborative",
            "synthesis_required": True,
        }

        response = client.post(
            "/api/v1/supervisors/multi/orchestrate",
            json=request_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["consensus_achieved"]
        assert data["synthesized_result"] == "Combined analysis"
        assert data["quality_metrics"]["average_quality"] == 0.88


class TestAdvancedCoordination:
    """Test advanced coordination features."""

    def test_optimize_worker_allocation(
        self, client: TestClient, mock_supervisor_service: Mock
    ) -> None:
        """Test worker allocation optimization."""
        mock_response = Mock(
            optimization_id="opt-123",
            recommended_allocation={"specialist": 3, "generalist": 2},
            expected_performance={"quality": 0.9, "speed": 0.8},
            optimization_score=0.85,
            reasoning="Optimized for quality with available resources",
            alternative_allocations=None,
        )

        mock_supervisor_service.optimize_worker_allocation = AsyncMock(
            return_value=mock_response
        )

        request_data = {
            "task_requirements": {
                "complexity": 0.8,
                "domains": ["ai", "ethics"],
                "quality_target": 0.9,
            },
            "available_workers": 10,
            "optimization_goal": "quality",
        }

        response = client.post(
            "/api/v1/supervisors/optimize-allocation",
            json=request_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["optimization_score"] == 0.85
        assert "specialist" in data["recommended_allocation"]

    def test_resolve_conflicts(
        self, client: TestClient, mock_supervisor_service: Mock
    ) -> None:
        """Test conflict resolution between workers."""
        mock_response = Mock(
            conflict_id="conflict-123",
            resolution_strategy="quality_based",
            resolved_output="Best quality output selected",
            confidence_score=0.88,
            resolution_reasoning="Selected highest quality output",
            worker_consensus={"worker-1": 0.9, "worker-2": 0.7},
        )

        mock_supervisor_service.resolve_conflict = AsyncMock(return_value=mock_response)

        request_data = {
            "conflict_id": "conflict-123",
            "worker_outputs": [
                {"worker_id": "w1", "output": "Result A", "confidence": 0.85},
                {"worker_id": "w2", "output": "Result B", "confidence": 0.90},
            ],
            "resolution_strategy": "quality_based",
        }

        response = client.post(
            "/api/v1/supervisors/resolve-conflicts",
            json=request_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["confidence_score"] == 0.88
        assert data["resolution_strategy"] == "quality_based"

    def test_compare_supervisor_performance(
        self, client: TestClient, mock_supervisor_service: Mock
    ) -> None:
        """Test comparing performance across supervisors."""
        mock_response = Mock(
            comparison_id="comp-123",
            supervisors_compared=["research", "analytics"],
            performance_metrics={
                "research": {"success_rate": 0.95, "average_quality": 0.88},
                "analytics": {"success_rate": 0.92, "average_quality": 0.85},
            },
            rankings={"success_rate": ["research", "analytics"]},
            recommendations={"research": "Best for complex analysis"},
            visualization_data=None,
        )

        mock_supervisor_service.compare_supervisor_performance = AsyncMock(
            return_value=mock_response
        )

        response = client.get(
            "/api/v1/supervisors/performance/compare",
            params={"supervisors": ["research", "analytics"]},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["supervisors_compared"]) == 2
        assert "research" in data["performance_metrics"]

    def test_run_coordination_experiment(
        self, client: TestClient, mock_supervisor_service: Mock
    ) -> None:
        """Test running coordination strategy experiments."""
        mock_response = Mock(
            experiment_id="exp-123",
            strategies_tested=[
                SupervisionStrategy.DIRECT,
                SupervisionStrategy.COLLABORATIVE,
            ],
            results={
                "direct": {"quality": 0.85, "speed": 0.9},
                "collaborative": {"quality": 0.92, "speed": 0.7},
            },
            best_strategy=SupervisionStrategy.COLLABORATIVE,
            statistical_significance=0.95,
            recommendations=["Use collaborative for quality-critical tasks"],
            detailed_analysis={},
        )

        mock_supervisor_service.run_experiment = AsyncMock(return_value=mock_response)

        request_data = {
            "query": "Test query for experimentation",
            "strategies_to_test": ["direct", "collaborative"],
            "metrics_to_track": ["quality", "speed"],
            "repetitions": 3,
        }

        response = client.post(
            "/api/v1/supervisors/experiment",
            json=request_data,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["best_strategy"] == "collaborative"
        assert data["statistical_significance"] == 0.95


class TestWebSocketEndpoints:
    """Test WebSocket endpoints for real-time updates."""

    @pytest.mark.asyncio
    async def test_supervisor_websocket_connection(self, app: FastAPI) -> None:
        """Test WebSocket connection for supervisor updates."""
        with TestClient(app) as client, client.websocket_connect(
            "/api/v1/supervisors/research/ws?client_id=test-client"
        ) as websocket:
            data = websocket.receive_json()
            assert data["event_type"] == "connection_established"

            websocket.send_json({"type": "ping"})
            response = websocket.receive_json()
            assert response["type"] == "pong"

    @pytest.mark.asyncio
    async def test_coordination_progress_websocket(self, app: FastAPI) -> None:
        """Test WebSocket for coordination progress updates."""
        with TestClient(app) as client, client.websocket_connect(
            "/api/v1/supervisors/coordination/ws?coordination_id=test-coord"
        ) as websocket:
            data = websocket.receive_json()
            assert data["event_type"] == "connection_established"
            assert data["coordination_id"] == "test-coord"
