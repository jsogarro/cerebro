"""Tests for the fabrication deletions in ``src/agents/citation_agent.py``.

Packet 0 of Wave 4 removed several code paths that reported results the
agent never computed:

- ``_resolve_doi`` returned a fabricated "verified" CrossRef response
  (publisher "Mock Publisher") for any DOI-shaped string. (X1)
- ``_export_to_bibtex`` returned a placeholder string as if it were BibTeX
  output. (X4)
- ``_calculate_confidence`` mixed a flat baseline, magic-number additions,
  and an MCP-availability bonus that could inflate confidence independent
  of what was actually verified. (X6)
- ``_verify_single_source`` used a hardcoded ``current_year = 2024`` (wrong
  from 2025 onward) and a journal-name substring match as a "quality"
  signal. (X7)

Each test below asserts the corrected, honest behavior. See the packet
handoff for the transcript proving the prior (fabricating) behavior before
these fixes were applied.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.agents.citation_agent import CitationAgent


@pytest.fixture
def agent() -> CitationAgent:
    return CitationAgent()


class TestDoiResolutionIsHonest:
    """X1: _resolve_doi must never claim a resolution it did not perform."""

    @pytest.mark.asyncio
    async def test_well_formed_doi_reports_unavailable_not_resolved(self, agent):
        result = await agent._resolve_doi("10.1234/does-not-exist-fake")

        assert result["resolved"] is False
        assert result["checked"] == "format_only"
        assert result["reason"] == "doi_resolution_unavailable"
        # The specific fabrication this replaces must not reappear.
        assert "crossref_data" not in result
        assert "publisher" not in str(result)

    @pytest.mark.asyncio
    async def test_malformed_doi_reports_invalid_format(self, agent):
        result = await agent._resolve_doi("not-a-doi")

        assert result["resolved"] is False
        assert result["checked"] == "format_only"
        assert result["reason"] == "invalid_doi_format"

    @pytest.mark.asyncio
    async def test_empty_doi_reports_invalid_format(self, agent):
        result = await agent._resolve_doi("")

        assert result["resolved"] is False
        assert result["reason"] == "invalid_doi_format"

    @pytest.mark.asyncio
    async def test_verify_single_source_never_marks_doi_resolved(self, agent):
        """No DOI resolution service is wired up; verification must reflect that."""
        result = await agent._verify_single_source(
            {"title": "T", "authors": ["A"], "year": 2020, "doi": "10.1234/real"},
            "s1",
        )

        assert result["doi_resolved"] is False
        assert result["doi_info"] == {}
        assert "DOI could not be resolved" in result["issues"]


class TestBibtexExportIsDeleted:
    """X4: no placeholder BibTeX output; the format is simply unsupported here."""

    @pytest.mark.asyncio
    async def test_bibtex_export_no_longer_exists(self, agent):
        assert not hasattr(agent, "_export_to_bibtex")

    @pytest.mark.asyncio
    async def test_bibtex_request_produces_no_export_entry(self, agent):
        citation_result = {"formatted_citations": ["Doe, A. (2024). Title."]}

        exports = await agent._export_citations(citation_result, ["bibtex"])

        assert "bibtex" not in exports
        assert "% BibTeX export" not in str(exports)

    @pytest.mark.asyncio
    async def test_other_export_formats_still_work(self, agent):
        citation_result = {"formatted_citations": ["Doe, A. (2024). Title."]}

        exports = await agent._export_citations(citation_result, ["text", "json"])

        assert exports["text"] == "1. Doe, A. (2024). Title."
        assert "Doe, A." in exports["json"]


class TestConfidenceIsCountsBased:
    """X6: confidence must be derived from actual counts, not magic numbers."""

    def test_full_formatting_and_verification_yields_full_confidence(self, agent):
        confidence = agent._calculate_confidence(
            {"formatted_citations": ["a", "b"]},
            sources=["s1", "s2"],
            verified_count=2,
        )

        assert confidence == 1.0

    def test_zero_verification_no_longer_gets_a_free_baseline(self, agent):
        """Old formula: 0.5 baseline + 0.25 (all formatted) + 0 (mcp) = 0.75.

        That inflated a task with zero real verifications above the old
        code's own 0.7 threshold. The new formula reports what actually
        happened: fully formatted, nothing verified -> 0.5, not 0.75.
        """
        confidence = agent._calculate_confidence(
            {"formatted_citations": ["a", "b"]},
            sources=["s1", "s2"],
            verified_count=0,
        )

        assert confidence == 0.5

    def test_nothing_formatted_or_verified_yields_zero(self, agent):
        confidence = agent._calculate_confidence(
            {"formatted_citations": []}, sources=["s1", "s2"], verified_count=0
        )

        assert confidence == 0.0

    def test_no_sources_yields_zero_not_a_divide_by_zero(self, agent):
        confidence = agent._calculate_confidence(
            {"formatted_citations": []}, sources=[], verified_count=0
        )

        assert confidence == 0.0

    def test_mcp_availability_no_longer_affects_confidence(self, agent):
        """The MCP-availability bonus branch is gone; the signature no
        longer even accepts an mcp_available argument."""
        import inspect

        params = inspect.signature(agent._calculate_confidence).parameters
        assert "mcp_available" not in params

    def test_source_of_truth_is_the_caller_supplied_count_not_citation_data(
        self, agent
    ):
        """Regression guard for a wiring bug found while fixing X6: the
        formatting result's own "verified_sources" field (some formatting
        paths set it to len(sources) unconditionally, unrelated to real
        verification) must not be read. Only the explicit verified_count
        argument — sourced by the caller from real verification — counts."""
        confidence = agent._calculate_confidence(
            {"formatted_citations": ["a", "b"], "verified_sources": 2},
            sources=["s1", "s2"],
            verified_count=0,
        )

        assert confidence == 0.5  # not 1.0 -- citation_data's claim is ignored


class TestJournalAndYearHeuristicsAreHonest:
    """X7: no hardcoded year, no journal-name substring "quality" bonus."""

    @pytest.mark.asyncio
    async def test_current_year_is_not_hardcoded(self, agent):
        real_current_year = datetime.now(UTC).year
        # A source dated one year after the *real* current year must be
        # flagged as a future publication. Under the deleted hardcoded
        # current_year = 2024, any year > 2024 (including a genuinely past
        # year once the real calendar moves on) was wrongly flagged.
        result = await agent._verify_single_source(
            {
                "title": "T",
                "authors": ["A"],
                "year": real_current_year + 1,
            },
            "s1",
        )
        assert "Future publication year" in result["issues"]

        # A source dated exactly the real current year must NOT be flagged,
        # proving the check tracks the real calendar rather than 2024.
        result_current = await agent._verify_single_source(
            {
                "title": "T",
                "authors": ["A"],
                "year": real_current_year,
            },
            "s1",
        )
        assert "Future publication year" not in result_current["issues"]

    @pytest.mark.asyncio
    async def test_reputable_journal_substring_no_longer_grants_a_bonus(self, agent):
        """A source published in a journal whose name happens to contain
        "nature" as a substring must not score higher than an identical
        source in a journal that doesn't -- journal-name substring matching
        is not a source-quality measurement."""
        common = {"title": "T", "authors": ["A"], "year": 2020}

        with_reputable_name = await agent._verify_single_source(
            {**common, "journal": "Nature"}, "s1"
        )
        without = await agent._verify_single_source(
            {**common, "journal": "Some Regional Newsletter"}, "s2"
        )

        assert with_reputable_name["quality_score"] == without["quality_score"]


class TestNoResidualFabricationStrings:
    """Grep-assertions: the exact fabricated strings/symbols must not exist
    anywhere in src/. See tests/unit/qa/test_fabrication_deletion_grep.py
    for the repo-wide sweep; this focuses the same assertion on the file
    that used to contain them, as a fast, file-scoped signal."""

    def test_citation_agent_source_has_no_fabrication_markers(self):
        import inspect

        import src.agents.citation_agent as citation_agent_module

        source = inspect.getsource(citation_agent_module)

        assert "Mock Publisher" not in source
        assert "current_year = 2024" not in source
        assert "reputable_indicators" not in source
        assert "_export_to_bibtex" not in source
        assert "mcp_available" not in source
