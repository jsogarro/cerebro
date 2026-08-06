"""Pin the current behavior of the individual network-calling MCP tools.

`AcademicSearchTool`, `CitationTool`, and `KnowledgeGraphTool` are gated here
behind `scipy`, not `fastmcp` — and none of them import `scipy` themselves.
The dependency comes from `src/mcp/tools/__init__.py`, which unconditionally
does `from src.mcp.tools.statistics_tool import StatisticsTool` alongside the
other three. Because Python must execute a package's `__init__.py` before
any of its submodules, `from src.mcp.tools.citation_tool import CitationTool`
transitively requires `scipy` even though `citation_tool.py` never imports
it — confirmed by importing each tool module alone, in a fresh process, with
`scipy` absent: all three fail with the identical
`ModuleNotFoundError: No module named 'scipy'` traceback rooted in
`src/mcp/tools/__init__.py:11`, not in the tool's own file. That is a real,
separate finding from the `fastmcp`/`MCPServer` one in
`test_mcp_server_baseline.py`: this package eagerly couples three
network-only tools to one unrelated statistics dependency.

Network safety: every test that touches `AcademicSearchTool` or
`CitationTool` patches `httpx.AsyncClient.get` (the pattern already used by
`tests/test_mcp_tools.py`) so no test in this file can reach the network. A
test that forgets the patch would get a `MagicMock` where JSON/text is
expected and fail loudly on `TypeError`/`AttributeError`, not silently pass
by making a live call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

pytest.importorskip(
    "scipy",
    reason=(
        "src/mcp/tools/__init__.py eagerly imports StatisticsTool alongside "
        "every other tool in the package, so importing AcademicSearchTool/"
        "CitationTool/KnowledgeGraphTool alone still requires scipy "
        "(declared in pyproject.toml's [stats] extra)"
    ),
)

from src.mcp.registry import ToolRegistry as MCPToolRegistry
from src.mcp.tools.academic_search_tool import AcademicSearchTool
from src.mcp.tools.citation_tool import CitationTool
from src.mcp.tools.knowledge_graph_tool import KnowledgeGraphTool


class TestFailureShapesNeverRaise:
    @pytest.mark.asyncio
    async def test_academic_search_network_failure_is_caught_not_raised(self) -> None:
        """A connection failure inside httpx must not propagate — every
        `_search_*` helper wraps its own body in try/except Exception and
        returns `[]`, so the tool-level result is still `success: True`
        with empty results, not a raised exception and not `success: False`.
        This is the "always succeeds, quietly returns nothing" shape a
        caller has to know about independently of the `success` flag.
        """
        tool = AcademicSearchTool()

        with patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("refused")):
            result = await tool.execute(
                query="test", databases=["arxiv"], max_results=5
            )

        assert result["success"] is True
        assert result["results"] == []
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_citation_doi_resolution_network_failure_returns_error_dict(
        self,
    ) -> None:
        tool = CitationTool()

        with patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("refused")):
            result = await tool.execute(doi="10.1234/whatever")

        assert result["success"] is False
        assert "refused" in result["error"]

    @pytest.mark.asyncio
    async def test_citation_doi_not_found_status_code_is_a_clean_error_not_raise(
        self,
    ) -> None:
        tool = CitationTool()
        not_found = MagicMock()
        not_found.status_code = 404

        with patch("httpx.AsyncClient.get", AsyncMock(return_value=not_found)):
            result = await tool.execute(doi="10.9999/missing")

        assert result == {"success": False, "error": "DOI not found: 10.9999/missing"}

    @pytest.mark.asyncio
    async def test_knowledge_graph_analyze_before_build_is_a_clean_error(self) -> None:
        """No persisted graph state exists yet — pins that `analyze_graph`
        depends entirely on in-process instance state (`self.graph`) set by
        a prior `build_graph` call on the *same* tool instance; nothing
        durable backs it.
        """
        tool = KnowledgeGraphTool()

        result = await tool.execute(operation="analyze_graph")

        assert result == {"success": False, "error": "No graph built yet"}


class TestNoTimeoutNoRetryAtThisLayer:
    def test_academic_search_tool_has_exactly_one_hardcoded_timeout(self) -> None:
        """CHARACTERIZATION: the only timeout anywhere on this path is the
        fixed 30s httpx client timeout set at tool construction. There is
        no per-call timeout, no cancellation, and no retry — a slow but
        eventually-responding upstream blocks for up to 30s per database
        per call with nothing above this layer able to shorten that.
        """
        tool = AcademicSearchTool()

        assert tool.client.timeout.connect == 30.0
        assert tool.client.timeout.read == 30.0

    def test_citation_tool_has_exactly_one_hardcoded_timeout(self) -> None:
        tool = CitationTool()

        assert tool.client.timeout.connect == 30.0


class TestMCPToolRegistryHasNoAuthorizationLayer:
    def test_duplicate_registration_replaces_silently_with_only_a_log_warning(
        self,
    ) -> None:
        registry = MCPToolRegistry()
        first = AcademicSearchTool()
        second = AcademicSearchTool()

        registry.register(first)
        registry.register(second)

        assert registry.get_tool("search_academic") is second
        assert len(registry.list_tools()) == 1
