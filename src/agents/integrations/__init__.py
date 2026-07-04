"""
Agent integrations package.

This package provides integration capabilities for agents to use
external tools and services.
"""

from typing import Any

# MCP integration requires fastmcp which is an optional dependency.
# Use lazy imports to avoid crashing when fastmcp is not installed.


def __getattr__(name: str) -> Any:
    if name in ("MCPIntegration", "create_mcp_integrated_agent"):
        from .mcp_integration import MCPIntegration, create_mcp_integrated_agent

        _exports = {
            "MCPIntegration": MCPIntegration,
            "create_mcp_integrated_agent": create_mcp_integrated_agent,
        }
        return _exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["MCPIntegration", "create_mcp_integrated_agent"]
