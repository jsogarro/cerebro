"""
MCP (Model Context Protocol) Server Implementation.

This module provides MCP tool servers for the research platform,
enabling AI agents to interact with various data sources and tools.
"""

from typing import Any

from src.mcp.base import BaseMCPTool
from src.mcp.registry import ToolRegistry

# MCPServer requires fastmcp which is an optional dependency.
# Use lazy import to avoid crashing when fastmcp is not installed.


def __getattr__(name: str) -> Any:
    if name == "MCPServer":
        from src.mcp.server import MCPServer

        return MCPServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BaseMCPTool",
    "MCPServer",
    "ToolRegistry",
]
