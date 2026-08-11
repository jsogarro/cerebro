"""Pin the behavior of `MCPServer` — the piece of Path 2 that touches `fastmcp`.

`src/mcp/server.py` used to do `from fastmcp import FastMCP` at module scope,
so anything touching `MCPServer` — including tests that only read its source —
could not be collected without the optional `[mcp]` extra. The import is now
deferred into `MCPServer.__init__`. *Constructing* a server still requires the
extra; importing the module does not, which is what lets the two tests here
that never construct one execute in the environment the gate runs in.

This is deliberately the *only* fastmcp-dependent slice of Path 2. The rest of
`src/mcp/base.py` and `src/mcp/registry.py` need no optional extra at all
(see `test_mcp_tools_baseline.py`), and the individual network tools needed
`scipy` through their package's eager `__init__` — a different, unrelated
dependency, since decoupled and gated by nothing.
"""

from __future__ import annotations

import inspect

import pytest

from src.mcp.server import MCPServer


class TestMCPServerRoutesThroughBaseMCPToolValidation:
    """WAS: `MCPServer.execute_tool` — the path every real caller uses —
    called `tool.execute()` directly, bypassing `BaseMCPTool.__call__`, the
    only place parameter validation and `log_execution` run. So a required
    parameter could be silently absent and the tool would still "run", and no
    invocation reached through the server was logged at all.

    4-Char wrote the original assertion to break loudly the moment this was
    routed. It did, and this is the replacement.
    """

    def test_mcp_server_execute_tool_calls_the_validated_entry_point(self) -> None:
        source = inspect.getsource(MCPServer.execute_tool)
        assert "await tool(**kwargs)" in source
        assert "await tool.execute(**kwargs)" not in source

    def test_server_execute_tool_signature_has_no_deadline_or_capability_params(
        self,
    ) -> None:
        """Still true, and still worth stating.

        `MCPServer` is not itself mediated. In production it is reached only
        through `MCPClient`, which is reached only from `MCPIntegration`, which
        is mediated — so the deadline, capability check, and provenance for a
        network tool call live one layer up. A caller that constructs an
        `MCPServer` directly gets none of them, and nothing here prevents that.
        """
        sig = inspect.signature(MCPServer.execute_tool)
        assert list(sig.parameters) == ["self", "name", "kwargs"]

    @pytest.mark.asyncio
    async def test_server_execute_tool_unknown_name_returns_error_dict(self) -> None:
        pytest.importorskip(
            "fastmcp",
            reason="constructing an MCPServer needs the optional [mcp] extra",
        )
        server = MCPServer()

        result = await server.execute_tool("does-not-exist", query="x")

        assert result == {"success": False, "error": "Tool not found: does-not-exist"}
