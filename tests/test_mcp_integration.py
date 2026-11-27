"""Integration tests for mediated research tools through MCPIntegration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.integrations.mcp_integration import MCPIntegration


class TestDirectResearchToolsIntegration:
    """Exercise all four research tools through MCPIntegration and ToolBoundary."""

    @pytest.mark.asyncio
    async def test_academic_search(self) -> None:
        marker = "zz-live-network-guard-marker-6d1b8f"
        arxiv_feed = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            f"<entry><title>{marker}</title>"
            f"<summary>{marker} abstract</summary>"
            "<published>2024-01-01T00:00:00Z</published>"
            "<id>http://arxiv.org/abs/0000.00000</id>"
            "</entry></feed>"
        )
        response = MagicMock()
        response.status_code = 200
        response.text = arxiv_feed
        response.json.return_value = {}

        integration = MCPIntegration(enable_fallback=False)

        with patch(
            "httpx.AsyncClient.get", new=AsyncMock(return_value=response)
        ) as mocked_get:
            result = await integration.search_academic_sources(
                query="test query", databases=["arxiv"], max_results=5
            )

        assert result["success"] is True
        assert mocked_get.await_count >= 1
        assert marker in str(result)

    @pytest.mark.asyncio
    async def test_citation_formatting(self) -> None:
        integration = MCPIntegration(enable_fallback=False)

        result = await integration.format_citations(
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

        assert result["success"] is True
        assert len(result["formatted_citations"]) == 1

    @pytest.mark.asyncio
    async def test_statistical_analysis(self) -> None:
        integration = MCPIntegration(enable_fallback=False)

        result = await integration.analyze_statistics(
            operation="descriptive", data=[1, 2, 3, 4, 5]
        )

        assert result["success"] is True
        assert result["analysis"]["mean"] == 3.0

    @pytest.mark.asyncio
    async def test_knowledge_graph_building_from_entities_and_text(self) -> None:
        integration = MCPIntegration(enable_fallback=False)

        entities = [
            {"id": "1", "text": "Node1", "type": "concept"},
            {"id": "2", "text": "Node2", "type": "concept"},
        ]
        relationships = [{"source": "1", "target": "2", "type": "related"}]

        result = await integration.build_knowledge_graph(
            entities=entities, relationships=relationships
        )
        assert result["success"] is True
        assert result["graph"]["nodes"] == 2
        assert result["graph"]["edges"] == 1

        from_text = await integration.build_knowledge_graph(
            text="Apple Inc. was founded by Steve Jobs in California."
        )
        assert from_text["success"] is True
        assert from_text["graph"]["nodes"] >= 1

    @pytest.mark.asyncio
    async def test_tool_status(self) -> None:
        integration = MCPIntegration(enable_fallback=False)

        status = await integration.get_tool_status()

        assert status["initialized"] is True
        assert status["client_health"]["status"] == "healthy"
        assert len(status["available_tools"]) == 4
