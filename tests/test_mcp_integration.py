"""
Integration tests for MCP server and tools.
"""

import pytest

from src.mcp.client import MCPClient
from src.mcp.server import MCPServer, MCPServerConfig
from src.mcp.tools import (
    AcademicSearchTool,
    CitationTool,
    KnowledgeGraphTool,
    StatisticsTool,
)


class TestMCPServer:
    """Test MCP server functionality."""

    def test_server_initialization(self):
        """Test server initialization."""
        config = MCPServerConfig(name="Test Server")
        server = MCPServer(config)

        assert server.config.name == "Test Server"
        assert server.registry is not None

    def test_tool_registration(self):
        """Test tool registration."""
        server = MCPServer()
        tool = AcademicSearchTool()

        server.register_tool(tool)

        assert "search_academic" in server.get_registered_tools()
        assert server.get_tool("search_academic") is not None

    def test_multiple_tool_registration(self):
        """Test registering multiple tools."""
        server = MCPServer()
        tools = [
            AcademicSearchTool(),
            CitationTool(),
            StatisticsTool(),
            KnowledgeGraphTool(),
        ]

        server.register_tools(tools)

        assert len(server.get_registered_tools()) == 4

    @pytest.mark.asyncio
    async def test_tool_execution(self):
        """Test tool execution through server."""
        server = MCPServer()
        server.register_tool(CitationTool())

        result = await server.execute_tool(
            "citation_formatter",
            sources=[{"title": "Test", "authors": ["Author"], "year": 2024}],
            style="APA",
        )

        assert result["success"] == True

    @pytest.mark.asyncio
    async def test_health_check(self):
        """Test server health check."""
        server = MCPServer()
        server.register_tool(AcademicSearchTool())

        health = await server.health_check()

        assert health["status"] == "healthy"
        assert health["tools_available"] == 1


class TestMCPClient:
    """Test MCP client functionality."""

    def test_client_initialization(self):
        """Test client initialization with default tools."""
        client = MCPClient()

        tools = client.get_available_tools()
        assert len(tools) == 4
        assert "search_academic" in tools
        assert "citation_formatter" in tools
        assert "statistics_analyzer" in tools
        assert "knowledge_graph" in tools

    @pytest.mark.asyncio
    async def test_academic_search(self):
        """Test academic search through client."""
        client = MCPClient()

        result = await client.search_academic(
            query="test query", databases=["arxiv"], max_results=5
        )

        assert "success" in result

    @pytest.mark.asyncio
    async def test_citation_formatting(self):
        """Test citation formatting through client."""
        client = MCPClient()

        result = await client.format_citations(
            sources=[
                {
                    "title": "Test Paper",
                    "authors": ["Smith, J."],
                    "year": 2024,
                    "journal": "Test Journal",
                }
            ],
            style="APA",
        )

        assert result["success"] == True
        assert "citations" in result

    @pytest.mark.asyncio
    async def test_statistical_analysis(self):
        """Test statistical analysis through client."""
        client = MCPClient()

        result = await client.analyze_statistics(
            operation="descriptive", data=[1, 2, 3, 4, 5]
        )

        assert result["success"] == True
        assert "mean" in result

    @pytest.mark.asyncio
    async def test_knowledge_graph_building(self):
        """Test knowledge graph building through client."""
        client = MCPClient()

        entities = [
            {"id": "1", "text": "Node1", "type": "concept"},
            {"id": "2", "text": "Node2", "type": "concept"},
        ]
        relationships = [{"source": "1", "target": "2", "type": "related"}]

        result = await client.build_knowledge_graph(
            entities=entities, relationships=relationships
        )

        assert result["success"] == True
        assert result["graph"]["nodes"] == 2
        assert result["graph"]["edges"] == 1

    @pytest.mark.asyncio
    async def test_client_health_check(self):
        """Test client health check."""
        client = MCPClient()

        health = await client.health_check()

        assert health["client"] == "healthy"
        assert health["server"]["status"] == "healthy"
        assert health["tools_available"] == 4


class TestToolRegistry:
    """Test tool registry functionality."""

    def test_registry_operations(self):
        """Test registry basic operations."""
        from src.mcp.registry import ToolRegistry

        registry = ToolRegistry()
        tool = AcademicSearchTool()

        # Register tool
        registry.register(tool)
        assert "search_academic" in registry.list_tools()

        # Get tool
        retrieved = registry.get_tool("search_academic")
        assert retrieved is not None

        # Get metadata
        metadata = registry.get_metadata("search_academic")
        assert metadata.name == "search_academic"

        # Unregister tool
        success = registry.unregister("search_academic")
        assert success == True
        assert "search_academic" not in registry.list_tools()

    def test_registry_search(self):
        """Test registry search functionality."""
        from src.mcp.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(AcademicSearchTool())
        registry.register(CitationTool())

        # Search by query
        results = registry.search_tools("academic")
        assert "search_academic" in results

        # Search by tag
        results = registry.get_tools_by_tag("citation")
        assert "citation_formatter" in results
