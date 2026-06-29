"""Characterization tests for comparative similarity analysis."""

from src.agents.comparative_analysis_agent import ComparativeAnalysisAgent
from src.agents.comparative_similarity_analyzer import SimilarityAnalyzer


def test_similarity_analyzer_preserves_legacy_confidence_scoring() -> None:
    analyzer = SimilarityAnalyzer()

    high_confidence = analyzer.calculate_confidence(
        items_count=4,
        criteria_count=4,
        matrix_completeness=1.0,
        trade_offs=["T1", "T2", "T3"],
        recommendations=["R1", "R2", "R3"],
    )
    low_confidence = analyzer.calculate_confidence(
        items_count=2,
        criteria_count=1,
        matrix_completeness=1.0,
        trade_offs=[],
        recommendations=[],
    )

    assert high_confidence == 1.0
    assert low_confidence == 0.67


def test_similarity_analyzer_preserves_mcp_confidence_bonus() -> None:
    analyzer = SimilarityAnalyzer()

    confidence = analyzer.calculate_confidence_with_mcp(
        items_count=2,
        criteria_count=1,
        matrix_completeness=1.0,
        trade_offs=[],
        recommendations=[],
        mcp_data_quality={
            "research_quality": True,
            "statistical_quality": True,
            "graph_quality": False,
        },
    )

    assert confidence == 0.77


def test_similarity_analyzer_calculates_text_similarity() -> None:
    analyzer = SimilarityAnalyzer()

    similarity = analyzer.calculate_text_similarity(
        "Performance cost tradeoff",
        "Performance quality tradeoff",
    )

    assert similarity == 0.5
    assert analyzer.calculate_text_similarity("", "") == 1.0


def test_similarity_analyzer_calculates_matrix_similarity() -> None:
    analyzer = SimilarityAnalyzer()

    similarity = analyzer.calculate_matrix_similarity(
        {
            "A": {"quality": 1.0, "cost": 0.0},
            "B": {"quality": 0.5, "cost": 0.5},
            "C": {"quality": 0.0, "cost": 1.0},
        }
    )

    assert similarity["method"] == "score_vector_overlap"
    assert similarity["average_similarity"] == 1 / 3
    assert similarity["pairwise"][0] == {
        "items": ["A", "B"],
        "similarity": 0.5,
        "shared_criteria": 2,
    }


def test_comparative_agent_initializes_similarity_analyzer() -> None:
    agent = ComparativeAnalysisAgent()

    assert isinstance(agent.similarity_analyzer, SimilarityAnalyzer)
    assert agent._calculate_text_similarity("A B", "B C") == 1 / 3
