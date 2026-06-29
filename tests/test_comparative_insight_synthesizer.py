"""Characterization tests for comparative insight synthesis extraction."""

from unittest.mock import AsyncMock

import pytest

from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent
from src.agents.comparative_insight_synthesizer import ComparativeInsightSynthesizer


def test_insight_synthesizer_preserves_trade_off_analysis_shape() -> None:
    synthesizer = ComparativeInsightSynthesizer()

    analysis = synthesizer.analyze_trade_offs(
        [
            "Performance vs cost trade-off",
            "Complexity increases implementation time",
            "Quality varies by deployment context",
        ],
        {
            "A": {"performance": 1.0, "cost": 0.0},
            "B": {"performance": 0.0, "cost": 1.0},
        },
    )

    assert analysis == {
        "total_trade_offs": 3,
        "trade_off_categories": {
            "performance": ["Performance vs cost trade-off"],
            "complexity": ["Complexity increases implementation time"],
            "quality": ["Quality varies by deployment context"],
        },
        "severity": "high",
    }


def test_insight_synthesizer_preserves_relationship_enrichment() -> None:
    synthesizer = ComparativeInsightSynthesizer()

    analysis = synthesizer.analyze_trade_offs_with_relationships(
        ["Performance favors Tool A", "Cost favors Tool B"],
        {"Tool A": {"Performance": 1.0}, "Tool B": {"Performance": 0.0}},
        {
            "success": True,
            "entities": [{"text": "Tool A"}, {"text": "Cost"}],
            "relationships": [{"type": "outperforms"}, {"type": "costs_less"}],
        },
    )

    assert analysis["relationship_insights"] == {
        "entities_identified": 2,
        "relationships_found": 2,
        "relationship_types": ["costs_less", "outperforms"],
    }
    assert analysis["entity_coverage"] == 1.0


def test_insight_synthesizer_preserves_research_summary_and_fallbacks() -> None:
    synthesizer = ComparativeInsightSynthesizer()

    summary = synthesizer.summarize_research_findings(
        {
            "success": True,
            "sources": [
                {
                    "title": "A Comparative Framework",
                    "year": 2026,
                    "abstract": "x" * 151,
                }
            ],
        }
    )
    fallback_research = synthesizer.fallback_comparative_research(
        {"items": ["A", "B"], "criteria": ["Quality"]}
    )
    fallback_stats = synthesizer.fallback_statistical_analysis({"items": ["A", "B"]})

    assert summary.startswith("1. A Comparative Framework (2026): ")
    assert summary.endswith("...")
    assert fallback_research["success"] is True
    assert fallback_research["sources"][0]["source"] == "fallback"
    assert fallback_stats["descriptive_stats"]["count"] == 2


@pytest.mark.asyncio
async def test_insight_synthesizer_formats_methodology_citations() -> None:
    synthesizer = ComparativeInsightSynthesizer()
    mcp_integration = AsyncMock()
    mcp_integration.format_citations = AsyncMock(
        return_value={
            "success": True,
            "formatted_citations": ["Example, A. (2026). Comparative methods."],
        }
    )
    info_messages: list[str] = []
    error_messages: list[str] = []

    citations = await synthesizer.cite_comparison_methodologies(
        {
            "success": True,
            "sources": [
                {
                    "title": "Comparative methods",
                    "authors": ["A. Example"],
                    "year": 2026,
                    "abstract": "A comparison methodology.",
                },
                {
                    "title": "Unrelated study",
                    "abstract": "Not relevant.",
                },
            ],
        },
        mcp_integration,
        info_messages.append,
        error_messages.append,
    )

    assert citations == {
        "success": True,
        "citations": ["Example, A. (2026). Comparative methods."],
        "methodology_count": 1,
    }
    assert info_messages == ["Generated 1 methodology citations"]
    assert error_messages == []
    mcp_integration.format_citations.assert_awaited_once()


def test_comparative_agent_initializes_insight_synthesizer() -> None:
    agent = ComparativeAnalysisAgent()

    assert isinstance(agent.insight_synthesizer, ComparativeInsightSynthesizer)
    assert agent._categorize_trade_offs(["Cost vs quality"]) == {
        "cost": ["Cost vs quality"]
    }
