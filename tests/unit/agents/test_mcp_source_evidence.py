"""The search result's evidence hook: guarded, explicit, and success-only."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.agents.integrations.mcp_integration import MCPIntegration
from src.agents.tools.mediation import ToolCallIdentity


def _client(*, fail: bool = False) -> AsyncMock:
    client = AsyncMock()
    client.health_check.return_value = {"client": "healthy"}
    client.search_academic.return_value = (
        {"success": False, "error": "down"}
        if fail
        else {"success": True, "results": [{"title": "t", "abstract": "a"}]}
    )
    return client


async def _search(integration: MCPIntegration) -> dict[str, Any]:
    return await integration.search_academic_sources(
        "q", identity=ToolCallIdentity("run-1", "task-1", "attempt-1", None)
    )


@pytest.mark.asyncio
async def test_a_raising_recorder_does_not_lose_the_search() -> None:
    recorder = AsyncMock(side_effect=ValueError("boom"))
    result = await _search(
        MCPIntegration(mcp_client=_client(), evidence_recorder=recorder)
    )

    assert result["success"] is True
    assert result["sources"]
    assert result["source_evidence"]["recorded"] == []
    assert result["source_evidence"]["failed"][0]["reason"] == "ValueError"


@pytest.mark.asyncio
async def test_no_recorder_says_so() -> None:
    result = await _search(MCPIntegration(mcp_client=_client()))

    assert result["source_evidence"] == {"enabled": False}


@pytest.mark.asyncio
async def test_fallback_output_is_never_recorded() -> None:
    recorder = AsyncMock()
    result = await _search(
        MCPIntegration(
            mcp_client=_client(fail=True),
            enable_fallback=True,
            evidence_recorder=recorder,
        )
    )

    assert result["fallback"] is True
    assert "source_evidence" not in result
    recorder.assert_not_called()
