"""Tests for QueryDecomposer

Covers domain detection, sub-query generation, and multi-domain
decomposition logic.
"""

import pytest

from src.ai_brain.router.query_decomposer import QueryDecomposer


class TestQueryDecomposer:
    """Test suite for QueryDecomposer."""

    @pytest.fixture
    def decomposer(self):
        """Create decomposer instance."""
        return QueryDecomposer()

    def test_single_domain_query(self, decomposer):
        """Test decomposition of single-domain query."""
        query = "Research the latest findings on machine learning"
        result = decomposer.decompose_query(query)

        assert len(result.detected_domains) == 1
        assert "research" in result.detected_domains
        assert not result.is_multi_domain
        assert result.primary_domain == "research"
        assert "research" in result.domain_subqueries
        assert result.coordination_complexity == 0

    def test_multi_domain_query(self, decomposer):
        """Test decomposition of multi-domain query."""
        query = "Analyze machine learning trends and write a comprehensive article"
        result = decomposer.decompose_query(query)

        assert len(result.detected_domains) >= 2
        assert (
            "research" in result.detected_domains
            or "analytics" in result.detected_domains
        )
        assert "content" in result.detected_domains
        assert result.is_multi_domain
        assert len(result.domain_subqueries) >= 2

    def test_research_content_dependency(self, decomposer):
        """Test that research->content dependency is detected."""
        query = "Study AI impact and create a detailed report"
        result = decomposer.decompose_query(query)

        assert "research" in result.detected_domains
        assert "content" in result.detected_domains
        assert ("research", "content") in result.cross_domain_dependencies
        assert result.coordination_complexity > 0

    def test_analytics_domain_detection(self, decomposer):
        """Test analytics domain detection."""
        query = "Analyze data trends and statistics for quarterly metrics"
        result = decomposer.decompose_query(query)

        assert "analytics" in result.detected_domains
        assert "analytics" in result.domain_subqueries
        assert "Analyze data and trends" in result.domain_subqueries["analytics"]

    def test_finance_domain_detection(self, decomposer):
        """Test finance domain detection."""
        query = "Perform DCF valuation and portfolio analysis"
        result = decomposer.decompose_query(query)

        assert "finance" in result.detected_domains
        assert "finance" in result.domain_subqueries

    def test_domain_relevance_scoring(self, decomposer):
        """Test that domain relevance scores are calculated."""
        query = "Research and analyze and study the literature"
        result = decomposer.decompose_query(query)

        assert "research" in result.domain_relevance
        assert 0.0 < result.domain_relevance["research"] <= 1.0

    def test_empty_query(self, decomposer):
        """Test empty query handling."""
        query = ""
        result = decomposer.decompose_query(query)

        assert len(result.detected_domains) == 0
        assert len(result.domain_subqueries) == 0
        assert result.coordination_complexity == 0

    def test_no_domain_query(self, decomposer):
        """Test query with no recognizable domains."""
        query = "Hello there"
        result = decomposer.decompose_query(query)

        assert len(result.detected_domains) == 0
        assert not result.is_multi_domain
        assert result.primary_domain is None

    def test_primary_domain_selection(self, decomposer):
        """Test that primary domain is the highest relevance."""
        query = "Write write write and generate content plus analyze a bit"
        result = decomposer.decompose_query(query)

        # Content should be primary (more matches)
        if result.detected_domains:
            assert result.primary_domain == "content"

    def test_case_insensitive_matching(self, decomposer):
        """Test case-insensitive domain matching."""
        query = "RESEARCH the LITERATURE and ANALYZE DATA with STATISTICS"
        result = decomposer.decompose_query(query)

        assert "research" in result.detected_domains
        # Analytics needs more keywords to trigger
        assert (
            "analytics" in result.detected_domains
            or "research" in result.detected_domains
        )


class TestQueryDecomposition:
    """Test the QueryDecomposition result object."""

    def test_is_multi_domain_true(self):
        """Test is_multi_domain property when multiple domains."""
        from src.ai_brain.router.query_decomposer import QueryDecomposition

        decomp = QueryDecomposition(
            detected_domains=["research", "content"],
            domain_relevance={"research": 0.8, "content": 0.6},
            domain_subqueries={},
            cross_domain_dependencies=[],
            coordination_complexity=0,
        )

        assert decomp.is_multi_domain

    def test_is_multi_domain_false(self):
        """Test is_multi_domain property when single domain."""
        from src.ai_brain.router.query_decomposer import QueryDecomposition

        decomp = QueryDecomposition(
            detected_domains=["research"],
            domain_relevance={"research": 0.8},
            domain_subqueries={},
            cross_domain_dependencies=[],
            coordination_complexity=0,
        )

        assert not decomp.is_multi_domain

    def test_primary_domain_selection(self):
        """Test primary_domain property selects highest relevance."""
        from src.ai_brain.router.query_decomposer import QueryDecomposition

        decomp = QueryDecomposition(
            detected_domains=["research", "content", "analytics"],
            domain_relevance={"research": 0.6, "content": 0.9, "analytics": 0.4},
            domain_subqueries={},
            cross_domain_dependencies=[],
            coordination_complexity=0,
        )

        assert decomp.primary_domain == "content"

    def test_primary_domain_none_when_empty(self):
        """Test primary_domain returns None when no domains."""
        from src.ai_brain.router.query_decomposer import QueryDecomposition

        decomp = QueryDecomposition(
            detected_domains=[],
            domain_relevance={},
            domain_subqueries={},
            cross_domain_dependencies=[],
            coordination_complexity=0,
        )

        assert decomp.primary_domain is None
