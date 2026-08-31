"""
MCP Server implementation using FastMCP.

Provides the main MCP server that manages and exposes tools.
"""

import inspect
from typing import Any, Final

from pydantic import BaseModel
from structlog import get_logger

from src.mcp.base import BaseMCPTool, ToolParameter
from src.mcp.registry import ToolRegistry

logger = get_logger()

_MCP_PARAMETER_TYPES: Final[dict[str, Any]] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list[Any],
    "object": dict[str, Any],
}


def _signature_for_metadata(
    parameters: list[ToolParameter],
) -> tuple[inspect.Signature, dict[str, Any]]:
    """Build a FastMCP-compatible signature from the platform metadata."""
    signature_parameters: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}

    for parameter in parameters:
        annotation = _MCP_PARAMETER_TYPES.get(parameter.type, Any)
        default = inspect.Parameter.empty if parameter.required else parameter.default
        signature_parameters.append(
            inspect.Parameter(
                parameter.name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            )
        )
        annotations[parameter.name] = annotation

    annotations["return"] = dict[str, Any]
    return inspect.Signature(signature_parameters), annotations


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
        # Deferred: `fastmcp` is the optional `[mcp]` extra, and importing it
        # at module scope made every test that merely reads this module's
        # source uncollectable without it. Constructing a server still
        # requires it — this only decouples import from construction.
        from fastmcp import FastMCP

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

        # Register tool with FastMCP
        async def tool_wrapper(**kwargs: Any) -> dict[str, Any]:
            """Wrapper function for MCP tool execution."""
            return await tool(**kwargs)

        # FastMCP validates the callable's inspected signature and rejects a
        # raw VAR_KEYWORD parameter. Keep the forwarding implementation
        # flexible for BaseMCPTool while exposing the typed, named contract
        # described by the platform metadata to the MCP runtime.
        signature, annotations = _signature_for_metadata(metadata.parameters)
        tool_wrapper.__signature__ = signature
        tool_wrapper.__annotations__ = annotations

        _decorated_tool = self.mcp.tool(
            name=metadata.name, description=metadata.description
        )(tool_wrapper)

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
        Execute a tool by name, through its validated entry point.

        Calls the tool rather than its `execute` method. `BaseMCPTool.__call__`
        is the only place `validate_parameters` and `log_execution` run, and
        this method used to skip it — so a required parameter could be silently
        absent and the tool would still "run", and no invocation reached
        through the server was logged at all.

        Args:
            name: Tool name
            **kwargs: Tool parameters

        Returns:
            Tool execution result
        """
        tool = self.get_tool(name)
        if not tool:
            return {"success": False, "error": f"Tool not found: {name}"}

        return await tool(**kwargs)

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
