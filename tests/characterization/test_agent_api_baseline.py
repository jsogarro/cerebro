"""Observed contracts of direct legacy agent API route functions."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.api.routes import agent_api
from src.models.agent_api_models import (
    AgentCapability,
    AgentExecutionRequest,
    AgentExecutionResponse,
    AgentInfo,
    AgentType,
    AgentValidationRequest,
    ChainOfAgentsResponse,
    MixtureOfAgentsResponse,
)


class _AgentService:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def execute_single_agent(self, agent_type, request):
        self.requests.append((agent_type, request))
        return _execution_response(agent_type)

    async def execute_chain_of_agents(self, request):
        self.requests.append(request)
        return _chain_response()

    async def execute_mixture_of_agents(self, request):
        self.requests.append(request)
        return _mixture_response()

    async def get_agent_list(self):
        return [_agent_info()]

    async def get_service_stats(self):
        return {
            "system_health": "degraded",
            "agent_metrics": {"literature-review": {"total_executions": 3}},
        }

    async def get_agent_health(self, agent_type):
        return SimpleNamespace(
            agent_type=agent_type,
            status="unavailable",
            success_rate_24h=0.0,
            average_response_time_ms=0.0,
            error_rate=1.0,
            resource_utilization=0.0,
            queue_length=0,
            last_error="fixture",
            current_issues=["fixture"],
            last_check=datetime(2026, 7, 26, tzinfo=UTC),
        )


def _agent_info() -> AgentInfo:
    return AgentInfo(
        agent_type=AgentType.LITERATURE_REVIEW,
        name="Fixture",
        description="Fixture agent",
        capabilities=[AgentCapability.SOURCE_EVALUATION],
        average_execution_time_ms=1,
        reliability_score=0.8,
        quality_score=0.8,
        complexity_handling=["simple"],
        optimal_domains=["research"],
        endpoints=["/fixture"],
    )


def _execution_response(agent_type: AgentType) -> AgentExecutionResponse:
    return AgentExecutionResponse(
        execution_id="agent-execution-1",
        agent_type=agent_type,
        status="completed",
        output={"legacy": True},
        confidence=0.8,
        quality_score=0.8,
        execution_time_seconds=1.0,
        started_at=datetime(2026, 7, 26, tzinfo=UTC),
    )


def _chain_response() -> ChainOfAgentsResponse:
    return ChainOfAgentsResponse(
        execution_id="chain-1",
        status="completed",
        agent_chain=[AgentType.LITERATURE_REVIEW, AgentType.SYNTHESIS],
        intermediate_results=[{"legacy": "step"}],
        final_result={"legacy": "final"},
        overall_confidence=0.8,
        total_execution_time_seconds=1.0,
        agent_execution_times=[0.5, 0.5],
        chain_quality_score=0.8,
        quality_improvement=0.0,
        started_at=datetime(2026, 7, 26, tzinfo=UTC),
    )


def _mixture_response() -> MixtureOfAgentsResponse:
    return MixtureOfAgentsResponse(
        execution_id="mixture-1",
        status="completed",
        agent_types=[AgentType.LITERATURE_REVIEW, AgentType.METHODOLOGY],
        agent_results={},
        aggregated_result={"legacy": "aggregate"},
        consensus_score=0.8,
        aggregation_strategy="consensus",
        agent_weights={},
        consensus_achieved=True,
        total_execution_time_seconds=1.0,
        parallel_efficiency=1.0,
        mixture_quality_score=0.8,
        inter_agent_agreement=0.8,
        started_at=datetime(2026, 7, 26, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_execute_and_convenience_routes_forward_legacy_requests():
    service = _AgentService()

    direct = await agent_api.execute_agent(
        AgentType.LITERATURE_REVIEW,
        AgentExecutionRequest(query="Characterize direct execution"),
        background_tasks=None,
        execution_service=service,
    )
    literature = await agent_api.literature_search(
        query="Characterize literature convenience",
        max_sources=30,
        domains=["research"],
        execution_service=service,
    )
    citations = await agent_api.format_citations(
        ["source"],
        "MLA",
        execution_service=service,
    )

    assert direct.status == "completed"
    assert literature.agent_type == AgentType.LITERATURE_REVIEW
    assert citations.agent_type == AgentType.CITATION
    assert service.requests[1][0] == AgentType.LITERATURE_REVIEW
    assert service.requests[1][1].parameters == {
        "max_sources": 30,
        "domains": ["research"],
    }
    assert service.requests[2][0] == AgentType.CITATION
    assert service.requests[2][1].parameters["citation_style"] == "MLA"


@pytest.mark.asyncio
async def test_synthesis_combine_pins_exact_agent_request():
    service = _AgentService()
    findings = [
        {"claim": "Alpha", "evidence_ids": ["evidence-1"]},
        {"claim": "Beta", "evidence_ids": ["evidence-2"]},
    ]

    response = await agent_api.synthesize_findings(
        findings=findings,
        synthesis_focus="thematic",
        execution_service=service,
    )

    assert response.agent_type is AgentType.SYNTHESIS
    agent_type, request = service.requests[0]
    assert agent_type is AgentType.SYNTHESIS
    assert request.query == "Synthesize findings with thematic focus"
    assert request.parameters == {
        "findings": findings,
        "synthesis_focus": "thematic",
    }
    assert request.user_id is None
    assert request.session_id is None


@pytest.mark.asyncio
async def test_chain_mixture_and_workflows_preserve_distinct_shapes():
    service = _AgentService()

    workflow = await agent_api.literature_analysis_workflow(
        query="Characterize literature workflow",
        domains=["research"],
        execution_service=service,
    )
    mixture = await agent_api.comprehensive_research_workflow(
        query="Characterize comprehensive workflow",
        analysis_depth="exhaustive",
        execution_service=service,
    )

    assert workflow.final_result == {"legacy": "final"}
    assert [item.value for item in service.requests[0].agent_chain] == [
        "literature-review",
        "citation",
        "synthesis",
    ]
    assert mixture.aggregated_result == {"legacy": "aggregate"}
    assert [item.value for item in service.requests[1].agent_types] == [
        "literature-review",
        "methodology",
        "comparative-analysis",
        "synthesis",
        "citation",
    ]


@pytest.mark.asyncio
async def test_agent_validation_is_local_heuristic_not_agent_execution():
    response = await agent_api.validate_agent_input(
        AgentType.LITERATURE_REVIEW,
        AgentValidationRequest(
            agent_type=AgentType.LITERATURE_REVIEW,
            query="short query",
            parameters={"max_sources": 25, "unsupported": object()},
        ),
    )

    assert response.valid is False
    assert response.estimated_cost == 0.01
    assert response.parameter_validation == {"max_sources": True, "unsupported": False}
    assert response.validation_issues == ["Query or parameters need improvement"]


@pytest.mark.asyncio
async def test_list_info_and_health_preserve_legacy_discovery_and_status_shapes():
    service = _AgentService()

    listed = await agent_api.list_agents(execution_service=service)
    info = await agent_api.get_agent_info(
        AgentType.LITERATURE_REVIEW,
        execution_service=service,
    )
    health = await agent_api.get_agent_health(
        AgentType.LITERATURE_REVIEW,
        execution_service=service,
    )

    assert listed.system_health == "degraded"
    assert listed.total_system_executions == 3
    assert listed.supported_execution_modes == list(agent_api.ExecutionMode)
    assert info.endpoints == ["/fixture"]
    assert health.status == "unavailable"
    assert health.current_issues == ["fixture"]
