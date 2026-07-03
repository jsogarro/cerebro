"""
Tests for Multi-Domain Merge Strategy (concat vs llm)

Tests the MULTI_DOMAIN_MERGE_STRATEGY config option that allows choosing
between labeled concatenation (default) and LLM synthesis for merging
per-domain results from multi-domain queries.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.agents.models import AgentResult
from src.ai_brain.router.query_decomposer import QueryDecomposition
from src.api.services.direct_execution_service import DirectExecutionService
from src.models.research_project import (
    ResearchDepth,
    ResearchProject,
    ResearchQuery,
    ResearchScope,
)


# Minimal routing decision stand-in
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
    query_id: str = "test-merge-123"
    collaboration_mode: str = "parallel"
    agent_allocation: _FakeAgentAllocation = field(default_factory=_FakeAgentAllocation)
    estimated_cost: float = 0.02
    estimated_latency_ms: int = 150000
    estimated_quality: float = 0.88
    confidence_score: float = 0.86
    complexity_analysis: _FakeComplexityAnalysis = field(
        default_factory=_FakeComplexityAnalysis
    )
    context: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def service_with_concat_strategy():
    """DirectExecutionService with concat merge strategy (default)."""
    with patch(
        "src.api.services.direct_execution_service.get_settings"
    ) as mock_settings:
        mock_settings.return_value.MULTI_DOMAIN_MERGE_STRATEGY = "concat"
        mock_settings.return_value.MULTI_DOMAIN_MERGE_PER_DOMAIN_CHAR_LIMIT = 4000

        masr_router = AsyncMock()
        bridge = AsyncMock()

        decomposition = QueryDecomposition(
            detected_domains=["research", "analytics"],
            domain_relevance={"research": 0.8, "analytics": 0.7},
            domain_subqueries={
                "research": "Research AI trends",
                "analytics": "Analyze AI data",
            },
            cross_domain_dependencies=[],
            coordination_complexity=2,
        )

        complexity_analysis = _FakeComplexityAnalysis(decomposition=decomposition)
        main_decision = _FakeRoutingDecision(complexity_analysis=complexity_analysis)
        masr_router.route.return_value = main_decision

        # Per-domain routing
        async def route_side_effect(query: str, context: dict[str, Any] | None = None):
            if context and "domain" in context:
                domain = context["domain"]
                domain_complexity = _FakeComplexityAnalysis(
                    domains=[domain], decomposition=None
                )
                return _FakeRoutingDecision(
                    complexity_analysis=domain_complexity,
                    agent_allocation=_FakeAgentAllocation(supervisor_type=domain),
                )
            return main_decision

        masr_router.route.side_effect = route_side_effect

        # Supervisor bridge returns per-domain results
        async def execute_side_effect(routing_decision, task, supervisor_registry):
            domain = routing_decision.agent_allocation.supervisor_type
            return Mock(
                status=Mock(value="completed"),
                agent_result=Mock(
                    output={"domain_result": f"Results from {domain} domain"}
                ),
                quality_score=0.85,
                consensus_score=0.90,
                workers_used=2,
                execution_time_seconds=5.5,
                errors=[],
            )

        bridge.execute_routing_decision.side_effect = execute_side_effect

        service = DirectExecutionService(
            masr_router=masr_router,
            supervisor_bridge=bridge,
            gemini_service=None,
        )
        yield service


@pytest.fixture
def service_with_llm_strategy():
    """DirectExecutionService with llm merge strategy."""
    with patch(
        "src.api.services.direct_execution_service.get_settings"
    ) as mock_settings:
        mock_settings.return_value.MULTI_DOMAIN_MERGE_STRATEGY = "llm"
        mock_settings.return_value.MULTI_DOMAIN_MERGE_PER_DOMAIN_CHAR_LIMIT = 4000

        masr_router = AsyncMock()
        bridge = AsyncMock()
        gemini_service = Mock()

        decomposition = QueryDecomposition(
            detected_domains=["research", "analytics"],
            domain_relevance={"research": 0.8, "analytics": 0.7},
            domain_subqueries={
                "research": "Research AI trends",
                "analytics": "Analyze AI data",
            },
            cross_domain_dependencies=[],
            coordination_complexity=2,
        )

        complexity_analysis = _FakeComplexityAnalysis(decomposition=decomposition)
        main_decision = _FakeRoutingDecision(complexity_analysis=complexity_analysis)
        masr_router.route.return_value = main_decision

        async def route_side_effect(query: str, context: dict[str, Any] | None = None):
            if context and "domain" in context:
                domain = context["domain"]
                domain_complexity = _FakeComplexityAnalysis(
                    domains=[domain], decomposition=None
                )
                return _FakeRoutingDecision(
                    complexity_analysis=domain_complexity,
                    agent_allocation=_FakeAgentAllocation(supervisor_type=domain),
                )
            return main_decision

        masr_router.route.side_effect = route_side_effect

        async def execute_side_effect(routing_decision, task, supervisor_registry):
            domain = routing_decision.agent_allocation.supervisor_type
            return Mock(
                status=Mock(value="completed"),
                agent_result=Mock(
                    output={"domain_result": f"Results from {domain} domain"}
                ),
                quality_score=0.85,
                consensus_score=0.90,
                workers_used=2,
                execution_time_seconds=5.5,
                errors=[],
            )

        bridge.execute_routing_decision.side_effect = execute_side_effect

        service = DirectExecutionService(
            masr_router=masr_router,
            supervisor_bridge=bridge,
            gemini_service=gemini_service,
        )
        yield service


@pytest.mark.asyncio
async def test_concat_strategy_default_behavior(service_with_concat_strategy):
    """Test that concat strategy (default) produces labeled concatenation."""
    project = ResearchProject(
        title="AI Trends Analysis",
        user_id="test-user",
        query=ResearchQuery(
            text="Analyze AI trends across research and analytics",
            domains=["research", "analytics"],
        ),
        scope=ResearchScope(depth=ResearchDepth.COMPREHENSIVE),
    )

    execution_id = await service_with_concat_strategy.start_research_execution(project)

    # Wait for async execution
    await asyncio.sleep(0.15)

    # Get execution status
    status = service_with_concat_strategy.active_executions.get(execution_id)
    assert status is not None
    assert status.status == "completed"
    assert status.final_output is not None

    # With concat strategy, output should be per-domain labeled concatenation
    output = status.final_output
    assert "research" in output
    assert "analytics" in output
    assert "_multi_domain_metadata" in output

    metadata = output["_multi_domain_metadata"]
    assert metadata["merge_strategy"] == "concat"
    assert set(metadata["succeeded_domains"]) == {"research", "analytics"}
    assert metadata["failed_domains"] == []


@pytest.mark.asyncio
async def test_llm_strategy_synthesis(service_with_llm_strategy):
    """Test that llm strategy invokes synthesis and returns composed output."""
    # Mock the synthesis agent
    with patch("src.agents.synthesis_agent.SynthesisAgent") as mock_agent_class:
        mock_agent_instance = AsyncMock()
        mock_agent_class.return_value = mock_agent_instance

        # Mock synthesis result
        synthesis_result = AgentResult(
            task_id="synthesis_test",
            status="success",
            output={
                "comprehensive_narrative": "This is the synthesized output combining research and analytics insights.",
                "integrated_findings": ["Finding 1", "Finding 2"],
                "meta_insights": ["Insight 1"],
            },
            confidence=0.92,
            execution_time=2.0,
        )
        mock_agent_instance.execute.return_value = synthesis_result

        project = ResearchProject(
            title="AI Trends Analysis",
            user_id="test-user",
            query=ResearchQuery(
                text="Analyze AI trends across research and analytics",
                domains=["research", "analytics"],
            ),
            scope=ResearchScope(depth=ResearchDepth.COMPREHENSIVE),
        )

        execution_id = await service_with_llm_strategy.start_research_execution(project)

        # Wait for async execution
        await asyncio.sleep(0.15)

        status = service_with_llm_strategy.active_executions.get(execution_id)
        assert status is not None
        assert status.status == "completed"
        assert status.final_output is not None

        # With llm strategy, output should have synthesis + per_domain
        output = status.final_output
        assert "synthesis" in output
        assert "per_domain" in output
        assert (
            output["synthesis"]
            == "This is the synthesized output combining research and analytics insights."
        )

        # Per-domain outputs preserved
        assert "research" in output["per_domain"]
        assert "analytics" in output["per_domain"]

        # Metadata records llm strategy
        metadata = output["_multi_domain_metadata"]
        assert metadata["merge_strategy"] == "llm"
        assert "synthesis_confidence" in metadata
        assert metadata["synthesis_confidence"] == 0.92


@pytest.mark.asyncio
async def test_llm_strategy_fallback_on_synthesis_failure(service_with_llm_strategy):
    """Test that llm strategy falls back to concat when synthesis fails."""
    with patch("src.agents.synthesis_agent.SynthesisAgent") as mock_agent_class:
        mock_agent_instance = AsyncMock()
        mock_agent_class.return_value = mock_agent_instance

        # Mock synthesis failure
        mock_agent_instance.execute.side_effect = RuntimeError("Synthesis failed")

        project = ResearchProject(
            title="AI Trends Analysis",
            user_id="test-user",
            query=ResearchQuery(
                text="Analyze AI trends across research and analytics",
                domains=["research", "analytics"],
            ),
            scope=ResearchScope(depth=ResearchDepth.COMPREHENSIVE),
        )

        execution_id = await service_with_llm_strategy.start_research_execution(project)

        # Wait for async execution
        await asyncio.sleep(0.15)

        status = service_with_llm_strategy.active_executions.get(execution_id)
        assert status is not None
        assert status.status == "completed"
        assert status.final_output is not None

        # Should fall back to concat
        output = status.final_output
        assert "research" in output
        assert "analytics" in output

        metadata = output["_multi_domain_metadata"]
        assert metadata["merge_strategy"] == "concat_fallback"


@pytest.mark.asyncio
async def test_partial_domain_failure_with_llm_synthesis(service_with_llm_strategy):
    """Test that llm synthesis works over surviving domains when some fail."""

    # Modify bridge to fail analytics domain
    async def execute_side_effect_with_failure(
        routing_decision, task, supervisor_registry
    ):
        domain = routing_decision.agent_allocation.supervisor_type
        if domain == "analytics":
            return Mock(
                status=Mock(value="failed"),
                agent_result=None,
                errors=["Analytics service unavailable"],
            )
        return Mock(
            status=Mock(value="completed"),
            agent_result=Mock(
                output={"domain_result": f"Results from {domain} domain"}
            ),
            quality_score=0.85,
            consensus_score=0.90,
            workers_used=2,
            execution_time_seconds=5.5,
            errors=[],
        )

    service_with_llm_strategy.supervisor_bridge.execute_routing_decision.side_effect = (
        execute_side_effect_with_failure
    )

    with patch("src.agents.synthesis_agent.SynthesisAgent") as mock_agent_class:
        mock_agent_instance = AsyncMock()
        mock_agent_class.return_value = mock_agent_instance

        synthesis_result = AgentResult(
            task_id="synthesis_test",
            status="success",
            output={
                "comprehensive_narrative": "Synthesized from research domain only.",
                "integrated_findings": ["Finding 1"],
            },
            confidence=0.75,
            execution_time=1.5,
        )
        mock_agent_instance.execute.return_value = synthesis_result

        project = ResearchProject(
            title="AI Trends Analysis",
            user_id="test-user",
            query=ResearchQuery(
                text="Analyze AI trends across research and analytics",
                domains=["research", "analytics"],
            ),
            scope=ResearchScope(depth=ResearchDepth.COMPREHENSIVE),
        )

        execution_id = await service_with_llm_strategy.start_research_execution(project)
        await asyncio.sleep(0.15)
        status = service_with_llm_strategy.active_executions.get(execution_id)
        assert status is not None

        assert status.status == "completed"
        assert len(status.warnings) > 0  # Should warn about partial failure

        output = status.final_output
        assert "synthesis" in output
        assert output["synthesis"] == "Synthesized from research domain only."

        metadata = output["_multi_domain_metadata"]
        assert metadata["merge_strategy"] == "llm"
        assert len(metadata["succeeded_domains"]) == 1
        assert len(metadata["failed_domains"]) == 1


@pytest.mark.asyncio
async def test_single_domain_path_unchanged():
    """Test that single-domain queries bypass multi-domain merge logic entirely."""
    with patch(
        "src.api.services.direct_execution_service.get_settings"
    ) as mock_settings:
        mock_settings.return_value.MULTI_DOMAIN_MERGE_STRATEGY = "llm"

        masr_router = AsyncMock()
        bridge = AsyncMock()

        # Single-domain decision (no decomposition)
        complexity_analysis = _FakeComplexityAnalysis(
            domains=["research"],
            decomposition=None,  # Single-domain
        )
        main_decision = _FakeRoutingDecision(complexity_analysis=complexity_analysis)
        masr_router.route.return_value = main_decision

        bridge.execute_routing_decision.return_value = Mock(
            status=Mock(value="completed"),
            agent_result=Mock(output={"result": "Single domain result"}),
            quality_score=0.9,
        )

        service = DirectExecutionService(
            masr_router=masr_router,
            supervisor_bridge=bridge,
            gemini_service=Mock(),
        )

        project = ResearchProject(
            title="Research Only",
            user_id="test-user",
            query=ResearchQuery(text="Pure research query", domains=["research"]),
            scope=ResearchScope(depth=ResearchDepth.COMPREHENSIVE),
        )

        execution_id = await service.start_research_execution(project)

        # Wait for async execution
        await asyncio.sleep(0.15)

        status = service.active_executions.get(execution_id)
        assert status is not None
        assert status.status == "completed"
        # Single-domain path doesn't call _merge_domain_results at all
        # Output comes directly from supervisor
        assert status.final_output == {"result": "Single domain result"}
