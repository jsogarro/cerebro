"""Observed contracts of the legacy ``/api/v1/query`` route functions."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi import BackgroundTasks, HTTPException

from src.api.routes import query_api
from src.api.services.direct_execution_service import ExecutionStatus
from src.middleware.tenant_context import TenantContext

# These characterization tests now run as an authenticated tenant, because
# the query routes require one. The service stub records the organization it
# is called with so the pinned behaviour includes that scoping.
TENANT = TenantContext(
    user_id="user-1", organization_id="11111111-1111-1111-1111-111111111111"
)


class _QueryService:
    def __init__(self) -> None:
        self.started: list[tuple[object, dict[str, object]]] = []
        self.organizations: list[str | None] = []
        self.status = ExecutionStatus(
            execution_id="execution-1",
            project_id="00000000-0000-0000-0000-000000000001",
            status="running",
            progress_percentage=25.0,
            current_phase="masr_routing",
            routing_decision={"strategy": "balanced"},
            supervisor_type="research",
            agent_results={"research": {"finding": "pinned"}},
            started_at=datetime(2026, 7, 26, tzinfo=UTC),
        )

    async def start_research_execution(
        self, project, context, *, organization_id=None, **kwargs
    ):
        self.started.append((project, context))
        self.organizations.append(organization_id)
        return self.status.execution_id

    async def get_execution_status(self, execution_id, *, organization_id=None):
        self.organizations.append(organization_id)
        return self.status if execution_id == self.status.execution_id else None

    async def get_execution_results(self, execution_id, *, organization_id=None):
        self.organizations.append(organization_id)
        if execution_id == self.status.execution_id:
            return {"output": "legacy-result"}
        return None

    async def resume_execution(self, project_id):
        return "resumed-1" if project_id == UUID(int=1) else None


@pytest.mark.asyncio
async def test_research_pins_placeholder_metrics_and_ignored_execution_options():
    service = _QueryService()
    request = query_api.IntelligentQueryRequest(
        query="Characterize the current public query response.",
        domains=["research"],
        enable_real_time_updates=False,
        timeout_seconds=61,
    )

    response = await query_api.intelligent_research_query(
        request, BackgroundTasks(), service, TENANT
    )

    assert response.status == "running"
    assert response.results == {"research": {"finding": "pinned"}}
    assert response.estimated_cost == 0.015
    assert response.estimated_quality == 0.85
    assert response.confidence == 0.85
    assert response.routing_time_ms == 50.0
    assert response.selected_agents == []
    _, context = service.started[0]
    assert context["api_endpoint"] == "intelligent_research_query"
    assert "enable_real_time_updates" not in context
    assert "timeout_seconds" not in context


@pytest.mark.asyncio
async def test_analyze_and_synthesize_translate_to_research_context():
    service = _QueryService()

    await query_api.intelligent_analysis_query(
        query_api.AnalysisRequest(
            query="Analyze baseline behavior", analysis_type="basic"
        ),
        BackgroundTasks(),
        service,
        TENANT,
    )
    await query_api.intelligent_synthesis_query(
        query_api.SynthesisRequest(
            query="Synthesize baseline behavior", source_materials=[{"id": "a"}]
        ),
        BackgroundTasks(),
        service,
        TENANT,
    )

    assert service.started[0][1]["api_endpoint"] == "intelligent_research_query"
    assert service.started[0][1]["analysis_type"] == "basic"
    assert service.started[1][1]["api_endpoint"] == "intelligent_research_query"
    assert service.started[1][1]["source_materials"] == [{"id": "a"}]


@pytest.mark.asyncio
async def test_methodology_and_comparison_wrappers_pin_exact_context():
    service = _QueryService()

    methodology = await query_api.intelligent_methodology_query(
        query="Design a reproducible mixed-methods study.",
        research_type="mixed",
        domains=["education"],
        execution_service=service,
        tenant_context=TENANT,
    )
    comparison = await query_api.intelligent_comparison_query(
        query="Compare the documented outcomes across cohorts.",
        comparison_focus="findings",
        domains=["healthcare"],
        execution_service=service,
        tenant_context=TENANT,
    )

    methodology_project, methodology_context = service.started[0]
    comparison_project, comparison_context = service.started[1]
    assert methodology.execution_id == "execution-1"
    assert methodology_project.query.text == (
        "Methodology design: Design a reproducible mixed-methods study."
    )
    assert methodology_project.query.domains == ["education"]
    assert methodology_context == {
        "research_type": "mixed",
        "focus": "methodology",
        "routing_strategy": "balanced",
        "quality_preference": None,
        "cost_preference": None,
        "api_endpoint": "intelligent_research_query",
    }
    assert comparison.execution_id == "execution-1"
    assert comparison_project.query.text == (
        "Compare findings: Compare the documented outcomes across cohorts."
    )
    assert comparison_project.query.domains == ["healthcare"]
    assert comparison_context == {
        "comparison_focus": "findings",
        "focus": "comparative_analysis",
        "routing_strategy": "quality_focused",
        "quality_preference": None,
        "cost_preference": None,
        "api_endpoint": "intelligent_research_query",
    }


@pytest.mark.asyncio
async def test_execution_status_results_and_resume_pin_legacy_error_details():
    service = _QueryService()

    status = await query_api.get_execution_status("execution-1", service, TENANT)
    results = await query_api.get_execution_results("execution-1", service, TENANT)
    resumed = await query_api.resume_execution(str(UUID(int=1)), service)

    assert status["status"] == "running"
    assert status["current_phase"] == "masr_routing"
    assert results == {"output": "legacy-result"}
    assert resumed == {
        "execution_id": "resumed-1",
        "project_id": str(UUID(int=1)),
        "status": "resumed",
        "message": "Execution resumed from checkpoint",
    }

    with pytest.raises(HTTPException, match="Invalid project_id format") as invalid:
        await query_api.resume_execution("not-a-uuid", service)
    assert invalid.value.status_code == 400

    with pytest.raises(HTTPException, match="not found") as missing:
        await query_api.get_execution_results("missing", service, TENANT)
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_routing_recommendation_is_local_heuristic_and_invalid_context_is_500():
    strategies = await query_api.get_available_routing_strategies()
    recommendation = await query_api.get_routing_recommendation(
        query="short query", context='{"domains": ["research"]}'
    )

    assert strategies["default_strategy"] == "balanced"
    assert recommendation["query_analysis"] == {
        "complexity": "simple",
        "estimated_domains": ["research"],
        "confidence": 0.85,
    }
    assert (
        recommendation["routing_recommendation"]["suggested_strategy"]
        == "cost_efficient"
    )

    with pytest.raises(
        HTTPException, match="Failed to generate routing recommendation"
    ) as error:
        await query_api.get_routing_recommendation(query="query", context="not-json")
    assert error.value.status_code == 500
