"""C1 regression tests for the durable mediated-tool construction seam."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest

import src.ai_brain.integration.masr_supervisor_bridge as bridge_module
import src.api.services.direct_execution_service as service_module
from src.agents.models import AgentResult, AgentTask
from src.agents.tools.durable_audit import SessionToolAuditStore
from src.ai_brain.integration.masr_supervisor_bridge import MASRSupervisorBridge
from src.core.config import Settings
from src.core.contracts import WorkerAssignment


def _worker_assignment() -> WorkerAssignment:
    return WorkerAssignment(
        worker_id="worker-1",
        worker_type="comparative_analysis",
        objective="Exercise durable audit wiring",
        output_schema={},
        permission_scopes=(),
        tool_allowlist=(),
    )


def _task() -> AgentTask:
    return AgentTask(
        id="ephemeral-worker-task",
        agent_type="comparative_analysis",
        input_data={},
        context={
            "run_id": "run-1",
            "task_id": "task-1",
            "attempt_id": "attempt-1",
            "organization_id": "org-1",
        },
    )


class CapturingWorker:
    configs: list[dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.configs.append(kwargs["config"])

    async def execute(self, task: AgentTask) -> AgentResult:
        return AgentResult(task.id, "success", {}, 1.0, 0.0)


def _bare_bridge(*, session_factory: Any = None, audit_store: Any = None) -> Any:
    bridge = object.__new__(MASRSupervisorBridge)
    bridge.component_registry = Mock()
    bridge.component_registry.resolve.return_value = CapturingWorker
    bridge.gemini_service = None
    bridge.session_factory = session_factory
    bridge.audit_store = audit_store
    return bridge


@pytest.mark.asyncio
async def test_enabled_plan_worker_uses_a_session_backed_audit_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = object()
    mcp_constructor = Mock(return_value=object())
    monkeypatch.setattr(bridge_module, "MCPIntegration", mcp_constructor)
    monkeypatch.setattr(
        bridge_module,
        "settings",
        Settings(_env_file=None, MCP_TOOL_PATH_ENABLED=True),
    )
    CapturingWorker.configs.clear()

    await _bare_bridge(session_factory=session_factory)._execute_plan_worker(
        _worker_assignment(), _task()
    )

    assert len(CapturingWorker.configs) == 1
    audit_store = mcp_constructor.call_args.kwargs["audit_store"]
    assert isinstance(audit_store, SessionToolAuditStore)
    assert audit_store.session_factory is session_factory


@pytest.mark.asyncio
async def test_enabled_plan_worker_without_audit_factory_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp_constructor = Mock(return_value=object())
    monkeypatch.setattr(bridge_module, "MCPIntegration", mcp_constructor)
    monkeypatch.setattr(
        bridge_module,
        "settings",
        Settings(_env_file=None, MCP_TOOL_PATH_ENABLED=True),
    )

    with pytest.raises(RuntimeError, match=r"MCP_TOOL_PATH_ENABLED.*session factory"):
        await _bare_bridge()._execute_plan_worker(_worker_assignment(), _task())

    mcp_constructor.assert_not_called()


@pytest.mark.asyncio
async def test_enabled_plan_worker_preserves_explicit_audit_store_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    injected_store = object()
    mcp_constructor = Mock(return_value=object())
    monkeypatch.setattr(bridge_module, "MCPIntegration", mcp_constructor)
    monkeypatch.setattr(
        bridge_module,
        "settings",
        Settings(_env_file=None, MCP_TOOL_PATH_ENABLED=True),
    )
    CapturingWorker.configs.clear()

    await _bare_bridge(audit_store=injected_store)._execute_plan_worker(
        _worker_assignment(), _task()
    )

    assert mcp_constructor.call_args.kwargs["audit_store"] is injected_store


@pytest.mark.asyncio
async def test_disabled_plan_worker_does_not_require_an_audit_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp_constructor = Mock(return_value=object())
    monkeypatch.setattr(bridge_module, "MCPIntegration", mcp_constructor)
    monkeypatch.setattr(
        bridge_module,
        "settings",
        Settings(_env_file=None, MCP_TOOL_PATH_ENABLED=False),
    )
    CapturingWorker.configs.clear()

    await _bare_bridge()._execute_plan_worker(_worker_assignment(), _task())

    mcp_constructor.assert_not_called()
    assert "mcp_integration" not in CapturingWorker.configs[-1]


def test_direct_service_threads_session_factory_to_owned_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_factory = object()
    captured: dict[str, Any] = {}

    class Bridge:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(service_module, "MASRSupervisorBridge", Bridge)

    service_module.DirectExecutionService(session_factory=session_factory)

    assert captured["session_factory"] is session_factory


def test_direct_service_does_not_rebuild_an_injected_bridge() -> None:
    injected_bridge = Mock()

    service = service_module.DirectExecutionService(
        supervisor_bridge=injected_bridge,
        session_factory=object(),
    )

    assert service.supervisor_bridge is injected_bridge
