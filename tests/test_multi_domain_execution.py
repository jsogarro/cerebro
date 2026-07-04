"""Tests for Multi-Domain Sub-Query Execution

Tests the new multi-domain decomposition feature where queries spanning multiple
domains are dispatched to domain supervisors concurrently and results are merged.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from src.ai_brain.router.query_decomposer import QueryDecomposition
from src.api.services.direct_execution_service import DirectExecutionService
from src.models.research_project import (
    ResearchDepth,
    ResearchProject,
    ResearchQuery,
    ResearchScope,
)


# Minimal routing decision stand-in with multi-domain decomposition
@dataclass
class _FakeComplexityAnalysis:
    level: str = "moderate"
    score: float = 0.6
    domains: list[str] = field(default_factory=lambda: ["research", "content"])
    decomposition: QueryDecomposition | None = None


@dataclass
class _FakeAgentAllocation:
    supervisor_type: str = "research"
    worker_count: int = 3
    worker_types: list[str] = field(default_factory=lambda: ["general"])


@dataclass
class _FakeRoutingDecision:
    query_id: str = "test-multi-domain-123"
    collaboration_mode: str = "parallel"
    agent_allocation: _FakeAgentAllocation = field(default_factory=_FakeAgentAllocation)
    estimated_cost: float = 0.02
    estimated_latency_ms: int = 150000
    estimated_quality: float = 0.88
    confidence_score: float = 0.86
    complexity_analysis: _FakeComplexityAnalysis = field(
        default_factory=_FakeComplexityAnalysis,
    )
    context: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def multi_domain_service():
    """DirectExecutionService with multi-domain routing configured."""
    masr_router = AsyncMock()

    # First call (main route) returns multi-domain decision
    decomposition = QueryDecomposition(
        detected_domains=["research", "content"],
        domain_relevance={"research": 0.7, "content": 0.6},
        domain_subqueries={
            "research": "Research and analyze: AI impact on content creation",
            "content": "Create content for: AI impact on content creation",
        },
        cross_domain_dependencies=[("research", "content")],
        coordination_complexity=2,
    )

    complexity_analysis = _FakeComplexityAnalysis(decomposition=decomposition)
    main_decision = _FakeRoutingDecision(complexity_analysis=complexity_analysis)
    masr_router.route.return_value = main_decision

    # Subsequent calls (per-domain routes) return single-domain decisions
    async def route_side_effect(query: str, context: dict[str, Any] | None = None):
        if context and "domain" in context:
            # Per-domain routing
            domain = context["domain"]
            domain_complexity = _FakeComplexityAnalysis(
                domains=[domain], decomposition=None,
            )
            domain_decision = _FakeRoutingDecision(
                complexity_analysis=domain_complexity,
                agent_allocation=_FakeAgentAllocation(supervisor_type=domain),
            )
            return domain_decision
        # Main route
        return main_decision

    masr_router.route.side_effect = route_side_effect

    bridge = AsyncMock()

    # Supervisor bridge returns different results per domain
    async def execute_side_effect(routing_decision, task, supervisor_registry):
        domain = routing_decision.agent_allocation.supervisor_type

        result = Mock()
        result.execution_id = f"supervisor-exec-{domain}"
        result.supervisor_type = domain
        result.domain = domain
        result.status.value = "completed"
        result.quality_score = 0.85
        result.consensus_score = 0.88
        result.execution_time_seconds = 45.0
        result.workers_used = 2
        result.errors = []

        agent_result = Mock()
        agent_result.output = {
            f"{domain}_findings": [f"{domain.capitalize()} finding 1"],
            f"{domain}_quality": 0.85,
        }
        result.agent_result = agent_result

        return result

    bridge.execute_routing_decision.side_effect = execute_side_effect

    publisher = AsyncMock()

    return DirectExecutionService(
        masr_router=masr_router,
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        event_publisher=publisher,
    )


@pytest.fixture
def multi_domain_project():
    """Sample multi-domain research project."""
    return ResearchProject(
        title="AI Impact on Content Creation",
        query=ResearchQuery(
            text="Analyze AI impact on content creation and write a report",
            domains=["ai", "research", "content"],
            depth_level=ResearchDepth.COMPREHENSIVE,
        ),
        user_id="test-user-456",
        scope=ResearchScope(max_sources=30),
    )


@pytest.fixture
def single_domain_service():
    """DirectExecutionService with single-domain routing for regression check."""
    masr_router = AsyncMock()

    # No decomposition - single domain
    complexity_analysis = _FakeComplexityAnalysis(
        domains=["research"], decomposition=None,
    )
    decision = _FakeRoutingDecision(complexity_analysis=complexity_analysis)
    masr_router.route.return_value = decision

    bridge = AsyncMock()
    result = Mock()
    result.execution_id = "supervisor-exec-single"
    result.supervisor_type = "research"
    result.domain = "research"
    result.status.value = "completed"
    result.quality_score = 0.89
    result.consensus_score = 0.92
    result.execution_time_seconds = 50.0
    result.workers_used = 2
    result.errors = []
    agent_result = Mock()
    agent_result.output = {"research_findings": ["Finding 1"], "quality": 0.89}
    result.agent_result = agent_result
    bridge.execute_routing_decision.return_value = result

    publisher = AsyncMock()

    return DirectExecutionService(
        masr_router=masr_router,
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        event_publisher=publisher,
    )


@pytest.fixture
def single_domain_project():
    """Sample single-domain research project for regression testing."""
    return ResearchProject(
        title="Simple Research",
        query=ResearchQuery(
            text="What are machine learning best practices?",
            domains=["research"],
            depth_level=ResearchDepth.SURVEY,
        ),
        user_id="test-user-789",
        scope=ResearchScope(max_sources=15),
    )


class TestMultiDomainExecution:
    """Test suite for multi-domain sub-query execution."""

    @pytest.mark.asyncio
    async def test_multi_domain_detection_and_execution(
        self, multi_domain_service, multi_domain_project,
    ):
        """Test that multi-domain queries trigger concurrent domain execution."""
        execution_id = await multi_domain_service.start_research_execution(
            multi_domain_project,
        )

        # Wait for async execution
        await asyncio.sleep(0.15)

        execution_status = multi_domain_service.active_executions.get(execution_id)
        assert execution_status is not None
        assert execution_status.status == "completed"

        # Verify multi-domain branch was taken
        assert execution_status.current_phase == "completed"

        # Verify both domain supervisors were called
        # Main route + 2 domain-specific routes = 3 calls
        assert multi_domain_service.masr_router.route.call_count >= 3

        # Verify bridge was called for each domain
        assert (
            multi_domain_service.supervisor_bridge.execute_routing_decision.call_count
            == 2
        )

        # Verify merged output contains both domains
        assert execution_status.final_output is not None
        output = execution_status.final_output

        assert "research" in output
        assert "content" in output
        assert "_multi_domain_metadata" in output

        metadata = output["_multi_domain_metadata"]
        assert "research" in metadata["succeeded_domains"]
        assert "content" in metadata["succeeded_domains"]
        assert len(metadata["failed_domains"]) == 0

    @pytest.mark.asyncio
    async def test_multi_domain_partial_failure(
        self, multi_domain_service, multi_domain_project,
    ):
        """Test partial failure handling - one domain succeeds, one fails."""

        # Reconfigure bridge to fail content domain
        async def execute_with_failure(routing_decision, task, supervisor_registry):
            domain = routing_decision.agent_allocation.supervisor_type

            result = Mock()
            result.execution_id = f"supervisor-exec-{domain}"
            result.supervisor_type = domain
            result.domain = domain

            if domain == "content":
                # Content domain fails
                result.status.value = "failed"
                result.errors = ["Content generation failed"]
                result.agent_result = None
            else:
                # Research domain succeeds
                result.status.value = "completed"
                result.quality_score = 0.85
                result.consensus_score = 0.88
                result.execution_time_seconds = 45.0
                result.workers_used = 2
                result.errors = []
                agent_result = Mock()
                agent_result.output = {
                    "research_findings": ["Research finding 1"],
                    "research_quality": 0.85,
                }
                result.agent_result = agent_result

            return result

        multi_domain_service.supervisor_bridge.execute_routing_decision.side_effect = (
            execute_with_failure
        )

        execution_id = await multi_domain_service.start_research_execution(
            multi_domain_project,
        )
        await asyncio.sleep(0.15)

        execution_status = multi_domain_service.active_executions.get(execution_id)
        assert execution_status is not None

        # Overall status should be completed (partial success)
        assert execution_status.status == "completed"

        # Verify warnings about partial failure
        assert len(execution_status.warnings) > 0
        assert "failed" in execution_status.warnings[0].lower()

        # Verify metadata reflects the failure
        output = execution_status.final_output
        metadata = output["_multi_domain_metadata"]
        assert "research" in metadata["succeeded_domains"]
        assert len(metadata["failed_domains"]) == 1
        assert metadata["failed_domains"][0]["domain"] == "content"

    @pytest.mark.asyncio
    async def test_multi_domain_all_fail(
        self, multi_domain_service, multi_domain_project,
    ):
        """Test complete failure when all domains fail."""

        # Reconfigure bridge to fail all domains
        async def execute_all_fail(routing_decision, task, supervisor_registry):
            result = Mock()
            result.execution_id = "supervisor-exec-fail"
            result.supervisor_type = routing_decision.agent_allocation.supervisor_type
            result.domain = routing_decision.agent_allocation.supervisor_type
            result.status.value = "failed"
            result.errors = ["Execution failed"]
            result.agent_result = None
            return result

        multi_domain_service.supervisor_bridge.execute_routing_decision.side_effect = (
            execute_all_fail
        )

        execution_id = await multi_domain_service.start_research_execution(
            multi_domain_project,
        )
        await asyncio.sleep(0.15)

        execution_status = multi_domain_service.active_executions.get(execution_id)
        assert execution_status is not None

        # Overall status should be failed
        assert execution_status.status == "failed"
        assert "All domains failed" in execution_status.errors

    @pytest.mark.asyncio
    async def test_single_domain_no_regression(
        self, single_domain_service, single_domain_project,
    ):
        """Test single-domain queries are completely unaffected (no regression)."""
        execution_id = await single_domain_service.start_research_execution(
            single_domain_project,
        )
        await asyncio.sleep(0.15)

        execution_status = single_domain_service.active_executions.get(execution_id)
        assert execution_status is not None
        assert execution_status.status == "completed"

        # Verify single-domain path was taken (no multi-domain metadata)
        output = execution_status.final_output
        assert output is not None
        assert "_multi_domain_metadata" not in output

        # Verify exactly ONE supervisor call (no concurrent dispatch)
        assert (
            single_domain_service.supervisor_bridge.execute_routing_decision.call_count
            == 1
        )

        # Verify quality scores are simple (not multi-domain merged)
        quality_scores = execution_status.quality_scores
        assert "overall" in quality_scores
        assert "consensus" in quality_scores

    @pytest.mark.asyncio
    async def test_domain_supervisor_mapping(
        self, multi_domain_service, multi_domain_project,
    ):
        """Test sub-queries are routed to correct domain supervisors."""
        await multi_domain_service.start_research_execution(multi_domain_project)
        await asyncio.sleep(0.15)

        # Extract domain routing calls
        bridge_calls = multi_domain_service.supervisor_bridge.execute_routing_decision.call_args_list

        assert len(bridge_calls) == 2

        # Verify supervisors match domains
        called_supervisors = [
            call.kwargs["routing_decision"].agent_allocation.supervisor_type
            for call in bridge_calls
        ]

        assert "research" in called_supervisors
        assert "content" in called_supervisors

    @pytest.mark.asyncio
    async def test_multi_domain_bounded_parallelism(
        self, multi_domain_service, multi_domain_project,
    ):
        """Test concurrency is bounded by semaphore."""
        # Verify max_domain_parallelism is set
        assert multi_domain_service.max_domain_parallelism == 4

        # For this test with 2 domains, both should run concurrently
        # (Actual concurrency control is verified by integration test timing)
        execution_id = await multi_domain_service.start_research_execution(
            multi_domain_project,
        )
        await asyncio.sleep(0.15)

        execution_status = multi_domain_service.active_executions.get(execution_id)
        assert execution_status.status == "completed"


class TestResultMerging:
    """Test suite for multi-domain result merging."""

    @pytest.mark.asyncio
    async def test_merge_all_success(self, multi_domain_service):
        """Test merging when all domains succeed."""
        domain_results = [
            {
                "domain": "research",
                "status": "completed",
                "output": {"findings": ["F1", "F2"]},
                "quality_score": 0.85,
                "consensus_score": 0.88,
                "workers_used": 2,
                "execution_time_seconds": 45.0,
            },
            {
                "domain": "content",
                "status": "completed",
                "output": {"draft": "Content draft text"},
                "quality_score": 0.82,
                "consensus_score": 0.84,
                "workers_used": 2,
                "execution_time_seconds": 50.0,
            },
        ]

        merged = await multi_domain_service._merge_domain_results(domain_results)

        assert merged["succeeded_domains"] == ["research", "content"]
        assert len(merged["failed_domains"]) == 0

        output = merged["output"]
        assert "research" in output
        assert "content" in output
        assert output["research"]["findings"] == ["F1", "F2"]
        assert output["content"]["draft"] == "Content draft text"

        quality_scores = merged["quality_scores"]
        assert "research_quality" in quality_scores
        assert "content_quality" in quality_scores

        assert merged["workers_used"] == 4

    @pytest.mark.asyncio
    async def test_merge_partial_success(self, multi_domain_service):
        """Test merging with partial failures."""
        domain_results = [
            {
                "domain": "research",
                "status": "completed",
                "output": {"findings": ["F1"]},
                "quality_score": 0.85,
                "consensus_score": 0.88,
                "workers_used": 2,
                "execution_time_seconds": 45.0,
            },
            {
                "domain": "content",
                "status": "failed",
                "errors": ["Content generation error"],
            },
        ]

        merged = await multi_domain_service._merge_domain_results(domain_results)

        assert merged["succeeded_domains"] == ["research"]
        assert len(merged["failed_domains"]) == 1
        assert merged["failed_domains"][0]["domain"] == "content"

        output = merged["output"]
        assert "research" in output
        assert "content" not in output

    @pytest.mark.asyncio
    async def test_merge_all_fail(self, multi_domain_service):
        """Test merging when all domains fail."""
        domain_results = [
            {"domain": "research", "status": "failed", "errors": ["Error 1"]},
            {"domain": "content", "status": "failed", "errors": ["Error 2"]},
        ]

        merged = await multi_domain_service._merge_domain_results(domain_results)

        assert len(merged["succeeded_domains"]) == 0
        assert len(merged["failed_domains"]) == 2

        output = merged["output"]
        assert "_multi_domain_metadata" in output
        assert output["_multi_domain_metadata"]["succeeded_domains"] == []
