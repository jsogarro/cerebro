"""
Tests for Direct Execution Service

Tests the direct MASR routing and supervisor execution service that replaces
the Temporal workflow system.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest

from src.agents.models import AgentResult
from src.ai_brain.integration.masr_supervisor_bridge import SupervisorExecutionResult
from src.ai_brain.router.masr import AgentAllocation, CollaborationMode, RoutingDecision
from src.api.services.direct_execution_service import (
    DirectExecutionService,
    ExecutionStatus,
    get_direct_execution_service,
)
from src.models.research_project import (
    ResearchDepth,
    ResearchProject,
    ResearchQuery,
    ResearchScope,
)


class TestDirectExecutionService:
    """Test suite for DirectExecutionService."""

    @pytest.fixture
    def mock_masr_router(self):
        """Mock MASR router."""
        router = AsyncMock()
        
        # Mock routing decision
        mock_decision = Mock(spec=RoutingDecision)
        mock_decision.query_id = "test-query-123"
        mock_decision.timestamp = datetime.now()
        mock_decision.collaboration_mode = CollaborationMode.HIERARCHICAL
        mock_decision.agent_allocation = Mock(spec=AgentAllocation)
        mock_decision.agent_allocation.supervisor_type = "research"
        mock_decision.agent_allocation.worker_count = 3
        mock_decision.agent_allocation.worker_types = ["literature", "analysis", "synthesis"]
        mock_decision.estimated_cost = 0.015
        mock_decision.estimated_latency_ms = 120000
        mock_decision.estimated_quality = 0.87
        mock_decision.confidence_score = 0.85
        
        router.route.return_value = mock_decision
        return router
    
    @pytest.fixture
    def mock_supervisor_bridge(self):
        """Mock supervisor bridge."""
        bridge = AsyncMock()
        
        # Mock successful execution result
        mock_result = Mock(spec=SupervisorExecutionResult)
        mock_result.execution_id = "supervisor-exec-123"
        mock_result.supervisor_type = "research"
        mock_result.domain = "research"
        mock_result.status.value = "completed"
        mock_result.quality_score = 0.89
        mock_result.consensus_score = 0.92
        mock_result.execution_time_seconds = 95.0
        mock_result.workers_used = 3
        mock_result.refinement_rounds = 2
        mock_result.errors = []
        
        # Mock agent result
        mock_agent_result = Mock(spec=AgentResult)
        mock_agent_result.output = {
            "research_findings": ["Finding 1", "Finding 2"],
            "literature_sources": ["Source 1", "Source 2"],
            "synthesis": "Comprehensive analysis completed",
            "quality_metrics": {"confidence": 0.89}
        }
        mock_result.agent_result = mock_agent_result
        
        bridge.execute_routing_decision.return_value = mock_result
        return bridge
    
    @pytest.fixture
    def mock_supervisor_factory(self):
        """Mock supervisor factory."""
        return Mock()
    
    @pytest.fixture
    def mock_event_publisher(self):
        """Mock event publisher."""
        publisher = AsyncMock()
        publisher.publish_project_event.return_value = None
        return publisher
    
    @pytest.fixture
    def execution_service(self, mock_masr_router, mock_supervisor_bridge, mock_supervisor_factory, mock_event_publisher):
        """Create DirectExecutionService with mocked dependencies."""
        return DirectExecutionService(
            masr_router=mock_masr_router,
            supervisor_bridge=mock_supervisor_bridge,
            supervisor_factory=mock_supervisor_factory,
            event_publisher=mock_event_publisher
        )
    
    @pytest.fixture
    def sample_project(self):
        """Create sample research project."""
        return ResearchProject(
            title="Test Research Project",
            query=ResearchQuery(
                question="What are the impacts of AI on society?",
                domains=["ai", "sociology"],
                depth_level=ResearchDepth.COMPREHENSIVE
            ),
            user_id="test-user-123",
            scope=ResearchScope(max_sources=25)
        )

    @pytest.mark.asyncio
    async def test_start_research_execution_success(self, execution_service, sample_project):
        """Test successful research execution start."""
        
        execution_id = await execution_service.start_research_execution(sample_project)
        
        assert execution_id is not None
        assert execution_id in execution_service.active_executions
        
        execution_status = execution_service.active_executions[execution_id]
        assert execution_status.project_id == str(sample_project.id)
        assert execution_status.status == "pending"
        assert execution_status.current_phase == "initialization"

    @pytest.mark.asyncio
    async def test_execution_workflow_complete_flow(self, execution_service, sample_project, mock_masr_router, mock_supervisor_bridge):
        """Test complete execution workflow from start to finish."""
        
        # Start execution
        execution_id = await execution_service.start_research_execution(sample_project)
        
        # Wait for async execution to complete
        await asyncio.sleep(0.1)  # Give time for background task
        
        # Verify MASR router was called
        mock_masr_router.route.assert_called_once()
        call_args = mock_masr_router.route.call_args
        assert sample_project.query.question in str(call_args)
        
        # Verify supervisor bridge was called
        mock_supervisor_bridge.execute_routing_decision.assert_called_once()
        
        # Check execution status
        execution_status = execution_service.active_executions[execution_id]
        
        # The execution should be completed (or completing)
        assert execution_status.status in ["running", "completed"]
        assert execution_status.routing_decision is not None
        assert execution_status.supervisor_type == "research"

    @pytest.mark.asyncio
    async def test_get_execution_status(self, execution_service, sample_project):
        """Test getting execution status."""
        
        execution_id = await execution_service.start_research_execution(sample_project)
        
        status = await execution_service.get_execution_status(execution_id)
        assert status is not None
        assert status.execution_id == execution_id
        assert status.project_id == str(sample_project.id)

    @pytest.mark.asyncio
    async def test_get_execution_status_nonexistent(self, execution_service):
        """Test getting status for non-existent execution."""
        
        status = await execution_service.get_execution_status("nonexistent-id")
        assert status is None

    @pytest.mark.asyncio
    async def test_cancel_execution(self, execution_service, sample_project):
        """Test canceling an active execution."""
        
        execution_id = await execution_service.start_research_execution(sample_project)
        
        # Cancel execution
        success = await execution_service.cancel_execution(execution_id)
        assert success is True
        
        # Verify status is updated
        execution_status = execution_service.active_executions[execution_id]
        assert execution_status.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_execution_nonexistent(self, execution_service):
        """Test canceling non-existent execution."""
        
        success = await execution_service.cancel_execution("nonexistent-id")
        assert success is False

    @pytest.mark.asyncio
    async def test_max_concurrent_executions(self, execution_service, sample_project):
        """Test maximum concurrent executions limit."""
        
        # Set low limit for testing
        execution_service.max_concurrent_executions = 2
        
        # Start maximum executions
        await execution_service.start_research_execution(sample_project)
        await execution_service.start_research_execution(sample_project)
        
        # Third execution should fail
        with pytest.raises(RuntimeError, match="Maximum concurrent executions"):
            await execution_service.start_research_execution(sample_project)
        
        assert len(execution_service.active_executions) == 2

    @pytest.mark.asyncio 
    async def test_execution_error_handling(self, execution_service, sample_project):
        """Test error handling in execution workflow."""
        
        # Mock MASR router to raise an exception
        execution_service.masr_router.route.side_effect = Exception("MASR routing failed")
        
        execution_id = await execution_service.start_research_execution(sample_project)
        
        # Wait for execution to complete
        await asyncio.sleep(0.2)
        
        execution_status = execution_service.active_executions[execution_id]
        
        # Should be failed with error
        assert execution_status.status == "failed"
        assert len(execution_status.errors) > 0
        assert "MASR routing failed" in execution_status.errors[0]

    @pytest.mark.asyncio
    async def test_get_execution_results(self, execution_service, sample_project):
        """Test getting execution results."""
        
        execution_id = await execution_service.start_research_execution(sample_project)
        
        # Wait for execution
        await asyncio.sleep(0.1)
        
        results = await execution_service.get_execution_results(execution_id)
        assert results is not None

    @pytest.mark.asyncio
    async def test_list_active_executions(self, execution_service, sample_project):
        """Test listing active executions."""
        
        # Start multiple executions
        execution_id_1 = await execution_service.start_research_execution(sample_project)
        execution_id_2 = await execution_service.start_research_execution(sample_project)
        
        active_executions = await execution_service.list_active_executions()
        
        assert len(active_executions) >= 2
        execution_ids = [ex.execution_id for ex in active_executions]
        assert execution_id_1 in execution_ids
        assert execution_id_2 in execution_ids

    @pytest.mark.asyncio
    async def test_cleanup_completed_executions(self, execution_service, sample_project):
        """Test cleanup of old completed executions."""
        
        # Start and complete an execution
        execution_id = await execution_service.start_research_execution(sample_project)
        
        # Manually mark as completed and set old timestamp
        execution_status = execution_service.active_executions[execution_id]
        execution_status.status = "completed"
        execution_status.completed_at = datetime.now()
        
        # Test cleanup (with 0 hour limit to clean immediately)
        cleaned_count = await execution_service.cleanup_completed_executions(max_age_hours=0)
        
        assert cleaned_count == 1
        assert execution_id not in execution_service.active_executions

    @pytest.mark.asyncio
    async def test_service_stats(self, execution_service, sample_project):
        """Test service statistics."""
        
        stats = await execution_service.get_service_stats()
        
        assert "execution_stats" in stats
        assert "active_executions" in stats
        assert "component_health" in stats
        
        assert stats["execution_stats"]["total_executions"] >= 0
        assert isinstance(stats["active_executions"], int)

    @pytest.mark.asyncio
    async def test_health_check(self, execution_service):
        """Test service health check."""
        
        health = await execution_service.health_check()
        
        assert "status" in health
        assert "components" in health
        assert "service_stats" in health
        
        assert health["status"] in ["healthy", "degraded", "unknown"]
        assert "masr_router" in health["components"]
        assert "supervisor_bridge" in health["components"]
        assert "supervisor_factory" in health["components"]

    def test_get_direct_execution_service_singleton(self):
        """Test that get_direct_execution_service returns singleton."""
        
        service1 = get_direct_execution_service()
        service2 = get_direct_execution_service()
        
        assert service1 is service2
        assert isinstance(service1, DirectExecutionService)


class TestExecutionStatus:
    """Test suite for ExecutionStatus data class."""

    def test_execution_status_initialization(self):
        """Test ExecutionStatus initialization."""
        
        status = ExecutionStatus(
            execution_id="test-123",
            project_id="project-456",
            status="pending"
        )
        
        assert status.execution_id == "test-123"
        assert status.project_id == "project-456"
        assert status.status == "pending"
        assert status.progress_percentage == 0.0
        assert status.current_phase == "initialization"
        assert status.agent_results == {}
        assert status.quality_scores == {}
        assert status.errors == []
        assert status.warnings == []
        assert status.retry_count == 0
        assert isinstance(status.started_at, datetime)

    def test_execution_status_post_init(self):
        """Test ExecutionStatus post-initialization."""
        
        status = ExecutionStatus(
            execution_id="test-123",
            project_id="project-456", 
            status="pending",
            agent_results=None,  # Should be initialized to {}
            quality_scores=None,  # Should be initialized to {}
        )
        
        assert status.agent_results == {}
        assert status.quality_scores == {}
        assert status.errors == []
        assert status.warnings == []


@pytest.mark.integration
class TestDirectExecutionIntegration:
    """Integration tests for direct execution service."""

    @pytest.mark.asyncio
    async def test_full_integration_flow(self):
        """Test full integration flow with real components."""
        
        # This would test with real MASR and supervisor components
        # in an integration test environment
        
        execution_service = DirectExecutionService()
        
        ResearchProject(
            title="Integration Test Project",
            query=ResearchQuery(
                question="Test query for integration",
                domains=["test"],
                depth_level=ResearchDepth.COMPREHENSIVE
            ),
            user_id="integration-test-user"
        )
        
        # Health check should work
        health = await execution_service.health_check()
        assert health["status"] in ["healthy", "degraded", "unknown"]
        
        # Service stats should be accessible
        stats = await execution_service.get_service_stats()
        assert "execution_stats" in stats
        
        # Note: Full execution test would require mocking external services
        # or running in a full test environment with all dependencies


# Performance benchmarks
class TestDirectExecutionPerformance:
    """Performance tests for direct execution."""

    @pytest.mark.asyncio
    @pytest.mark.benchmark
    async def test_execution_startup_time(self, execution_service, sample_project):
        """Benchmark execution startup time."""
        
        start_time = datetime.now()
        
        execution_id = await execution_service.start_research_execution(sample_project)
        
        startup_time = (datetime.now() - start_time).total_seconds()
        
        # Should start quickly (under 100ms)
        assert startup_time < 0.1
        assert execution_id in execution_service.active_executions

    @pytest.mark.asyncio
    async def test_concurrent_execution_performance(self, execution_service, sample_project):
        """Test performance with multiple concurrent executions."""
        
        # Start multiple executions concurrently
        tasks = []
        for _i in range(5):
            task = asyncio.create_task(
                execution_service.start_research_execution(sample_project)
            )
            tasks.append(task)
        
        start_time = datetime.now()
        execution_ids = await asyncio.gather(*tasks)
        total_time = (datetime.now() - start_time).total_seconds()
        
        # Should handle concurrent starts efficiently
        assert len(execution_ids) == 5
        assert all(ex_id in execution_service.active_executions for ex_id in execution_ids)
        assert total_time < 1.0  # Under 1 second for 5 concurrent starts