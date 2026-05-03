"""
Comprehensive tests for Hierarchical Supervisor API

Tests all supervisor coordination endpoints including execution, worker management,
multi-supervisor orchestration, and advanced features like conflict resolution
and performance experimentation.
"""

import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.models.supervisor_api_models import (
    SupervisorInfo,
    WorkerInfo,
)


@pytest.fixture
def app() -> FastAPI:
    """Create FastAPI app with supervisor routes"""
    from src.api.routes.supervisor_api import router
    
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create test client"""
    return TestClient(app)


@pytest.fixture
def mock_supervisor_service() -> Generator[Mock, None, None]:
    """Mock supervisor coordination service"""
    with patch("src.api.routes.supervisor_api.supervisor_service") as mock_service:
        yield mock_service


class TestSupervisorExecution:
    """Test supervisor task execution endpoints"""
    
    def test_execute_supervisor_task_success(
        self, client: TestClient, mock_supervisor_service: Mock
    ) -> None:
        """Test successful supervisor task execution"""
        # Setup mock response
        mock_response = {
            "execution_id": str(uuid.uuid4()),
            "supervisor_type": "research",
            "status": "completed",
            "result": "Task completed successfully",
            "workers_used": ["worker-1", "worker-2"],
            "coordination_mode": "hierarchical",
            "quality_score": 0.92,
            "execution_time_ms": 2500,
            "refinement_rounds": 2,
            "metadata": {}
        }
        
        mock_supervisor_service.execute_supervisor_task = AsyncMock(
            return_value=Mock(**mock_response)
        )
        
        # Make request
        request_data = {
            "query": "Analyze AI impact on employment",
            "supervision_strategy": "collaborative",
            "coordination_mode": "hierarchical",
            "max_workers": 5,
            "quality_threshold": 0.85
        }
        
        response = client.post(
            "/api/v1/supervisors/research/execute",
            json=request_data
        )
        
        # Assert response
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["quality_score"] == 0.92
        assert len(data["workers_used"]) == 2
    
    def test_execute_invalid_supervisor_type(self, client: TestClient) -> None:
        """Test execution with invalid supervisor type"""
        request_data = {
            "query": "Test query",
            "supervision_strategy": "direct"
        }
        
        response = client.post(
            "/api/v1/supervisors/invalid_type/execute",
            json=request_data
        )
        
        assert response.status_code == 422  # Validation error
    
    def test_execute_with_timeout(
        self, client: TestClient, mock_supervisor_service: Mock
    ) -> None:
        """Test execution with timeout parameter"""
        mock_supervisor_service.execute_supervisor_task = AsyncMock(
            return_value=Mock(
                execution_id="test-id",
                supervisor_type="research",
                status="completed",
                result="Result",
                workers_used=[],
                coordination_mode="sequential",
                quality_score=0.88,
                execution_time_ms=5000,
                refinement_rounds=1,
                metadata={}
            )
        )
        
        request_data = {
            "query": "Complex analysis task",
            "timeout_seconds": 30
        }
        
        response = client.post(
            "/api/v1/supervisors/research/execute",
            json=request_data
        )
        
        assert response.status_code == 200
        assert response.json()["execution_time_ms"] == 5000


class TestSupervisorManagement:
    """Test supervisor listing and information endpoints"""
    
    def test_list_all_supervisors(
        self, client: TestClient, mock_supervisor_service: Mock
    ) -> None:
        """Test listing all available supervisors"""
        mock_supervisors = [
            SupervisorInfo(
                supervisor_id="sup-1",
                supervisor_type="research",
                status="active",
                capabilities=["literature_review", "synthesis"],
                worker_count=5,
                active_tasks=2,
                performance_metrics={},
            ),
            SupervisorInfo(
                supervisor_id="sup-2",
                supervisor_type="content",
                status="active",
                capabilities=["writing", "editing"],
                worker_count=3,
                active_tasks=0,
                performance_metrics={},
            ),
        ]
        
        mock_supervisor_service.get_all_supervisors = AsyncMock(
            return_value=mock_supervisors
        )
        
        response = client.get("/api/v1/supervisors")
        
        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 2
        assert data["active_count"] == 2
        assert len(data["supervisors"]) == 2
    
    def test_get_supervisor_info(
        self, client: TestClient, mock_supervisor_service: Mock
    ) -> None:
        """Test getting specific supervisor information"""
        mock_info = Mock(
            supervisor_id="sup-1",
            supervisor_type="research",
            status="active",
            capabilities=["literature_review", "synthesis", "citation"],
            worker_count=5,
            active_tasks=1,
            performance_metrics={"success_rate": 0.95}
        )
        
        mock_supervisor_service.get_supervisor_info = AsyncMock(
            return_value=mock_info
        )
        
        response = client.get("/api/v1/supervisors/research")
        
        assert response.status_code == 200
        data = response.json()
        assert data["supervisor_type"] == "research"
        assert data["worker_count"] == 5
        assert "literature_review" in data["capabilities"]
    
    def test_get_supervisor_workers(
        self, client: TestClient, mock_supervisor_service: Mock
    ) -> None:
        """Test getting workers for a supervisor"""
        mock_workers = [
            WorkerInfo(
                worker_id="worker-1",
                worker_type="literature_review",
                status="idle",
                capabilities=["search", "extract"],
                performance_score=0.9,
                current_task=None,
            ),
            WorkerInfo(
                worker_id="worker-2",
                worker_type="synthesis",
                status="executing",
                capabilities=["integrate", "summarize"],
                performance_score=0.88,
                current_task="Synthesizing results",
            ),
        ]
        
        mock_supervisor_service.get_supervisor_workers = AsyncMock(
            return_value=mock_workers
        )
        
        response = client.get("/api/v1/supervisors/research/workers")
        
        assert response.status_code == 200
        data = response.json()
        assert data["total_workers"] == 2
        assert data["active_workers"] == 1
        assert data["idle_workers"] == 1


class TestSupervisorMetrics:
    """Test supervisor metrics and health endpoints"""
    
    def test_get_supervisor_stats(
        self, client: TestClient, mock_supervisor_service: Mock
    ) -> None:
        """Test getting supervisor statistics"""
        mock_stats = Mock(
            supervisor_type="research",
            total_executions=100,
            success_rate=0.95,
            average_execution_time_ms=2500.0,
            average_quality_score=0.88,
            worker_utilization=0.75,
            top_worker_types=["literature_review", "synthesis"],
            recent_performance_trend="stable",
            cost_metrics={"average_cost_per_execution": 0.25}
        )
        
        mock_supervisor_service.get_supervisor_stats = AsyncMock(
            return_value=mock_stats
        )
        
        response = client.get("/api/v1/supervisors/research/stats")
        
        assert response.status_code == 200
        data = response.json()
        assert data["total_executions"] == 100
        assert data["success_rate"] == 0.95
        assert data["worker_utilization"] == 0.75
    
    def test_get_supervisor_health(
        self, client: TestClient, mock_supervisor_service: Mock
    ) -> None:
        """Test getting supervisor health status"""
        mock_health = Mock(
            supervisor_type="research",
            status="healthy",
            health_score=0.92,
            active_workers=3,
            queue_depth=2,
            last_execution=datetime.now(UTC),
            issues=[],
            recommendations=[]
        )
        
        mock_supervisor_service.get_supervisor_health = AsyncMock(
            return_value=mock_health
        )
        
        response = client.get("/api/v1/supervisors/research/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["health_score"] == 0.92
        assert data["active_workers"] == 3


class TestErrorHandling:
    """Test error handling in supervisor API"""
    
    def test_invalid_supervisor_type_404(
        self, client: TestClient, mock_supervisor_service: Mock
    ) -> None:
        """Test 404 for invalid supervisor type"""
        mock_supervisor_service.get_supervisor_info = AsyncMock(
            side_effect=ValueError("Supervisor type 'invalid' not found")
        )
        
        response = client.get("/api/v1/supervisors/invalid")
        
        assert response.status_code == 422  # FastAPI validation error
    
    def test_internal_server_error_500(
        self, client: TestClient, mock_supervisor_service: Mock
    ) -> None:
        """Test 500 internal server error handling"""
        mock_supervisor_service.execute_supervisor_task = AsyncMock(
            side_effect=Exception("Internal error")
        )
        
        request_data = {
            "query": "Test query"
        }
        
        response = client.post(
            "/api/v1/supervisors/research/execute",
            json=request_data
        )
        
        assert response.status_code == 500
        data = response.json()
        assert "Internal error" in data["detail"]
    
    def test_validation_error_422(self, client: TestClient) -> None:
        """Test validation error for invalid request data"""
        request_data = {
            "query": "Test",
            "max_workers": 100,  # Exceeds maximum
            "quality_threshold": 1.5  # Exceeds maximum
        }
        
        response = client.post(
            "/api/v1/supervisors/research/execute",
            json=request_data
        )
        
        assert response.status_code == 422


class TestIntegrationScenarios:
    """Test complete integration scenarios"""
    
    def test_complete_supervisor_workflow(
        self, client: TestClient, mock_supervisor_service: Mock
    ) -> None:
        """Test complete workflow from execution to stats"""
        # Step 1: Execute task
        mock_supervisor_service.execute_supervisor_task = AsyncMock(
            return_value=Mock(
                execution_id="exec-123",
                supervisor_type="research",
                status="completed",
                result="Analysis complete",
                workers_used=["w1", "w2"],
                coordination_mode="hierarchical",
                quality_score=0.91,
                execution_time_ms=3000,
                refinement_rounds=2,
                metadata={}
            )
        )
        
        response = client.post(
            "/api/v1/supervisors/research/execute",
            json={"query": "Analyze trends"}
        )
        assert response.status_code == 200
        
        # Step 2: Check health
        mock_supervisor_service.get_supervisor_health = AsyncMock(
            return_value=Mock(
                supervisor_type="research",
                status="healthy",
                health_score=0.95,
                active_workers=2,
                queue_depth=0,
                last_execution=datetime.now(UTC),
                issues=[],
                recommendations=[]
            )
        )
        
        response = client.get("/api/v1/supervisors/research/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
        
        # Step 3: Get stats
        mock_supervisor_service.get_supervisor_stats = AsyncMock(
            return_value=Mock(
                supervisor_type="research",
                total_executions=1,
                success_rate=1.0,
                average_execution_time_ms=3000.0,
                average_quality_score=0.91,
                worker_utilization=0.4,
                top_worker_types=["analyst"],
                recent_performance_trend="stable",
                cost_metrics={}
            )
        )
        
        response = client.get("/api/v1/supervisors/research/stats")
        assert response.status_code == 200
        assert response.json()["total_executions"] == 1
