"""Fail-closed projection for a retry of an admitted MCP invocation."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from src.agents.integrations.mcp_integration import MCPIntegration
from src.agents.tools.mediation import ToolCallIdentity
from src.core.contracts.capabilities import (
    CapabilityDecision,
    CapabilityDecisionEffect,
)
from src.core.contracts.provenance import ToolInvocation, ToolInvocationStatus
from src.core.contracts.trust import TrustClassification
from src.core.tools import RetryDisposition, ToolBoundary, ToolOutcome

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _pending_outcome(tool_name: str) -> ToolOutcome:
    invocation = ToolInvocation(
        tool_invocation_id=f"invocation-{tool_name}",
        run_id="run-1",
        task_id="task-1",
        attempt_id="attempt-1",
        tool_name=tool_name,
        tool_version="1.0.0",
        status=ToolInvocationStatus.REQUESTED,
        capability_scope=f"scope-{tool_name}",
        idempotency_key=f"key-{tool_name}",
        input={"query": "pending"},
        input_trust=TrustClassification.USER_SUPPLIED,
        requested_at=NOW,
    )
    decision = CapabilityDecision(
        effect=CapabilityDecisionEffect.ALLOW,
        request_fingerprint="a" * 64,
        grant_id="grant-1",
        decided_at=NOW,
    )
    return ToolBoundary._in_progress(invocation, decision=decision)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "empty_payload", "call"),
    [
        (
            "mcp.academic_search",
            {
                "sources": [],
                "total_found": 0,
                "databases_searched": [],
                "search_strategy": "",
            },
            "search_academic_sources",
        ),
        (
            "mcp.format_citations",
            {"formatted_citations": [], "style": "", "total_sources": 0},
            "format_citations",
        ),
        (
            "mcp.analyze_statistics",
            {"analysis": {}, "operation": "", "data_points": 0},
            "analyze_statistics",
        ),
        (
            "mcp.build_knowledge_graph",
            {"graph": {}, "entities": [], "relationships": []},
            "build_knowledge_graph",
        ),
    ],
)
async def test_in_progress_projection_never_runs_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    empty_payload: dict[str, object],
    call: str,
) -> None:
    client = AsyncMock()
    integration = MCPIntegration(mcp_client=client, enable_fallback=True)
    identity = ToolCallIdentity(
        run_id="run-1",
        task_id="task-1",
        attempt_id="attempt-1",
        organization_id="org-1",
    )
    outcome = _pending_outcome(tool_name)
    monkeypatch.setattr(
        integration,
        "_invoke",
        AsyncMock(return_value=(outcome, identity)),
    )

    fallback_names = (
        "_fallback_academic_search",
        "_fallback_citation_formatting",
        "_fallback_statistical_analysis",
        "_fallback_knowledge_graph",
    )
    fallbacks = {
        name: Mock(side_effect=AssertionError(f"unexpected fallback: {name}"))
        for name in fallback_names
    }
    for name, fallback in fallbacks.items():
        monkeypatch.setattr(integration, name, fallback)

    if call == "search_academic_sources":
        result = await integration.search_academic_sources(
            query="pending query", identity=identity
        )
    elif call == "format_citations":
        result = await integration.format_citations(
            sources=[{"title": "pending source"}], identity=identity
        )
    elif call == "analyze_statistics":
        result = await integration.analyze_statistics(
            operation="descriptive", data=[1, 2, 3], identity=identity
        )
    else:
        result = await integration.build_knowledge_graph(
            text="Pending text", entities=[{"id": "1"}], identity=identity
        )

    assert result == {
        **empty_payload,
        "success": False,
        "degraded": True,
        "fallback": False,
        "data_source": "in_progress",
        "tool_outcome": "in_progress",
        "error_code": None,
        "detail": outcome.detail,
        "reason": outcome.detail,
        "retry": RetryDisposition.RETRIABLE.value,
        "tool_invocation_id": outcome.invocation.tool_invocation_id,
        "identity_bound": True,
    }
    for fallback in fallbacks.values():
        fallback.assert_not_called()
