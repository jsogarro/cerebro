"""Direct dispatch of research tools through the tool boundary without FastMCP."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.integrations.mcp_integration import MCPIntegration
from src.agents.tools.mediation import InMemoryToolAuditStore, ToolCallIdentity
from src.core.contracts.provenance import ToolInvocationStatus
from src.mcp.tools.academic_search_tool import AcademicSearchTool
from src.mcp.tools.citation_tool import CitationTool
from src.mcp.tools.knowledge_graph_tool import KnowledgeGraphTool
from src.mcp.tools.statistics_tool import StatisticsTool

_IDENTITY = ToolCallIdentity("run-1", "task-1", "attempt-1", None)

_ARXIV_FEED = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<feed xmlns="http://www.w3.org/2005/Atom">'
    "<entry><title>Direct Dispatch Paper</title>"
    "<summary>Verified without fastmcp.</summary>"
    "<published>2024-05-01T00:00:00Z</published>"
    "<id>http://arxiv.org/abs/2405.00001</id>"
    "<author><name>Ada Lovelace</name></author>"
    "</entry></feed>"
)


@pytest.mark.asyncio
async def test_academic_search_succeeds_without_fastmcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "fastmcp", None)

    response = MagicMock()
    response.status_code = 200
    response.text = _ARXIV_FEED

    store = InMemoryToolAuditStore()
    integration = MCPIntegration(enable_fallback=False, audit_store=store)

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=response)):
        result = await integration.search_academic_sources(
            query="direct dispatch",
            databases=["arxiv"],
            max_results=5,
            identity=_IDENTITY,
        )

    assert result["success"] is True
    assert result["tool_outcome"] == "succeeded"
    assert result["sources"][0]["title"] == "Direct Dispatch Paper"
    assert store.invocations[-1].status is ToolInvocationStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_tool_exception_is_recorded_with_original_exception_type() -> None:
    store = InMemoryToolAuditStore()
    integration = MCPIntegration(enable_fallback=False, audit_store=store)

    with patch.object(
        AcademicSearchTool,
        "_search_arxiv",
        new=AsyncMock(side_effect=RuntimeError("upstream parser crash")),
    ):
        result = await integration.search_academic_sources(
            query="crash test",
            databases=["arxiv"],
            identity=_IDENTITY,
        )

    assert result["success"] is False
    assert result["tool_outcome"] == "failed"
    assert "RuntimeError" in (result["detail"] or "")
    assert "upstream parser crash" in (result["detail"] or "")
    assert store.invocations[-1].status is ToolInvocationStatus.FAILED


@pytest.mark.asyncio
async def test_tool_execute_propagates_unexpected_exceptions() -> None:
    academic = AcademicSearchTool()
    with (
        patch.object(
            academic,
            "_search_arxiv",
            new=AsyncMock(side_effect=RuntimeError("academic boom")),
        ),
        pytest.raises(RuntimeError, match="academic boom"),
    ):
        await academic.execute(query="q", databases=["arxiv"])

    citation = CitationTool()
    with (
        patch.object(
            citation,
            "_format_citations",
            side_effect=RuntimeError("citation boom"),
        ),
        pytest.raises(RuntimeError, match="citation boom"),
    ):
        await citation.execute(sources=[{"title": "T"}], style="APA")

    stats_tool = StatisticsTool()
    with (
        patch.object(
            stats_tool,
            "_descriptive_statistics",
            side_effect=RuntimeError("stats boom"),
        ),
        pytest.raises(RuntimeError, match="stats boom"),
    ):
        await stats_tool.execute(operation="descriptive", data=[1, 2, 3])

    kg = KnowledgeGraphTool()
    with (
        patch.object(
            kg,
            "_build_graph",
            side_effect=RuntimeError("kg boom"),
        ),
        pytest.raises(RuntimeError, match="kg boom"),
    ):
        await kg.execute(operation="build_graph", entities=[{"id": "1"}])
