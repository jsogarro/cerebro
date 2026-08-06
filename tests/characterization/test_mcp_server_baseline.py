"""Pin the current behavior of `MCPServer` — the piece of Path 2 that requires `fastmcp`.

`src/mcp/server.py` imports `fastmcp` directly (`from fastmcp import FastMCP`),
so anything touching `MCPServer` cannot be collected without the optional
`[mcp]` extra installed. `pytest.importorskip` below makes that dependency
explicit instead of failing collection with a bare `ModuleNotFoundError` in
an environment that only ran `uv sync --extra dev` — the same convention
`tests/test_mcp_integration.py` already uses.

This is deliberately the *only* fastmcp-gated slice of Path 2. The rest of
`src/mcp/base.py` and `src/mcp/registry.py` need no optional extra at all
(see `test_mcp_tools_baseline.py`), and the individual network tools need
`scipy`, not `fastmcp` — a different, unrelated dependency, gated separately
in `test_mcp_network_tools_baseline.py`. Splitting by real import
requirements, rather than gating the whole of Path 2 behind one umbrella
skip, is what lets 12 of the original 15 Path-2 tests execute under the
baseline `--extra dev`-only environment instead of being silently skipped.
"""

from __future__ import annotations

import inspect

import pytest

pytest.importorskip(
    "fastmcp",
    reason="fastmcp lives in optional [mcp] extra; install with `pip install -e .[mcp]`",
)

from src.mcp.server import MCPServer


class TestMCPServerBypassesBaseMCPToolValidation:
    """`MCPServer.execute_tool` (the path every real caller uses) calls
    `tool.execute()` directly — bypassing `BaseMCPTool.__call__`, the only
    place parameter validation and `log_execution` run. See
    `test_mcp_tools_baseline.py::TestBaseMCPToolCallContractVsDirectExecute`
    for the `__call__`-side half of this contract.
    """

    def test_mcp_server_execute_tool_calls_execute_directly_not_call(self) -> None:
        """Pin the exact bypass mechanism via source inspection, so this
        test breaks (loudly, not silently) the moment 4D routes this
        through the mediator instead.
        """
        source = inspect.getsource(MCPServer.execute_tool)
        assert "await tool.execute(**kwargs)" in source
        assert "await tool(**kwargs)" not in source

    @pytest.mark.asyncio
    async def test_server_execute_tool_unknown_name_returns_error_dict(self) -> None:
        server = MCPServer()

        result = await server.execute_tool("does-not-exist", query="x")

        assert result == {"success": False, "error": "Tool not found: does-not-exist"}

    def test_server_execute_tool_signature_has_no_deadline_or_capability_params(
        self,
    ) -> None:
        sig = inspect.signature(MCPServer.execute_tool)
        assert list(sig.parameters) == ["self", "name", "kwargs"]
