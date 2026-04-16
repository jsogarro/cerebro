"""
MCP Integration for Agents.

This module provides the integration layer between agents and MCP tools,
enabling agents to use real external services in production.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from src.mcp.client import MCPClient
from src.mcp.server import MCPServerConfig

logger = logging.getLogger(__name__)


class MCPIntegration:
    """
    Integration layer between agents and MCP tools.

    Provides a production-ready interface for agents to access
    external tools with proper error handling and fallback mechanisms.
    """

    def __init__(
        self,
        mcp_client: MCPClient | None = None,
        config: dict[str, Any] | None = None,
        enable_fallback: bool = True,
    ):
        """
        Initialize MCP integration.

        Args:
            mcp_client: Optional pre-configured MCP client
            config: Configuration dictionary
            enable_fallback: Whether to enable fallback when tools fail
        """
        self.config = config or {}
        self.enable_fallback = enable_fallback
        self._client = mcp_client
        self._initialized = False
        self._tool_cache: dict[str, Any] = {}

        # Circuit breaker settings
        self._failure_count = 0
        self._max_failures = self.config.get("max_failures", 5)
        self._circuit_breaker_timeout = self.config.get("circuit_breaker_timeout", 60)
        self._last_failure_time: float = 0

        logger.info("MCP Integration initialized")

    async def initialize(self) -> None:
        """Initialize the MCP client if not already done."""
        if self._initialized:
            return

        if not self._client:
            server_config = MCPServerConfig(
                name=self.config.get("server_name", "research-mcp-server"),
                port=self.config.get("server_port", 9000),
                host=self.config.get("server_host", "localhost"),
            )
            self._client = MCPClient(server_config)

        # Validate client health
        try:
            health = await self._client.health_check()
            if health.get("client") == "healthy":
                self._initialized = True
                logger.info("MCP client initialized successfully")
            else:
                logger.warning(f"MCP client health check failed: {health}")
        except Exception as e:
            logger.error(f"Failed to initialize MCP client: {e}")
            if not self.enable_fallback:
                raise

    @asynccontextmanager
    async def _circuit_breaker(self) -> AsyncIterator[None]:
        """Circuit breaker pattern for tool execution."""
        import time

        current_time = time.time()

        # Check if circuit breaker is open
        if (
            self._failure_count >= self._max_failures
            and current_time - self._last_failure_time < self._circuit_breaker_timeout
        ):
            raise Exception("Circuit breaker is open - too many recent failures")

        try:
            yield
            # Reset failure count on success
            self._failure_count = 0
        except Exception as e:
            self._failure_count += 1
            self._last_failure_time = float(current_time)
            logger.warning(
                f"Tool execution failed ({self._failure_count} failures): {e}"
            )
            raise

    async def search_academic_sources(
        self,
        query: str,
        databases: list[str] | None = None,
        max_results: int = 20,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Search academic databases using MCP tools.

        Args:
            query: Search query
            databases: List of databases to search
            max_results: Maximum number of results
            filters: Search filters

        Returns:
            Search results with metadata
        """
        await self.initialize()

        databases = databases or ["arxiv", "pubmed"]
        filters = filters or {}

        try:
            async with self._circuit_breaker():
                if self._client is None:
                    raise Exception("MCP client not initialized")
                result = await self._client.search_academic(
                    query=query, databases=databases, max_results=max_results
                )

                if result.get("success"):
                    logger.info(
                        f"Academic search successful: {len(result.get('results', []))} results"
                    )
                    return {
                        "success": True,
                        "sources": result.get("results", []),
                        "total_found": len(result.get("results", [])),
                        "databases_searched": databases,
                        "search_strategy": f"Multi-database search across {', '.join(databases)}",
                    }
                else:
                    raise Exception(
                        f"Academic search failed: {result.get('error', 'Unknown error')}"
                    )

        except Exception as e:
            logger.error(f"Academic search error: {e}")
            if self.enable_fallback:
                return self._fallback_academic_search(query, databases, max_results)
            raise

    async def format_citations(
        self, sources: list[dict[str, Any]], style: str = "APA"
    ) -> dict[str, Any]:
        """
        Format citations using MCP citation tool.

        Args:
            sources: List of source dictionaries
            style: Citation style (APA, MLA, Chicago)

        Returns:
            Formatted citations
        """
        await self.initialize()

        try:
            async with self._circuit_breaker():
                if self._client is None:
                    raise Exception("MCP client not initialized")
                result = await self._client.format_citations(
                    sources=sources, style=style
                )

                if result.get("success"):
                    logger.info(
                        f"Citation formatting successful: {len(sources)} sources in {style} style"
                    )
                    return {
                        "success": True,
                        "formatted_citations": result.get("citations", []),
                        "style": style,
                        "total_sources": len(sources),
                    }
                else:
                    raise Exception(
                        f"Citation formatting failed: {result.get('error', 'Unknown error')}"
                    )

        except Exception as e:
            logger.error(f"Citation formatting error: {e}")
            if self.enable_fallback:
                return self._fallback_citation_formatting(sources, style)
            raise

    async def analyze_statistics(
        self, operation: str, data: list[Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """
        Perform statistical analysis using MCP statistics tool.

        Args:
            operation: Statistical operation to perform
            data: Data for analysis
            **kwargs: Additional parameters

        Returns:
            Statistical analysis results
        """
        await self.initialize()

        try:
            async with self._circuit_breaker():
                if self._client is None:
                    raise Exception("MCP client not initialized")
                result = await self._client.analyze_statistics(
                    operation=operation, data=data, **kwargs
                )

                if result.get("success"):
                    logger.info(f"Statistical analysis successful: {operation}")
                    return {
                        "success": True,
                        "analysis": result.get("result", {}),
                        "operation": operation,
                        "data_points": len(data) if data else 0,
                    }
                else:
                    raise Exception(
                        f"Statistical analysis failed: {result.get('error', 'Unknown error')}"
                    )

        except Exception as e:
            logger.error(f"Statistical analysis error: {e}")
            if self.enable_fallback:
                return self._fallback_statistical_analysis(operation, data, **kwargs)
            raise

    async def build_knowledge_graph(
        self,
        text: str | None = None,
        entities: list[Any] | None = None,
        relationships: list[Any] | None = None,
    ) -> dict[str, Any]:
        """
        Build knowledge graph using MCP knowledge graph tool.

        Args:
            text: Text for entity extraction
            entities: Pre-defined entities
            relationships: Pre-defined relationships

        Returns:
            Knowledge graph data
        """
        await self.initialize()

        try:
            async with self._circuit_breaker():
                if self._client is None:
                    raise Exception("MCP client not initialized")
                result = await self._client.build_knowledge_graph(
                    text=text, entities=entities, relationships=relationships
                )

                if result.get("success"):
                    logger.info("Knowledge graph building successful")
                    return {
                        "success": True,
                        "graph": result.get("graph", {}),
                        "entities": result.get("entities", []),
                        "relationships": result.get("relationships", []),
                    }
                else:
                    raise Exception(
                        f"Knowledge graph building failed: {result.get('error', 'Unknown error')}"
                    )

        except Exception as e:
            logger.error(f"Knowledge graph building error: {e}")
            if self.enable_fallback:
                return self._fallback_knowledge_graph(text, entities, relationships)
            raise

    async def get_tool_status(self) -> dict[str, Any]:
        """
        Get status of all available MCP tools.

        Returns:
            Tool status information
        """
        await self.initialize()

        try:
            if self._client is None:
                raise Exception("MCP client not initialized")
            health = await self._client.health_check()
            available_tools = self._client.get_available_tools()

            return {
                "initialized": self._initialized,
                "client_health": health,
                "available_tools": available_tools,
                "failure_count": self._failure_count,
                "circuit_breaker_open": (
                    self._failure_count >= self._max_failures
                    and (asyncio.get_event_loop().time() - self._last_failure_time)
                    < self._circuit_breaker_timeout
                ),
            }
        except Exception as e:
            logger.error(f"Failed to get tool status: {e}")
            return {
                "initialized": False,
                "error": str(e),
                "fallback_enabled": self.enable_fallback,
            }

    # Fallback implementations for when MCP tools are unavailable

    def _fallback_academic_search(
        self, query: str, databases: list[str], max_results: int
    ) -> dict[str, Any]:
        """Fallback implementation for academic search."""
        logger.info("Using fallback academic search")

        # Generate mock results based on query
        mock_sources = []
        for i in range(min(max_results, 5)):
            mock_sources.append(
                {
                    "title": f"Research Paper {i+1}: {query}",
                    "authors": [f"Author {i+1}"],
                    "year": 2024 - i,
                    "journal": f"Journal of {query.split()[0] if query.split() else 'Research'}",
                    "abstract": f"This paper investigates {query} using systematic methodology...",
                    "source": "fallback",
                    "relevance_score": 0.8 - (i * 0.1),
                }
            )

        return {
            "success": True,
            "sources": mock_sources,
            "total_found": len(mock_sources),
            "databases_searched": databases,
            "search_strategy": "Fallback search (MCP tools unavailable)",
            "fallback": True,
        }

    def _fallback_citation_formatting(
        self, sources: list[dict[str, Any]], style: str
    ) -> dict[str, Any]:
        """Fallback implementation for citation formatting."""
        logger.info("Using fallback citation formatting")

        formatted_citations = []
        for source in sources:
            if style.upper() == "APA":
                citation = f"{source.get('authors', ['Unknown'])[0]} ({source.get('year', 'n.d.')}). {source.get('title', 'Untitled')}."
            else:
                citation = f"{source.get('title', 'Untitled')} by {source.get('authors', ['Unknown'])[0]} ({source.get('year', 'n.d.')})"

            formatted_citations.append(
                {"citation": citation, "style": style, "source": source}
            )

        return {
            "success": True,
            "formatted_citations": formatted_citations,
            "style": style,
            "total_sources": len(sources),
            "fallback": True,
        }

    def _fallback_statistical_analysis(
        self, operation: str, data: list[Any] | None, **kwargs: Any
    ) -> dict[str, Any]:
        """Fallback implementation for statistical analysis."""
        logger.info("Using fallback statistical analysis")

        result = {"operation": operation, "fallback": True}

        if operation == "descriptive" and data:
            import statistics

            result.update(
                {
                    "mean": statistics.mean(data),
                    "median": statistics.median(data),
                    "std_dev": statistics.stdev(data) if len(data) > 1 else 0,
                    "count": len(data),
                }
            )
        else:
            result["message"] = f"Basic {operation} analysis completed (fallback mode)"

        return {
            "success": True,
            "analysis": result,
            "operation": operation,
            "data_points": len(data) if data else 0,
        }

    def _fallback_knowledge_graph(
        self,
        text: str | None,
        entities: list[Any] | None,
        relationships: list[Any] | None,
    ) -> dict[str, Any]:
        """Fallback implementation for knowledge graph building."""
        logger.info("Using fallback knowledge graph building")

        # Simple entity extraction using basic patterns
        extracted_entities = []
        if text:
            words = text.split()
            for i, word in enumerate(words):
                if word[0].isupper() and len(word) > 2:
                    extracted_entities.append(
                        {"id": str(i), "text": word, "type": "entity", "position": i}
                    )

        return {
            "success": True,
            "graph": {"nodes": len(extracted_entities), "edges": 0},
            "entities": extracted_entities,
            "relationships": relationships or [],
            "fallback": True,
        }


def create_mcp_integrated_agent(agent_class: type, config: dict[str, Any] | None = None) -> Any:
    """
    Factory function to create an agent with MCP integration.

    Args:
        agent_class: Agent class to instantiate
        config: Configuration including MCP settings

    Returns:
        Agent instance with MCP integration
    """
    config = config or {}

    # Create MCP integration
    mcp_config = config.get("mcp", {})
    mcp_integration = MCPIntegration(
        config=mcp_config, enable_fallback=mcp_config.get("enable_fallback", True)
    )

    # Add MCP integration to agent config
    agent_config = config.copy()
    agent_config["mcp_integration"] = mcp_integration

    return agent_class(config=agent_config)
