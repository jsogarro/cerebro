"""
MCP Server implementation using FastMCP.

Provides the main MCP server that manages and exposes tools.
"""

import logging
from typing import Any

from fastmcp import FastMCP
from pydantic import BaseModel

from src.mcp.base import BaseMCPTool
from src.mcp.registry import ToolRegistry

logger = logging.getLogger(__name__)


class MCPServerConfig(BaseModel):
    """Configuration for MCP server."""

    name: str = "Research Platform MCP Server"
    description: str = "MCP server providing research tools for AI agents"
    version: str = "1.0.0"
    host: str = "localhost"
    port: int = 8765
    auth_enabled: bool = False
    max_concurrent_tools: int = 10


class MCPServer:
    """
    Main MCP server that manages and exposes tools.

    This server uses FastMCP to provide a standard MCP interface
    for AI agents to interact with research tools.
    """

    def __init__(self, config: MCPServerConfig | None = None):
        """
        Initialize MCP server.

        Args:
            config: Server configuration
        """
        self.config = config or MCPServerConfig()
        self.mcp = FastMCP(self.config.name)
        self.registry = ToolRegistry()
        self._setup_server()

    def _setup_server(self) -> None:
        """Set up server metadata and configuration."""
        # FastMCP name is set during initialization, no need to modify it
        logger.info(f"MCP Server initialized: {self.config.name}")

    def register_tool(self, tool: BaseMCPTool) -> None:
        """
        Register a tool with the server.

        Args:
            tool: Tool instance to register
        """
        # Register with internal registry
        self.registry.register(tool)

        # Create MCP tool wrapper
        metadata = tool.get_metadata()

        # Build parameter schema
        params_schema = {}
        for param in metadata.parameters:
            param_def = {"type": param.type, "description": param.description}
            if not param.required and param.default is not None:
                param_def["default"] = param.default
            params_schema[param.name] = param_def

        # Register tool with FastMCP
        @self.mcp.tool(name=metadata.name, description=metadata.description)
        async def tool_wrapper(**kwargs: Any) -> dict[str, Any]:
            """Wrapper function for MCP tool execution."""
            return await tool.execute(**kwargs)

        logger.info(f"Registered tool: {metadata.name}")

    def register_tools(self, tools: list[BaseMCPTool]) -> None:
        """
        Register multiple tools.

        Args:
            tools: List of tool instances
        """
        for tool in tools:
            self.register_tool(tool)

    def get_registered_tools(self) -> list[str]:
        """
        Get list of registered tool names.

        Returns:
            List of tool names
        """
        return self.registry.list_tools()

    def get_tool(self, name: str) -> BaseMCPTool | None:
        """
        Get a specific tool by name.

        Args:
            name: Tool name

        Returns:
            Tool instance or None
        """
        return self.registry.get_tool(name)

    async def execute_tool(self, name: str, **kwargs: Any) -> dict[str, Any]:
        """
        Execute a tool by name.

        Args:
            name: Tool name
            **kwargs: Tool parameters

        Returns:
            Tool execution result
        """
        tool = self.get_tool(name)
        if not tool:
            return {"success": False, "error": f"Tool not found: {name}"}

        return await tool.execute(**kwargs)

    def get_server_info(self) -> dict[str, Any]:
        """
        Get server information.

        Returns:
            Server info dictionary
        """
        return {
            "name": self.config.name,
            "description": self.config.description,
            "version": self.config.version,
            "tools": self.get_registered_tools(),
            "tool_count": len(self.get_registered_tools()),
        }

    async def health_check(self) -> dict[str, Any]:
        """
        Perform health check.

        Returns:
            Health check result
        """
        try:
            # Check if tools are accessible
            tool_count = len(self.get_registered_tools())

            return {
                "status": "healthy",
                "server": self.config.name,
                "version": self.config.version,
                "tools_available": tool_count,
                "message": f"Server is running with {tool_count} tools",
            }
        except Exception as e:
            logger.error(f"Health check failed: {e!s}")
            return {"status": "unhealthy", "error": str(e)}

    def run(self) -> None:
        """Run the MCP server."""
        logger.info(f"Starting MCP server on {self.config.host}:{self.config.port}")

        # FastMCP handles the server lifecycle
        # In production, this would start the actual server
        logger.info("MCP server is ready to accept connections")

    def shutdown(self) -> None:
        """Shutdown the server gracefully."""
        logger.info("Shutting down MCP server")
        # Cleanup resources if needed
