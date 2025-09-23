"""
MCP Client for agent integration.

Provides a client interface for agents to use MCP tools.
"""

import logging
from typing import Any

from src.mcp.server import MCPServer, MCPServerConfig
from src.mcp.tools import (
    AcademicSearchTool,
    CitationTool,
    KnowledgeGraphTool,
    StatisticsTool,
)

logger = logging.getLogger(__name__)


class MCPClient:
    """
    Client for interacting with MCP tools.

    This client provides a simplified interface for agents
    to execute MCP tools without direct server interaction.
    """

    def __init__(self, server_config: MCPServerConfig | None = None):
        """
        Initialize MCP client.

        Args:
            server_config: Server configuration
        """
        self.server = MCPServer(server_config)
        self._register_default_tools()

    def _register_default_tools(self):
        """Register default research tools."""
        tools = [
            AcademicSearchTool(),
            CitationTool(),
            StatisticsTool(),
            KnowledgeGraphTool(),
        ]

        self.server.register_tools(tools)
        logger.info(f"Registered {len(tools)} default tools")

    async def search_academic(
        self, query: str, databases: list[str] = ["arxiv"], max_results: int = 10
    ) -> dict[str, Any]:
        """
        Search academic databases.

        Args:
            query: Search query
            databases: List of databases
            max_results: Maximum results

        Returns:
            Search results
        """
        return await self.server.execute_tool(
            "search_academic", query=query, databases=databases, max_results=max_results
        )

    async def format_citations(
        self, sources: list[dict], style: str = "APA"
    ) -> dict[str, Any]:
        """
        Format citations.

        Args:
            sources: List of sources
            style: Citation style

        Returns:
            Formatted citations
        """
        return await self.server.execute_tool(
            "citation_formatter", sources=sources, style=style
        )

    async def analyze_statistics(self, operation: str, **kwargs) -> dict[str, Any]:
        """
        Perform statistical analysis.

        Args:
            operation: Statistical operation
            **kwargs: Operation parameters

        Returns:
            Analysis results
        """
        return await self.server.execute_tool(
            "statistics_analyzer", operation=operation, **kwargs
        )

    async def build_knowledge_graph(
        self,
        text: str | None = None,
        entities: list | None = None,
        relationships: list | None = None,
    ) -> dict[str, Any]:
        """
        Build knowledge graph.

        Args:
            text: Text for entity extraction
            entities: List of entities
            relationships: List of relationships

        Returns:
            Graph building result
        """
        if text:
            # Extract entities first
            result = await self.server.execute_tool(
                "knowledge_graph", operation="extract_entities", text=text
            )

            if result["success"] and result.get("entities"):
                # Build graph from extracted entities
                entities = [
                    {"id": str(i), "text": e["text"], "type": e["type"]}
                    for i, e in enumerate(result["entities"])
                ]

        if entities:
            return await self.server.execute_tool(
                "knowledge_graph",
                operation="build_graph",
                entities=entities,
                relationships=relationships or [],
            )

        return {"success": False, "error": "No entities provided or extracted"}

    def get_available_tools(self) -> list[str]:
        """
        Get list of available tools.

        Returns:
            List of tool names
        """
        return self.server.get_registered_tools()

    async def execute_tool(self, tool_name: str, **kwargs) -> dict[str, Any]:
        """
        Execute a tool by name.

        Args:
            tool_name: Tool name
            **kwargs: Tool parameters

        Returns:
            Tool execution result
        """
        return await self.server.execute_tool(tool_name, **kwargs)

    async def health_check(self) -> dict[str, Any]:
        """
        Check client and server health.

        Returns:
            Health status
        """
        server_health = await self.server.health_check()

        return {
            "client": "healthy",
            "server": server_health,
            "tools_available": len(self.get_available_tools()),
        }
