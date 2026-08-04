"""Observed contracts of direct legacy agent API route functions."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

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
async def test_execute_and_convenience_routes_fail_closed_without_authority():
    """execute_agent and its convenience wrappers (literature_search,
    format_citations) all delegate to the same authority-gated dispatch;
    without a supplied authority reference, none can reach the service —
    they fail closed before forwarding anything."""
    service = _AgentService()

    with pytest.raises(HTTPException) as direct_exc:
        await agent_api.execute_agent(
            AgentType.LITERATURE_REVIEW,
            AgentExecutionRequest(query="Characterize direct execution"),
            background_tasks=None,
            execution_service=service,
        )
    with pytest.raises(HTTPException) as literature_exc:
        await agent_api.literature_search(
            query="Characterize literature convenience",
            max_sources=30,
            domains=["research"],
            authority_id=None,
            authority_version=None,
            execution_service=service,
        )
    with pytest.raises(HTTPException) as citations_exc:
        await agent_api.format_citations(
            ["source"],
            "MLA",
            authority_id=None,
            authority_version=None,
            execution_service=service,
        )

    for exc in (direct_exc, literature_exc, citations_exc):
        assert exc.value.detail == {"code": "EXECUTION_AUTHORITY_REQUIRED"}
    assert service.requests == []


_CONVENIENCE_CALLS_WITH_AUTHORITY = {
    "literature_search": lambda service: agent_api.literature_search(
        query="Characterize literature convenience",
        max_sources=30,
        domains=["research"],
        authority_id="authority-1",
        authority_version="1",
        execution_service=service,
    ),
    "format_citations": lambda service: agent_api.format_citations(
        sources=["Source 1", "Source 2"],
        style="APA",
        authority_id="authority-1",
        authority_version="1",
        execution_service=service,
    ),
    "synthesize_findings": lambda service: agent_api.synthesize_findings(
        findings=[
            {"claim": "Alpha", "evidence_ids": ["evidence-1"]},
            {"claim": "Beta", "evidence_ids": ["evidence-2"]},
        ],
        synthesis_focus="thematic",
        authority_id="authority-1",
        authority_version="1",
        execution_service=service,
    ),
    "literature_analysis_workflow": lambda service: (
        agent_api.literature_analysis_workflow(
            query="Characterize literature workflow",
            domains=["research"],
            authority_id="authority-1",
            authority_version="1",
            execution_service=service,
        )
    ),
    "comprehensive_research_workflow": lambda service: (
        agent_api.comprehensive_research_workflow(
            query="Characterize comprehensive workflow",
            analysis_depth="exhaustive",
            authority_id="authority-1",
            authority_version="1",
            execution_service=service,
        )
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", sorted(_CONVENIENCE_CALLS_WITH_AUTHORITY))
async def test_convenience_route_threads_a_supplied_authority_reference(endpoint):
    """Every convenience endpoint accepts authority_id/authority_version. A
    supplied reference changes the failure from EXECUTION_AUTHORITY_REQUIRED
    (no reference at all) to EXECUTION_AUTHORITY_UNAVAILABLE (reference
    present, but this fixture backend exposes no resolver) — proving the
    reference reached the same authority gate execute_agent uses, rather
    than being silently dropped on any of the five routes."""
    service = _AgentService()

    with pytest.raises(HTTPException) as exc_info:
        await _CONVENIENCE_CALLS_WITH_AUTHORITY[endpoint](service)

    assert exc_info.value.detail == {"code": "EXECUTION_AUTHORITY_UNAVAILABLE"}
    assert service.requests == []


def test_partial_authority_reference_is_rejected() -> None:
    """authority_id/authority_version must be supplied together, matching
    the CLI's own --authority-id/--authority-version pairing rule."""
    with pytest.raises(HTTPException) as exc_info:
        agent_api._optional_authority_reference("authority-1", None)

    assert exc_info.value.detail == {"code": "AUTHORITY_REFERENCE_INCOMPLETE"}


@pytest.mark.asyncio
async def test_synthesis_combine_fails_closed_without_authority():
    """synthesize_findings delegates to execute_agent; without a supplied
    authority reference it fails closed before forwarding."""
    service = _AgentService()
    findings = [
        {"claim": "Alpha", "evidence_ids": ["evidence-1"]},
        {"claim": "Beta", "evidence_ids": ["evidence-2"]},
    ]

    with pytest.raises(HTTPException) as exc_info:
        await agent_api.synthesize_findings(
            findings=findings,
            synthesis_focus="thematic",
            authority_id=None,
            authority_version=None,
            execution_service=service,
        )

    assert exc_info.value.detail == {"code": "EXECUTION_AUTHORITY_REQUIRED"}
    assert service.requests == []


@pytest.mark.asyncio
async def test_chain_mixture_and_workflows_fail_closed_without_authority():
    """literature_analysis_workflow and comprehensive_research_workflow
    delegate to execute_chain_of_agents/execute_mixture_of_agents; without
    a supplied authority reference, both fail closed before forwarding."""
    service = _AgentService()

    with pytest.raises(HTTPException) as workflow_exc:
        await agent_api.literature_analysis_workflow(
            query="Characterize literature workflow",
            domains=["research"],
            authority_id=None,
            authority_version=None,
            execution_service=service,
        )
    with pytest.raises(HTTPException) as mixture_exc:
        await agent_api.comprehensive_research_workflow(
            query="Characterize comprehensive workflow",
            analysis_depth="exhaustive",
            authority_id=None,
            authority_version=None,
            execution_service=service,
        )

    for exc in (workflow_exc, mixture_exc):
        assert exc.value.detail == {"code": "EXECUTION_AUTHORITY_REQUIRED"}
    assert service.requests == []


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
