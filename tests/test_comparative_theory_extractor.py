"""Characterization tests for comparative theory extraction."""

from unittest.mock import AsyncMock

import pytest

from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent
from src.agents.comparative_theory_extractor import TheoryExtractor


def test_theory_extractor_preserves_legacy_research_query_shape() -> None:
    extractor = TheoryExtractor()

    query = extractor.build_research_query(
        {
            "items": ["Method A", "Method B", "Method C", "Method D"],
            "criteria": ["Accuracy", "Speed", "Cost", "Scale"],
        }
    )

    assert (
        query
        == "comparative analysis Method A vs Method B vs Method C "
        "Accuracy Speed Cost comparison study"
    )


def test_theory_extractor_preserves_fallback_research_shape() -> None:
    extractor = TheoryExtractor()

    research = extractor.fallback_comparative_research(
        {"items": ["A", "B"], "criteria": ["Quality"]}
    )

    assert research["success"] is True
    assert research["total_found"] == 3
    assert research["search_strategy"] == "Fallback comparative research"
    assert research["sources"][0]["source"] == "fallback"


def test_theory_extractor_extracts_theory_hints_from_sources() -> None:
    extractor = TheoryExtractor()

    theories = extractor.extract_theories_from_sources(
        {
            "success": True,
            "sources": [
                {
                    "title": "A Comparative Framework",
                    "year": 2026,
                    "source": "academic",
                },
                {
                    "title": "A Comparative Framework",
                    "year": 2026,
                    "source": "academic",
                },
                {"title": "Field notes", "abstract": "Observational data only."},
            ],
        }
    )

    assert theories == [
        {
            "name": "A Comparative Framework",
            "source": "academic",
            "year": "2026",
        }
    ]


@pytest.mark.asyncio
async def test_theory_extractor_searches_mcp_sources() -> None:
    extractor = TheoryExtractor()
    mcp_integration = AsyncMock()
    mcp_integration.search_academic_sources = AsyncMock(
        return_value={"success": True, "total_found": 2, "sources": []}
    )
    info_messages: list[str] = []
    warning_messages: list[str] = []
    error_messages: list[str] = []

    research = await extractor.search_comparative_studies(
        {"items": ["A", "B"], "criteria": ["Quality"]},
        mcp_integration,
        info_messages.append,
        warning_messages.append,
        error_messages.append,
    )

    assert research == {"success": True, "total_found": 2, "sources": []}
    assert info_messages == ["Found 2 comparative studies"]
    assert warning_messages == []
    assert error_messages == []
    mcp_integration.search_academic_sources.assert_awaited_once_with(
        query="comparative analysis A vs B Quality comparison study",
        databases=["arxiv", "pubmed"],
        max_results=10,
    )


def test_comparative_agent_initializes_theory_extractor() -> None:
    agent = ComparativeAnalysisAgent()

    assert isinstance(agent.theory_extractor, TheoryExtractor)
    assert agent._fallback_comparative_research(
        {"items": ["A", "B"], "criteria": ["Quality"]}
    )["fallback"] is True
