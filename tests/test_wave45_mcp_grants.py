from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

import src.ai_brain.integration.masr_supervisor_bridge as bridge_module
from src.agents.integrations.mcp_integration import MCPIntegration
from src.agents.integrations.mcp_tool_specs import (
    TOOL_ACADEMIC_SEARCH,
    TOOL_FORMAT_CITATIONS,
    TOOL_VERSION,
)
from src.agents.models import AgentResult, AgentTask
from src.agents.tools.mediation import ToolCallIdentity
from src.ai_brain.integration.masr_supervisor_bridge import MASRSupervisorBridge
from src.core.capabilities import CAPABILITY_GRANTS_CONTEXT_KEY
from src.core.contracts import (
    CapabilityGrant,
    SensitivityClass,
    TrustClassification,
    WorkerAssignment,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def grant(tool_name: str, scope: str, *, task_id: str = "task-1") -> CapabilityGrant:
    return CapabilityGrant(
        grant_id=f"grant-{tool_name}",
        run_id="run-1",
        task_id=task_id,
        capability_scope=scope,
        tool_name=tool_name,
        tool_versions=(TOOL_VERSION,),
        sensitivity=SensitivityClass.READ_ONLY,
        max_input_trust=TrustClassification.EXTERNAL_UNTRUSTED,
        requires_approval=False,
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
    )


def identity() -> ToolCallIdentity:
    return ToolCallIdentity("run-1", "task-1", "attempt-1", "org-1")


def client() -> AsyncMock:
    value = AsyncMock()
    value.health_check.return_value = {"client": "healthy"}
    value.search_academic.return_value = {"success": True, "results": []}
    return value


@pytest.mark.asyncio
async def test_plan_worker_forwards_persisted_grants(monkeypatch: pytest.MonkeyPatch) -> None:
    grants = (grant(TOOL_ACADEMIC_SEARCH, "scope-search"),)
    constructor = Mock(side_effect=lambda **kwargs: MCPIntegration(mcp_client=client(), **kwargs))
    monkeypatch.setattr(bridge_module, "MCPIntegration", constructor)
    monkeypatch.setattr(bridge_module, "settings", Mock(MCP_TOOL_PATH_ENABLED=True))

    seen: dict[str, Any] = {}

    class Worker:
        def __init__(self, **kwargs: Any) -> None:
            seen["integration"] = kwargs["config"]["mcp_integration"]

        async def execute(self, task: AgentTask) -> AgentResult:
            return AgentResult(task.id, "success", {}, 1.0, 0.0)

    bridge = object.__new__(MASRSupervisorBridge)
    bridge.component_registry = Mock()
    bridge.component_registry.resolve.return_value = Worker
    bridge.gemini_service = None
    worker = WorkerAssignment(
        worker_id="w",
        worker_type="comparative_analysis",
        objective="objective",
        output_schema={},
        permission_scopes=(),
        tool_allowlist=(),
    )
    task = AgentTask(
        "task-1",
        "worker",
        {},
        {
            "run_id": "run-1",
            "task_id": "task-1",
            "attempt_id": "attempt-1",
            "organization_id": "org-1",
            CAPABILITY_GRANTS_CONTEXT_KEY: grants,
        },
    )

    await bridge._execute_plan_worker(worker, task)

    constructor.assert_called_once_with(
        enable_fallback=False, identity=ToolCallIdentity.from_agent_task(task), grants=grants
    )


@pytest.mark.asyncio
async def test_invoke_selects_matching_tool_scope_and_passes_all_grants() -> None:
    first = grant(TOOL_ACADEMIC_SEARCH, "scope-search")
    second = grant(TOOL_FORMAT_CITATIONS, "scope-citations")
    integration = MCPIntegration(mcp_client=client(), enable_fallback=False, identity=identity(), grants=(first, second))
    boundary = cast(Any, integration.boundary)
    boundary.invoke = AsyncMock(wraps=boundary.invoke)

    await integration.search_academic_sources("query")

    assert boundary.invoke.await_args is not None
    kwargs = boundary.invoke.await_args.kwargs
    assert kwargs["capability_scope"] == "scope-search"
    assert kwargs["grants"] == [first, second]


@pytest.mark.asyncio
async def test_wrong_tool_grant_denies_without_self_issuing() -> None:
    integration = MCPIntegration(mcp_client=client(), enable_fallback=False, identity=identity(), grants=(grant(TOOL_FORMAT_CITATIONS, "scope-citations"),))
    boundary = cast(Any, integration.boundary)
    boundary.invoke = AsyncMock(wraps=boundary.invoke)

    result = await integration.search_academic_sources("query")

    assert result["success"] is False
    assert boundary.invoke.await_args is not None
    kwargs = boundary.invoke.await_args.kwargs
    assert kwargs["capability_scope"] == TOOL_ACADEMIC_SEARCH
    assert not kwargs["grants"][0].capability_scope.startswith("self-issued:")
