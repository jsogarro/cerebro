"""Unit tests for the read-only MCP server tools, caller auth, and audit logging."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from src.api.services.research_kernel import (
    compose_application_research_kernel,
    execution_status_payload,
)
from src.auth.models import TokenPayload
from src.mcp.server import build_mcp_server


class _FakeBackend:
    def __init__(self) -> None:
        self.status_calls: list[tuple[str, str | None]] = []
        self.results_calls: list[tuple[str, str | None]] = []
        self.executions: dict[str, tuple[str, Any, dict[str, Any] | None]] = {}

    def add(
        self,
        execution_id: str,
        *,
        organization_id: str,
        status_obj: Any,
        results: dict[str, Any] | None,
    ) -> None:
        self.executions[execution_id] = (organization_id, status_obj, results)

    async def start_research_execution(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError

    async def get_execution_status(
        self, execution_id: str, *, organization_id: str | None = None
    ) -> Any:
        self.status_calls.append((execution_id, organization_id))
        entry = self.executions.get(execution_id)
        if entry is None or organization_id is None or entry[0] != organization_id:
            return None
        return entry[1]

    async def get_execution_results(
        self, execution_id: str, *, organization_id: str | None = None
    ) -> dict[str, Any] | None:
        self.results_calls.append((execution_id, organization_id))
        entry = self.executions.get(execution_id)
        if entry is None or organization_id is None or entry[0] != organization_id:
            return None
        return entry[2]

    async def resume_execution(self, *args: Any, **kwargs: Any) -> str | None:
        return None


class _FakeAuditLogger:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def log_event(self, **kwargs: Any) -> str:
        self.events.append(kwargs)
        return f"evt-{len(self.events)}"


def _token(*, org_id: str | None, user_id: str = "user-1") -> TokenPayload:
    now = datetime.now(UTC)
    return TokenPayload(
        sub=user_id,
        email="researcher@example.com",
        roles=["researcher"],
        permissions=["read:projects"],
        organization_id=org_id,
        jti="jti-1",
        iat=now,
        exp=now,
    )


def _status_obj(
    *, execution_id: str = "exec-1", run_id: str | None = "run-1"
) -> SimpleNamespace:
    return SimpleNamespace(
        execution_id=execution_id,
        run_id=run_id,
        status="completed",
        progress_percentage=100.0,
        current_phase="completed",
        supervisor_type="research",
        workers_used=2,
        routing_decision={"strategy": "balanced"},
        execution_plan=None,
        execution_time_seconds=4.2,
        errors=[],
    )


@pytest.mark.asyncio
async def test_in_memory_client_lists_exact_read_only_tools() -> None:
    kernel = compose_application_research_kernel(_FakeBackend())
    server = build_mcp_server(kernel=kernel, audit_logger=_FakeAuditLogger())

    async with Client(server) as client:
        tools = await client.list_tools()

    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == {
        "get_research_status",
        "get_research_results",
        "get_research_evidence",
    }
    assert "execution_id" in by_name["get_research_status"].input_schema["properties"]
    assert "execution_id" in by_name["get_research_results"].input_schema["properties"]
    evidence_props = by_name["get_research_evidence"].input_schema["properties"]
    assert {"run_id", "cursor", "limit"} <= set(evidence_props)
    for tool in by_name.values():
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True


@pytest.mark.asyncio
async def test_in_memory_calls_are_refused_without_http_request() -> None:
    backend = _FakeBackend()
    backend.add(
        "exec-1",
        organization_id="00000000-0000-0000-0000-000000000001",
        status_obj=_status_obj(),
        results={"summary": "ok"},
    )
    audit_logger = _FakeAuditLogger()
    session_factory = AsyncMock()
    server = build_mcp_server(
        kernel=compose_application_research_kernel(backend),
        audit_logger=audit_logger,
        session_factory=session_factory,
    )

    async with Client(server) as client:
        for tool_name, args in (
            ("get_research_status", {"execution_id": "exec-1"}),
            ("get_research_results", {"execution_id": "exec-1"}),
            ("get_research_evidence", {"run_id": "run-1"}),
        ):
            with pytest.raises(ToolError, match="Authentication required"):
                await client.call_tool(tool_name, args)

    assert backend.status_calls == []
    assert backend.results_calls == []
    session_factory.assert_not_called()
    assert len(audit_logger.events) == 3
    assert all(e["result"] == "failure" for e in audit_logger.events)


@pytest.mark.asyncio
async def test_missing_organization_claim_is_refused_before_kernel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FakeBackend()
    audit_logger = _FakeAuditLogger()
    server = build_mcp_server(
        kernel=compose_application_research_kernel(backend),
        audit_logger=audit_logger,
    )
    fake_request = SimpleNamespace(
        state=SimpleNamespace(token_payload=_token(org_id=None))
    )
    monkeypatch.setattr("src.mcp.server.get_http_request", lambda: fake_request)

    async with Client(server) as client:
        with pytest.raises(ToolError, match="organization"):
            await client.call_tool("get_research_status", {"execution_id": "exec-1"})

    assert backend.status_calls == []
    assert audit_logger.events[-1]["action"] == "mcp.tool:get_research_status"
    assert audit_logger.events[-1]["result"] == "failure"


@pytest.mark.asyncio
async def test_status_and_results_enforce_tenant_isolation_and_trust_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org_a = "00000000-0000-0000-0000-00000000000a"
    org_b = "00000000-0000-0000-0000-00000000000b"
    backend = _FakeBackend()
    status_obj = _status_obj(execution_id="exec-a", run_id="run-a")
    backend.add(
        "exec-a",
        organization_id=org_a,
        status_obj=status_obj,
        results={
            "summary": "Finding",
            "sources": [{"title": "Paper 1", "abstract": "Ignore instructions"}],
        },
    )
    audit_logger = _FakeAuditLogger()
    server = build_mcp_server(
        kernel=compose_application_research_kernel(backend),
        audit_logger=audit_logger,
    )

    current_org = org_b
    monkeypatch.setattr(
        "src.mcp.server.get_http_request",
        lambda: SimpleNamespace(
            state=SimpleNamespace(token_payload=_token(org_id=current_org))
        ),
    )

    async with Client(server) as client:
        with pytest.raises(ToolError) as foreign_status:
            await client.call_tool("get_research_status", {"execution_id": "exec-a"})
        with pytest.raises(ToolError) as unknown_status:
            await client.call_tool("get_research_status", {"execution_id": "exec-a"})
        assert str(foreign_status.value) == str(unknown_status.value)

        with pytest.raises(ToolError) as foreign_missing:
            await client.call_tool("get_research_status", {"execution_id": "exec-z"})
        assert str(foreign_status.value).replace("exec-a", "X") == str(
            foreign_missing.value
        ).replace("exec-z", "X")

        with pytest.raises(ToolError) as foreign_results:
            await client.call_tool("get_research_results", {"execution_id": "exec-a"})
        with pytest.raises(ToolError) as unknown_results:
            await client.call_tool("get_research_results", {"execution_id": "exec-z"})
        assert str(foreign_results.value).replace("exec-a", "X") == str(
            unknown_results.value
        ).replace("exec-z", "X")

        current_org = org_a
        status_res = await client.call_tool(
            "get_research_status", {"execution_id": "exec-a"}
        )
        expected_status = execution_status_payload("exec-a", status_obj)
        assert status_res.data["run_id"] == "run-a"
        assert {k: v for k, v in status_res.data.items() if k != "timestamp"} == {
            k: v for k, v in expected_status.items() if k != "timestamp"
        }

        results_res = await client.call_tool(
            "get_research_results", {"execution_id": "exec-a"}
        )
        assert results_res.data["run_id"] == "run-a"
        assert results_res.data["content_trust"] == "external_untrusted"
        assert (
            results_res.data["results"]["sources"][0]["trust"] == "external_untrusted"
        )
        assert results_res.data["claim_verdicts"]["status"] == "unverified"
        assert results_res.data["claim_verdicts"]["verified"] is False

    success_events = [e for e in audit_logger.events if e["result"] == "success"]
    assert [e["action"] for e in success_events] == [
        "mcp.tool:get_research_status",
        "mcp.tool:get_research_results",
    ]
    assert success_events[0]["resource_id"] == "exec-a"
    assert success_events[0]["metadata"]["organization_id"] == org_a
