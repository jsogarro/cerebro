"""Compatibility adapters delegate through one application-owned kernel."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from src.api.routes import agent_api, query_api
from src.api.services.agent_execution_service import (
    get_application_agent_execution_service,
)
from src.api.services.research_kernel import (
    compose_application_research_kernel,
    get_application_research_kernel,
    get_kernel_execution_results,
    get_kernel_execution_status,
    resume_kernel_execution,
)
from src.core.kernel import (
    RegistryEntry,
    RegistryKey,
    RegistryNamespace,
    ResearchKernel,
    TypedRegistry,
)
from src.models.agent_api_models import (
    AgentExecutionRequest,
    AgentExecutionResponse,
    AgentType,
)


class _DirectExecutionBackend:
    """Small direct-execution fake used to prove adapter delegation."""

    def __init__(self) -> None:
        self.closed = False
        self.calls: list[tuple[str, object, dict[str, Any] | None]] = []
        self.supervisor_registry = TypedRegistry(
            [
                RegistryEntry(
                    RegistryKey(RegistryNamespace.SUPERVISOR, "research"),
                    object(),
                )
            ]
        )

    async def start_research_execution(
        self,
        project: object,
        context: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append(("start", project, context))
        return "kernel-execution"

    async def get_execution_status(self, execution_id: str) -> SimpleNamespace | None:
        self.calls.append(("status", execution_id, None))
        if execution_id == "missing":
            return None
        return SimpleNamespace(
            status="running",
            progress_percentage=25.0,
            current_phase="masr_routing",
            routing_decision={"strategy": "balanced"},
            supervisor_type="research",
            workers_used=1,
            errors=[],
            agent_results={},
            quality_scores={},
            execution_time_seconds=0.0,
            started_at=datetime(2026, 7, 27, tzinfo=UTC),
        )

    async def get_execution_results(self, execution_id: str) -> dict[str, Any] | None:
        self.calls.append(("results", execution_id, None))
        if execution_id == "missing":
            return None
        return {"output": "legacy"}

    async def resume_execution(self, project_id: UUID) -> str | None:
        self.calls.append(("resume", project_id, None))
        return "kernel-resumed" if project_id == UUID(int=1) else None


class _AgentExecutionBackend:
    """Small agent-execution fake used to prove adapter delegation."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object, object | None]] = []

    async def execute_single_agent(
        self,
        agent_type: AgentType,
        request: AgentExecutionRequest,
    ) -> AgentExecutionResponse:
        self.calls.append(("single", agent_type, request))
        return AgentExecutionResponse(
            execution_id="kernel-agent-execution",
            agent_type=agent_type,
            status="completed",
            output={"source": "application-kernel"},
            confidence=0.9,
            quality_score=0.9,
            execution_time_seconds=0.1,
            started_at=datetime(2026, 7, 27, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_query_adapter_executes_through_composed_application_kernel() -> None:
    backend = _DirectExecutionBackend()
    kernel = compose_application_research_kernel(backend)

    response = await query_api.intelligent_research_query(
        query_api.IntelligentQueryRequest(query="Use the composed kernel."),
        background_tasks=BackgroundTasks(),
        execution_service=kernel,
    )

    assert isinstance(kernel, ResearchKernel)
    assert response.execution_id == "kernel-execution"
    assert [call[0] for call in backend.calls] == ["start", "status"]
    assert kernel.registry is backend.supervisor_registry


@pytest.mark.asyncio
async def test_composed_kernel_preserves_query_status_result_and_resume_reads() -> None:
    backend = _DirectExecutionBackend()
    kernel = compose_application_research_kernel(backend)

    status = await get_kernel_execution_status(kernel, "kernel-execution")
    results = await get_kernel_execution_results(kernel, "kernel-execution")
    resumed = await resume_kernel_execution(kernel, UUID(int=1))

    assert status.status == "running"
    assert results == {"output": "legacy"}
    assert resumed == "kernel-resumed"
    assert [call[0] for call in backend.calls] == ["status", "results", "resume"]


@pytest.mark.asyncio
async def test_query_operation_adapter_preserves_success_and_not_found_contracts() -> (
    None
):
    backend = _DirectExecutionBackend()
    kernel = compose_application_research_kernel(backend)

    status = await query_api.get_execution_status("kernel-execution", kernel)
    results = await query_api.get_execution_results("kernel-execution", kernel)
    resumed = await query_api.resume_execution(str(UUID(int=1)), kernel)

    assert status["status"] == "running"
    assert status["current_phase"] == "masr_routing"
    assert results == {"output": "legacy"}
    assert resumed == {
        "execution_id": "kernel-resumed",
        "project_id": str(UUID(int=1)),
        "status": "resumed",
        "message": "Execution resumed from checkpoint",
    }

    with pytest.raises(HTTPException) as missing_status:
        await query_api.get_execution_status("missing", kernel)
    assert missing_status.value.status_code == 404
    assert missing_status.value.detail == "Execution missing not found"

    with pytest.raises(HTTPException) as missing_results:
        await query_api.get_execution_results("missing", kernel)
    assert missing_results.value.status_code == 404
    assert (
        missing_results.value.detail == "Execution missing not found or not completed"
    )

    with pytest.raises(HTTPException) as missing_resume:
        await query_api.resume_execution(str(UUID(int=2)), kernel)
    assert missing_resume.value.status_code == 404
    assert missing_resume.value.detail == (
        f"No recoverable checkpoint found for project {UUID(int=2)}"
    )

    with pytest.raises(HTTPException) as invalid_resume:
        await query_api.resume_execution("not-a-uuid", kernel)
    assert invalid_resume.value.status_code == 400
    assert invalid_resume.value.detail == "Invalid project_id format: not-a-uuid"


def test_application_dependency_reuses_the_kernel_owned_by_lifespan() -> None:
    backend = _DirectExecutionBackend()
    kernel = compose_application_research_kernel(backend)
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(
                state=SimpleNamespace(research_kernel=kernel),
            ),
        }
    )

    resolved = get_application_research_kernel(
        request,
        backend,
    )

    assert resolved is kernel


def test_application_dependency_adapts_existing_lightweight_overrides() -> None:
    backend = _DirectExecutionBackend()
    del backend.supervisor_registry
    request = Request(
        {
            "type": "http",
            "app": SimpleNamespace(state=SimpleNamespace()),
        }
    )

    resolved = get_application_research_kernel(
        request,
        backend,
    )

    assert isinstance(resolved, ResearchKernel)
    assert resolved.registry.keys == ()


def test_agent_routes_declare_the_application_kernel_dependency() -> None:
    execute_route = next(
        route
        for route in agent_api.router.routes
        if route.path == "/api/v1/agents/{agent_type}/execute"
    )

    dependency_names = {
        dependency.call.__name__
        for dependency in execute_route.dependant.dependencies
        if dependency.call is not None
    }

    assert "get_application_agent_research_kernel" in dependency_names


@pytest.mark.asyncio
async def test_agent_adapter_executes_through_composed_application_kernel() -> None:
    direct_backend = _DirectExecutionBackend()
    agent_backend = _AgentExecutionBackend()
    kernel = compose_application_research_kernel(direct_backend, agent_backend)
    request = AgentExecutionRequest(query="Use the application-owned agent backend.")

    response = await agent_api.execute_agent(
        AgentType.LITERATURE_REVIEW,
        request,
        background_tasks=None,
        execution_service=kernel,
    )

    assert response.execution_id == "kernel-agent-execution"
    assert response.output == {"source": "application-kernel"}
    assert agent_backend.calls == [
        ("single", AgentType.LITERATURE_REVIEW, request),
    ]


def test_agent_http_adapter_accepts_lightweight_backend_override() -> None:
    backend = _AgentExecutionBackend()
    test_app = FastAPI()
    test_app.include_router(agent_api.router)
    test_app.dependency_overrides[get_application_agent_execution_service] = lambda: (
        backend
    )

    with TestClient(test_app) as client:
        response = client.post(
            "/api/v1/agents/literature-review/execute",
            json={"query": "Use the raw ASGI backend override."},
        )

    assert response.status_code == 200
    assert response.json()["execution_id"] == "kernel-agent-execution"
    assert backend.calls[0][0] == "single"
