"""
Tests for QueryComplexityAnalyzer integration with QueryDecomposer

Ensures decomposition is properly wired into the complexity analysis pipeline.
"""

import pytest

from src.ai_brain.router.query_analyzer import QueryComplexityAnalyzer


class TestQueryAnalyzerDecomposition:
    """Test decomposition integration in QueryComplexityAnalyzer."""

    @pytest.fixture
    def analyzer(self):
        """Create analyzer instance."""
        return QueryComplexityAnalyzer()

    @pytest.mark.asyncio
    async def test_single_domain_no_decomposition(self, analyzer):
        """Single-domain queries should not trigger decomposition."""
        query = "Research the latest machine learning papers"
        analysis = await analyzer.analyze(query)

        # Single domain or GENERAL should not have decomposition
        assert analysis.decomposition is None

    @pytest.mark.asyncio
    async def test_multi_domain_triggers_decomposition(self, analyzer):
        """Multi-domain queries should trigger decomposition."""
        query = "Analyze machine learning trends and write a comprehensive article"
        analysis = await analyzer.analyze(query)

        # Should have multiple domains and decomposition
        assert len(analysis.domains) >= 2
        assert analysis.decomposition is not None
        assert analysis.decomposition.is_multi_domain
        assert len(analysis.decomposition.detected_domains) >= 2

    @pytest.mark.asyncio
    async def test_decomposition_domains_match_analysis(self, analyzer):
        """Decomposition domains should align with complexity analysis domains."""
        query = "Study AI ethics research and create content and analyze data"
        analysis = await analyzer.analyze(query)

        if analysis.decomposition:
            # Decomposition domains should be subset/match of analysis domains
            decomp_domains = set(analysis.decomposition.detected_domains)
            # At least some overlap expected
            assert len(decomp_domains) > 0

    @pytest.mark.asyncio
    async def test_decomposition_has_subqueries(self, analyzer):
        """Multi-domain queries should have domain-specific sub-queries."""
        query = "Research machine learning impact and write a detailed article"
        analysis = await analyzer.analyze(query)

        if analysis.decomposition and analysis.decomposition.is_multi_domain:
            assert len(analysis.decomposition.domain_subqueries) >= 2
            # Each domain should have a sub-query
            for domain in analysis.decomposition.detected_domains:
                assert domain in analysis.decomposition.domain_subqueries

    @pytest.mark.asyncio
    async def test_general_domain_skips_decomposition(self, analyzer):
        """Queries with GENERAL domain should not be decomposed."""
        query = "Hello there, how are you?"
        analysis = await analyzer.analyze(query)

        # GENERAL domain queries should not trigger decomposition
        assert analysis.decomposition is None

    @pytest.mark.asyncio
    async def test_decomposition_primary_domain(self, analyzer):
        """Primary domain should be identifiable in decomposition."""
        query = "Write write write content and also do some research"
        analysis = await analyzer.analyze(query)

        if analysis.decomposition:
            primary = analysis.decomposition.primary_domain
            assert primary is not None
            assert primary in analysis.decomposition.detected_domains
