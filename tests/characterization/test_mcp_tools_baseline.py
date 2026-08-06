"""Pin the current behavior of the MCP tool path (`src/mcp/`).

Path 2 of the Wave 4 tool-boundary preflight. `AcademicSearchTool` and
`CitationTool` make real network calls (mocked here — see the network-safety
note below); `KnowledgeGraphTool` and `StatisticsTool` do not.

Two contracts matter more than any single tool's output shape:

1. `BaseMCPTool.__call__` is the only place parameter validation and error
   handling are applied — and `MCPServer.execute_tool` (the path every
   caller in this repo actually uses) calls `tool.execute()` directly,
   bypassing `__call__` entirely. This file pins that bypass.
2. Every concrete tool's own `execute()` also independently catches
   `Exception` and returns `{"success": False, "error": ...}`, so nothing
   here ever raises past the tool boundary today regardless of (1).

Network safety: every test that touches `AcademicSearchTool` or
`CitationTool` patches `httpx.AsyncClient.get` (the pattern already used by
`tests/test_mcp_tools.py`) so no test in this file can reach the network. A
test that forgets the patch would get a `MagicMock` where JSON/text is
expected and fail loudly on `TypeError`/`AttributeError`, not silently pass
by making a live call.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

pytest.importorskip(
    "fastmcp",
    reason="fastmcp lives in optional [mcp] extra; install with `pip install -e .[mcp]`",
)

from src.mcp.base import BaseMCPTool, ToolMetadata, ToolParameter
from src.mcp.registry import ToolRegistry as MCPToolRegistry
from src.mcp.server import MCPServer
from src.mcp.tools.academic_search_tool import AcademicSearchTool
from src.mcp.tools.citation_tool import CitationTool
from src.mcp.tools.knowledge_graph_tool import KnowledgeGraphTool


class _RequiredParamTool(BaseMCPTool):
    """Minimal tool with one required parameter, for validation characterization."""

    def _build_metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="required_param_tool",
            description="Requires 'value'",
            parameters=[
                ToolParameter(
                    name="value", type="string", description="required", required=True
                )
            ],
        )

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        return {"success": True, "echo": kwargs.get("value")}


class TestBaseMCPToolCallContractVsDirectExecute:
    """The validated entry point (`__call__`) is not the one production uses."""

    @pytest.mark.asyncio
    async def test_call_rejects_missing_required_param(self) -> None:
        tool = _RequiredParamTool()

        result = await tool(other="irrelevant")

        assert result["success"] is False
        assert result["error"] == "Invalid parameters"

    @pytest.mark.asyncio
    async def test_direct_execute_skips_required_param_validation_entirely(
        self,
    ) -> None:
        """CHARACTERIZATION: calling `.execute()` directly — which is what
        `MCPServer.execute_tool` does — never runs `validate_parameters`.
        A required field can be silently absent and the tool still "runs".
        """
        tool = _RequiredParamTool()

        result = await tool.execute(other="irrelevant")

        assert result["success"] is True
        assert result["echo"] is None

    @pytest.mark.asyncio
    async def test_call_wrapper_logs_execution_direct_execute_does_not(self) -> None:
        """CHARACTERIZATION: `log_execution` (an audit-adjacent structlog
        call bound to `self.logger`, not stdlib `logging` — so `caplog`
        cannot observe it either way) only fires through `__call__`. The
        production path (`MCPServer.execute_tool` -> `tool.execute()`)
        never reaches it, so today there is no log record — let alone a
        durable one — for any MCP tool invocation reached through the
        server/client.
        """
        tool = _RequiredParamTool()
        tool.logger = MagicMock()

        await tool.execute(value="x")
        tool.logger.info.assert_not_called()

        await tool(value="x")
        tool.logger.info.assert_called_once()

    def test_mcp_server_execute_tool_calls_execute_directly_not_call(self) -> None:
        """Pin the exact bypass mechanism via source inspection, so this
        test breaks (loudly, not silently) the moment 4D routes this
        through the mediator instead.
        """
        source = inspect.getsource(MCPServer.execute_tool)
        assert "await tool.execute(**kwargs)" in source
        assert "await tool(**kwargs)" not in source


class TestUnregisteredAndFailureShapesNeverRaise:
    @pytest.mark.asyncio
    async def test_server_execute_tool_unknown_name_returns_error_dict(self) -> None:
        server = MCPServer()

        result = await server.execute_tool("does-not-exist", query="x")

        assert result == {"success": False, "error": "Tool not found: does-not-exist"}

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


class TestNoTimeoutNoRetryNoCapabilityAtThisLayer:
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

    def test_base_mcp_tool_execute_signature_has_no_deadline_or_capability_params(
        self,
    ) -> None:
        sig = inspect.signature(BaseMCPTool.execute)
        assert list(sig.parameters) == ["self", "kwargs"]

    def test_server_execute_tool_signature_has_no_deadline_or_capability_params(
        self,
    ) -> None:
        sig = inspect.signature(MCPServer.execute_tool)
        assert list(sig.parameters) == ["self", "name", "kwargs"]


class TestMCPToolRegistryHasNoAuthorizationLayer:
    """`src/mcp/registry.py` is a second, distinct `ToolRegistry` from
    `src/agents/tools/registry.py` — mirrors that file's absence of any
    capability or tenant scoping, but for the network-calling tool set.
    """

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

    def test_register_accepts_any_tool_no_identity_or_scope_argument(self) -> None:
        sig = inspect.signature(MCPToolRegistry.register)
        assert list(sig.parameters) == ["self", "tool"]
