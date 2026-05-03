"""Characterization tests for research agent selector extraction."""

from src.agents.citation_agent import CitationAgent
from src.agents.literature_review_agent import LiteratureReviewAgent
from src.agents.supervisors.research_agent_selector import ResearchAgentSelector
from src.agents.supervisors.research_supervisor import ResearchSupervisor


def test_agent_selector_builds_legacy_worker_definition_keys() -> None:
    selector = ResearchAgentSelector()

    definitions = selector.build_worker_definitions()

    assert list(definitions) == [
        "literature_review",
        "methodology",
        "comparative_analysis",
        "synthesis",
        "citation",
    ]


def test_agent_selector_preserves_worker_definition_metadata() -> None:
    selector = ResearchAgentSelector()

    definitions = selector.build_worker_definitions()

    assert definitions["literature_review"].agent_class is LiteratureReviewAgent
    assert definitions["literature_review"].capabilities == [
        "database_search",
        "source_evaluation",
        "gap_analysis",
    ]
    assert definitions["citation"].agent_class is CitationAgent
    assert definitions["citation"].reliability_score == 0.98
    assert definitions["citation"].quality_score == 0.85


def test_research_supervisor_registers_workers_from_agent_selector() -> None:
    supervisor = ResearchSupervisor()

    assert isinstance(supervisor.agent_selector, ResearchAgentSelector)
    assert set(supervisor.worker_definitions) == {
        "literature_review",
        "methodology",
        "comparative_analysis",
        "synthesis",
        "citation",
    }
