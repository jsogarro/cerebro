"""
Comprehensive tests for Hierarchical Supervisor API

Tests all supervisor coordination endpoints including execution, worker management,
multi-supervisor orchestration, and advanced features like conflict resolution
and performance experimentation.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime
import uuid

from fastapi.testclient import TestClient
from fastapi import FastAPI
import pytest_asyncio

from src.models.supervisor_api_models import (
    SupervisorType,
    WorkerStatus,
    CoordinationMode,
    SupervisionStrategy,
    ConflictResolutionStrategy,
    SupervisorExecuteRequest,
    WorkerCoordinationRequest,
    MultiSupervisorOrchestrationRequest,
    WorkerAllocationOptimizationRequest,
    ConflictResolutionRequest,
    ExperimentRequest,
)


@pytest.fixture
def app():
    """Create FastAPI app with supervisor routes"""
    from src.api.routes.supervisor_api import router
    
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    """Create test client"""
    return TestClient(app)


@pytest.fixture
def mock_supervisor_service():
    """Mock supervisor coordination service"""
    with patch("src.api.routes.supervisor_api.supervisor_service") as mock_service:
        yield mock_service


class TestSupervisorExecution:
    """Test supervisor task execution endpoints"""
    
    def test_execute_supervisor_task_success(self, client, mock_supervisor_service):
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
    
    def test_execute_invalid_supervisor_type(self, client):
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
    
    def test_execute_with_timeout(self, client, mock_supervisor_service):
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
    
    def test_list_all_supervisors(self, client, mock_supervisor_service):
        """Test listing all available supervisors"""
        mock_supervisors = [
            Mock(
                supervisor_id="sup-1",
                supervisor_type="research",
                status="active",
                capabilities=["literature_review", "synthesis"],
                worker_count=5,
                active_tasks=2,
                performance_metrics={}
            ),
            Mock(
                supervisor_id="sup-2",
                supervisor_type="content",
                status="active",
                capabilities=["writing", "editing"],
                worker_count=3,
                active_tasks=0,
                performance_metrics={}
            )
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
    
    def test_get_supervisor_info(self, client, mock_supervisor_service):
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
    
    def test_get_supervisor_workers(self, client, mock_supervisor_service):
        """Test getting workers for a supervisor"""
        mock_workers = [
            Mock(
                worker_id="worker-1",
                worker_type="literature_review",
                status="idle",
                capabilities=["search", "extract"],
                performance_score=0.9,
                current_task=None
            ),
            Mock(
                worker_id="worker-2",
                worker_type="synthesis",
                status="executing",
                capabilities=["integrate", "summarize"],
                performance_score=0.88,
                current_task="Synthesizing results"
            )
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


class TestWorkerCoordination:
    """Test worker coordination endpoints"""
    
    def test_coordinate_workers(self, client, mock_supervisor_service):
        """Test worker coordination request"""
        mock_response = Mock(
            coordination_id="coord-123",
            workers_assigned=[
                Mock(
                    worker_id="w1",
                    worker_type="analyst",
                    status="assigned",
                    capabilities=[],
                    performance_score=0.9
                )
            ],
            coordination_plan={"phases": [{"phase": 1, "action": "analyze"}]},
            estimated_completion_time=30,
            status="initiated"
        )
        
        mock_supervisor_service.coordinate_workers = AsyncMock(
            return_value=mock_response
        )
        
        request_data = {
            "task": "Analyze market trends",
            "worker_types": ["analyst", "researcher"],
            "coordination_mode": "parallel",
            "refinement_rounds": 2
        }
        
        response = client.post(
            "/api/v1/supervisors/analytics/coordinate",
            json=request_data
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "initiated"
        assert data["estimated_completion_time"] == 30
    
    def test_coordinate_with_conflict_resolution(self, client, mock_supervisor_service):
        """Test coordination with conflict resolution strategy"""
        mock_supervisor_service.coordinate_workers = AsyncMock(
            return_value=Mock(
                coordination_id="coord-456",
                workers_assigned=[],
                coordination_plan={},
                estimated_completion_time=45,
                status="initiated"
            )
        )
        
        request_data = {
            "task": "Complex analysis",
            "worker_types": ["analyst"],
            "coordination_mode": "hierarchical",
            "conflict_resolution": "quality_based"
        }
        
        response = client.post(
            "/api/v1/supervisors/research/coordinate",
            json=request_data
        )
        
        assert response.status_code == 200


class TestMultiSupervisorOrchestration:
    """Test multi-supervisor orchestration endpoints"""
    
    def test_orchestrate_multi_supervisor(self, client, mock_supervisor_service):
        """Test multi-supervisor orchestration"""
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
                    performance_metrics={}
                )
            ],
            individual_results={
                "research": {"result": "Research findings", "quality_score": 0.9}
            },
            synthesized_result="Combined analysis",
            orchestration_time_ms=5000,
            consensus_achieved=True,
            quality_metrics={"average_quality": 0.88}
        )
        
        mock_supervisor_service.orchestrate_multi_supervisor = AsyncMock(
            return_value=mock_response
        )
        
        request_data = {
            "query": "Create comprehensive business analysis",
            "supervisor_types": ["research", "analytics", "content"],
            "orchestration_strategy": "collaborative",
            "synthesis_required": True
        }
        
        response = client.post(
            "/api/v1/supervisors/multi/orchestrate",
            json=request_data
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["consensus_achieved"] == True
        assert data["synthesized_result"] == "Combined analysis"
        assert data["quality_metrics"]["average_quality"] == 0.88


class TestSupervisorMetrics:
    """Test supervisor metrics and health endpoints"""
    
    def test_get_supervisor_stats(self, client, mock_supervisor_service):
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
    
    def test_get_supervisor_health(self, client, mock_supervisor_service):
        """Test getting supervisor health status"""
        mock_health = Mock(
            supervisor_type="research",
            status="healthy",
            health_score=0.92,
            active_workers=3,
            queue_depth=2,
            last_execution=datetime.utcnow(),
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


class TestAdvancedCoordination:
    """Test advanced coordination features"""
    
    def test_optimize_worker_allocation(self, client, mock_supervisor_service):
        """Test worker allocation optimization"""
        mock_response = Mock(
            optimization_id="opt-123",
            recommended_allocation={"specialist": 3, "generalist": 2},
            expected_performance={"quality": 0.9, "speed": 0.8},
            optimization_score=0.85,
            reasoning="Optimized for quality with available resources",
            alternative_allocations=None
        )
        
        mock_supervisor_service.optimize_worker_allocation = AsyncMock(
            return_value=mock_response
        )
        
        request_data = {
            "task_requirements": {
                "complexity": 0.8,
                "domains": ["ai", "ethics"],
                "quality_target": 0.9
            },
            "available_workers": 10,
            "optimization_goal": "quality"
        }
        
        response = client.post(
            "/api/v1/supervisors/optimize-allocation",
            json=request_data
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["optimization_score"] == 0.85
        assert "specialist" in data["recommended_allocation"]
    
    def test_resolve_conflicts(self, client, mock_supervisor_service):
        """Test conflict resolution between workers"""
        mock_response = Mock(
            conflict_id="conflict-123",
            resolution_strategy="quality_based",
            resolved_output="Best quality output selected",
            confidence_score=0.88,
            resolution_reasoning="Selected highest quality output",
            worker_consensus={"worker-1": 0.9, "worker-2": 0.7}
        )
        
        mock_supervisor_service.resolve_conflict = AsyncMock(
            return_value=mock_response
        )
        
        request_data = {
            "conflict_id": "conflict-123",
            "worker_outputs": [
                {"worker_id": "w1", "output": "Result A", "confidence": 0.85},
                {"worker_id": "w2", "output": "Result B", "confidence": 0.90}
            ],
            "resolution_strategy": "quality_based"
        }
        
        response = client.post(
            "/api/v1/supervisors/resolve-conflicts",
            json=request_data
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["confidence_score"] == 0.88
        assert data["resolution_strategy"] == "quality_based"
    
    def test_compare_supervisor_performance(self, client, mock_supervisor_service):
        """Test comparing performance across supervisors"""
        mock_response = Mock(
            comparison_id="comp-123",
            supervisors_compared=["research", "analytics"],
            performance_metrics={
                "research": {"success_rate": 0.95, "average_quality": 0.88},
                "analytics": {"success_rate": 0.92, "average_quality": 0.85}
            },
            rankings={"success_rate": ["research", "analytics"]},
            recommendations={"research": "Best for complex analysis"},
            visualization_data=None
        )
        
        mock_supervisor_service.compare_supervisor_performance = AsyncMock(
            return_value=mock_response
        )
        
        response = client.get(
            "/api/v1/supervisors/performance/compare",
            params={"supervisors": ["research", "analytics"]}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["supervisors_compared"]) == 2
        assert "research" in data["performance_metrics"]
    
    def test_run_coordination_experiment(self, client, mock_supervisor_service):
        """Test running coordination strategy experiments"""
        mock_response = Mock(
            experiment_id="exp-123",
            strategies_tested=["direct", "collaborative"],
            results={
                "direct": {"quality": 0.85, "speed": 0.9},
                "collaborative": {"quality": 0.92, "speed": 0.7}
            },
            best_strategy="collaborative",
            statistical_significance=0.95,
            recommendations=["Use collaborative for quality-critical tasks"],
            detailed_analysis={}
        )
        
        mock_supervisor_service.run_experiment = AsyncMock(
            return_value=mock_response
        )
        
        request_data = {
            "query": "Test query for experimentation",
            "strategies_to_test": ["direct", "collaborative"],
            "metrics_to_track": ["quality", "speed"],
            "repetitions": 3
        }
        
        response = client.post(
            "/api/v1/supervisors/experiment",
            json=request_data
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["best_strategy"] == "collaborative"
        assert data["statistical_significance"] == 0.95


class TestWebSocketEndpoints:
    """Test WebSocket endpoints for real-time updates"""
    
    @pytest.mark.asyncio
    async def test_supervisor_websocket_connection(self, app):
        """Test WebSocket connection for supervisor updates"""
        from fastapi.testclient import TestClient
        
        with TestClient(app) as client:
            with client.websocket_connect(
                "/api/v1/supervisors/research/ws?client_id=test-client"
            ) as websocket:
                # Receive initial connection message
                data = websocket.receive_json()
                assert data["event_type"] == "connection_established"
                
                # Send ping
                websocket.send_json({"type": "ping"})
                response = websocket.receive_json()
                assert response["type"] == "pong"
    
    @pytest.mark.asyncio
    async def test_coordination_progress_websocket(self, app):
        """Test WebSocket for coordination progress updates"""
        from fastapi.testclient import TestClient
        
        with TestClient(app) as client:
            with client.websocket_connect(
                "/api/v1/supervisors/coordination/ws?coordination_id=test-coord"
            ) as websocket:
                # Receive initial connection message
                data = websocket.receive_json()
                assert data["event_type"] == "connection_established"
                assert data["coordination_id"] == "test-coord"


class TestErrorHandling:
    """Test error handling in supervisor API"""
    
    def test_invalid_supervisor_type_404(self, client, mock_supervisor_service):
        """Test 404 for invalid supervisor type"""
        mock_supervisor_service.get_supervisor_info = AsyncMock(
            side_effect=ValueError("Supervisor type 'invalid' not found")
        )
        
        response = client.get("/api/v1/supervisors/invalid")
        
        assert response.status_code == 422  # FastAPI validation error
    
    def test_internal_server_error_500(self, client, mock_supervisor_service):
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
    
    def test_validation_error_422(self, client):
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
    
    def test_complete_supervisor_workflow(self, client, mock_supervisor_service):
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
                last_execution=datetime.utcnow(),
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