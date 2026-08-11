"""Pin the current behavior of `BaseMCPTool`'s validated entry point.

Path 2 of the Wave 4 tool-boundary preflight, dependency-minimal slice:
`BaseMCPTool.__call__` is the only place parameter validation and error
handling are applied, and it is not the path production uses — see
`test_mcp_server_baseline.py::test_mcp_server_execute_tool_calls_execute_directly_not_call`
for the mechanism. This file only needs `src/mcp/base.py` and
`src/mcp/registry.py`, neither of which imports `fastmcp` or `scipy`, so it
collects and runs unconditionally under `uv sync --extra dev` — no
`importorskip` gate.

The remaining Path 2 tests live in two dependency-gated siblings:

- `test_mcp_server_baseline.py` — anything touching `MCPServer` (needs
  `fastmcp`, which `src/mcp/server.py` imports directly).
- `test_mcp_network_tools_baseline.py` — anything touching
  `AcademicSearchTool`/`CitationTool`/`KnowledgeGraphTool` (needs `scipy`,
  transitively — see that file's docstring for why tools that don't use
  scipy themselves still require it to import).

This file was originally one file gated by `pytest.importorskip("fastmcp")`
at module level for all 15 tests; only 3 of those 15 actually touched
`MCPServer`. Splitting means 12 of the original 15 tests now execute under
the baseline `--extra dev`-only environment instead of being silently
skipped at collection.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.mcp.base import BaseMCPTool, ToolMetadata, ToolParameter
from src.mcp.registry import ToolRegistry as MCPToolRegistry


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
        `MCPServer.execute_tool` does (see `test_mcp_server_baseline.py`) —
        never runs `validate_parameters`. A required field can be silently
        absent and the tool still "runs".
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


class TestNoTimeoutNoRetryNoCapabilityAtThisLayer:
    def test_base_mcp_tool_execute_signature_has_no_deadline_or_capability_params(
        self,
    ) -> None:
        sig = inspect.signature(BaseMCPTool.execute)
        assert list(sig.parameters) == ["self", "kwargs"]


class TestMCPToolRegistryHasNoAuthorizationLayer:
    """`src/mcp/registry.py` is a second, distinct `ToolRegistry` from
    `src/agents/tools/registry.py` — mirrors that file's absence of any
    capability or tenant scoping, but for the network-calling tool set.
    """

    def test_register_accepts_any_tool_no_identity_or_scope_argument(self) -> None:
        sig = inspect.signature(MCPToolRegistry.register)
        assert list(sig.parameters) == ["self", "tool"]
