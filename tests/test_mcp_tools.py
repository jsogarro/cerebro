"""
Tests for MCP tools.

Following TDD principles - tests written before implementation.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestAcademicSearchTool:
    """Test cases for Academic Search Tool."""

    @pytest.mark.asyncio
    async def test_search_pubmed(self):
        """Test PubMed search functionality."""
        from src.mcp.tools.academic_search_tool import AcademicSearchTool

        tool = AcademicSearchTool()

        # Mock PubMed API responses
        with patch("httpx.AsyncClient.get") as mock_get:
            # First call - search for IDs
            search_response = MagicMock()
            search_response.status_code = 200
            search_response.json.return_value = {
                "esearchresult": {"idlist": ["12345", "67890"], "count": "2"}
            }

            # Second call - fetch summaries
            summary_response = MagicMock()
            summary_response.status_code = 200
            summary_response.json.return_value = {
                "result": {
                    "12345": {
                        "uid": "12345",
                        "title": "COVID-19 Vaccine Study 1",
                        "authors": [{"name": "Author A"}],
                        "pubdate": "2024",
                    },
                    "67890": {
                        "uid": "67890",
                        "title": "COVID-19 Vaccine Study 2",
                        "authors": [{"name": "Author B"}],
                        "pubdate": "2024",
                    },
                }
            }

            # Return different responses for each call
            mock_get.side_effect = [search_response, summary_response]

            result = await tool.execute(
                query="COVID-19 vaccines", databases=["pubmed"], max_results=10
            )

            assert result["success"] == True
            assert "results" in result
            assert len(result["results"]) > 0
            assert result["source"] == "pubmed"

    @pytest.mark.asyncio
    async def test_search_arxiv(self):
        """Test arXiv search functionality."""
        from src.mcp.tools.academic_search_tool import AcademicSearchTool

        tool = AcademicSearchTool()

        # Mock arXiv API response
        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = """<?xml version="1.0"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
                <entry>
                    <title>Test Paper</title>
                    <author><name>Test Author</name></author>
                    <summary>Test abstract</summary>
                    <id>http://arxiv.org/abs/2024.12345</id>
                </entry>
            </feed>"""
            mock_get.return_value = mock_response

            result = await tool.execute(
                query="machine learning", databases=["arxiv"], max_results=5
            )

            assert result["success"] == True
            assert "results" in result
            assert result["source"] == "arxiv"

    @pytest.mark.asyncio
    async def test_search_multiple_databases(self):
        """Test searching multiple databases."""
        from src.mcp.tools.academic_search_tool import AcademicSearchTool

        tool = AcademicSearchTool()

        result = await tool.execute(
            query="artificial intelligence",
            databases=["pubmed", "arxiv"],
            max_results=20,
        )

        assert result["success"] == True
        assert "results" in result
        assert "sources" in result
        assert len(result["sources"]) == 2

    @pytest.mark.asyncio
    async def test_search_with_filters(self):
        """Test search with filters."""
        from src.mcp.tools.academic_search_tool import AcademicSearchTool

        tool = AcademicSearchTool()

        result = await tool.execute(
            query="deep learning",
            databases=["arxiv"],
            filters={
                "year_start": 2023,
                "year_end": 2024,
                "categories": ["cs.LG", "cs.AI"],
            },
            max_results=10,
        )

        assert result["success"] == True
        assert "results" in result

    @pytest.mark.asyncio
    async def test_invalid_database(self):
        """Test handling of invalid database."""
        from src.mcp.tools.academic_search_tool import AcademicSearchTool

        tool = AcademicSearchTool()

        result = await tool.execute(
            query="test", databases=["invalid_db"], max_results=5
        )

        assert result["success"] == False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_tool_metadata(self):
        """Test tool metadata."""
        from src.mcp.tools.academic_search_tool import AcademicSearchTool

        tool = AcademicSearchTool()
        metadata = tool.get_metadata()

        assert metadata.name == "search_academic"
        assert "academic" in metadata.description.lower()
        assert len(metadata.parameters) > 0
        assert any(p.name == "query" for p in metadata.parameters)


class TestCitationTool:
    """Test cases for Citation Tool."""

    @pytest.mark.asyncio
    async def test_format_apa_citation(self):
        """Test APA citation formatting."""
        from src.mcp.tools.citation_tool import CitationTool

        tool = CitationTool()

        result = await tool.execute(
            sources=[
                {
                    "title": "Test Article",
                    "authors": ["Smith, J.", "Doe, A."],
                    "year": 2024,
                    "journal": "Test Journal",
                    "volume": 10,
                    "pages": "1-10",
                }
            ],
            style="APA",
        )

        assert result["success"] == True
        assert "citations" in result
        assert len(result["citations"]) == 1
        assert "Smith" in result["citations"][0]
        assert "2024" in result["citations"][0]

    @pytest.mark.asyncio
    async def test_format_mla_citation(self):
        """Test MLA citation formatting."""
        from src.mcp.tools.citation_tool import CitationTool

        tool = CitationTool()

        result = await tool.execute(
            sources=[
                {
                    "title": "Test Book",
                    "authors": ["Author, Test"],
                    "year": 2023,
                    "publisher": "Test Publisher",
                    "city": "New York",
                }
            ],
            style="MLA",
        )

        assert result["success"] == True
        assert "citations" in result
        assert "Author" in result["citations"][0]

    @pytest.mark.asyncio
    async def test_doi_resolution(self):
        """Test DOI resolution."""
        from src.mcp.tools.citation_tool import CitationTool

        tool = CitationTool()

        with patch("httpx.AsyncClient.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "title": "Resolved Article",
                "author": [{"given": "John", "family": "Smith"}],
                "published-print": {"date-parts": [[2024, 1, 1]]},
            }
            mock_get.return_value = mock_response

            result = await tool.execute(doi="10.1234/test", style="APA")

            assert result["success"] == True
            assert "citation" in result

    @pytest.mark.asyncio
    async def test_bibtex_export(self):
        """Test BibTeX export."""
        from src.mcp.tools.citation_tool import CitationTool

        tool = CitationTool()

        result = await tool.execute(
            sources=[
                {
                    "title": "Test Article",
                    "authors": ["Smith, J."],
                    "year": 2024,
                    "journal": "Test Journal",
                }
            ],
            format="bibtex",
        )

        assert result["success"] == True
        assert "bibtex" in result
        assert "@article" in result["bibtex"]


class TestStatisticsTool:
    """Test cases for Statistics Tool."""

    @pytest.mark.asyncio
    async def test_descriptive_statistics(self):
        """Test descriptive statistics calculation."""
        from src.mcp.tools.statistics_tool import StatisticsTool

        tool = StatisticsTool()

        result = await tool.execute(
            operation="descriptive", data=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        )

        assert result["success"] == True
        assert "mean" in result
        assert result["mean"] == 5.5
        assert "median" in result
        assert "std" in result

    @pytest.mark.asyncio
    async def test_t_test(self):
        """Test t-test calculation."""
        from src.mcp.tools.statistics_tool import StatisticsTool

        tool = StatisticsTool()

        result = await tool.execute(
            operation="t_test", group1=[1, 2, 3, 4, 5], group2=[6, 7, 8, 9, 10]
        )

        assert result["success"] == True
        assert "t_statistic" in result
        assert "p_value" in result
        assert result["p_value"] < 0.05  # Significant difference

    @pytest.mark.asyncio
    async def test_correlation_analysis(self):
        """Test correlation analysis."""
        from src.mcp.tools.statistics_tool import StatisticsTool

        tool = StatisticsTool()

        result = await tool.execute(
            operation="correlation", x=[1, 2, 3, 4, 5], y=[2, 4, 6, 8, 10]
        )

        assert result["success"] == True
        assert "correlation" in result
        assert result["correlation"] > 0.99  # Perfect correlation

    @pytest.mark.asyncio
    async def test_generate_plot(self):
        """Test plot generation."""
        from src.mcp.tools.statistics_tool import StatisticsTool

        tool = StatisticsTool()

        result = await tool.execute(
            operation="plot", plot_type="histogram", data=[1, 2, 2, 3, 3, 3, 4, 4, 5]
        )

        assert result["success"] == True
        assert "plot_html" in result or "plot_path" in result


class TestKnowledgeGraphTool:
    """Test cases for Knowledge Graph Tool."""

    @pytest.mark.asyncio
    async def test_entity_extraction(self):
        """Test entity extraction from text."""
        from src.mcp.tools.knowledge_graph_tool import KnowledgeGraphTool

        tool = KnowledgeGraphTool()

        result = await tool.execute(
            operation="extract_entities",
            text="Apple Inc. was founded by Steve Jobs in Cupertino, California.",
        )

        assert result["success"] == True
        assert "entities" in result
        assert len(result["entities"]) > 0
        assert any(e["text"] == "Apple Inc." for e in result["entities"])

    @pytest.mark.asyncio
    async def test_build_graph(self):
        """Test knowledge graph building."""
        from src.mcp.tools.knowledge_graph_tool import KnowledgeGraphTool

        tool = KnowledgeGraphTool()

        result = await tool.execute(
            operation="build_graph",
            entities=[
                {"id": "1", "text": "AI", "type": "concept"},
                {"id": "2", "text": "Machine Learning", "type": "concept"},
                {"id": "3", "text": "Deep Learning", "type": "concept"},
            ],
            relationships=[
                {"source": "1", "target": "2", "type": "includes"},
                {"source": "2", "target": "3", "type": "includes"},
            ],
        )

        assert result["success"] == True
        assert "graph" in result
        assert result["graph"]["nodes"] == 3
        assert result["graph"]["edges"] == 2

    @pytest.mark.asyncio
    async def test_graph_analysis(self):
        """Test graph analysis metrics."""
        from src.mcp.tools.knowledge_graph_tool import KnowledgeGraphTool

        tool = KnowledgeGraphTool()

        # Build a graph first
        await tool.execute(
            operation="build_graph",
            entities=[
                {"id": "1", "text": "A", "type": "node"},
                {"id": "2", "text": "B", "type": "node"},
                {"id": "3", "text": "C", "type": "node"},
            ],
            relationships=[
                {"source": "1", "target": "2", "type": "connected"},
                {"source": "2", "target": "3", "type": "connected"},
                {"source": "1", "target": "3", "type": "connected"},
            ],
        )

        result = await tool.execute(operation="analyze_graph")

        assert result["success"] == True
        assert "metrics" in result
        assert "centrality" in result["metrics"]
        assert "communities" in result["metrics"]

    @pytest.mark.asyncio
    async def test_graph_visualization(self):
        """Test graph visualization generation."""
        from src.mcp.tools.knowledge_graph_tool import KnowledgeGraphTool

        tool = KnowledgeGraphTool()

        # Build a simple graph
        await tool.execute(
            operation="build_graph",
            entities=[
                {"id": "1", "text": "Node1", "type": "concept"},
                {"id": "2", "text": "Node2", "type": "concept"},
            ],
            relationships=[{"source": "1", "target": "2", "type": "related"}],
        )

        result = await tool.execute(operation="visualize")

        assert result["success"] == True
        assert "visualization" in result or "html" in result
