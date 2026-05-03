"""Characterization tests for research query planning extraction."""

from src.agents.models import AgentTask
from src.agents.supervisors.research_query_planner import ResearchQueryPlanner
from src.agents.supervisors.research_supervisor import ResearchSupervisor


def test_query_planner_builds_legacy_research_context() -> None:
    planner = ResearchQueryPlanner(
        research_depth="comprehensive",
        max_sources=25,
        citation_style="MLA",
    )

    assert planner.build_research_context() == {
        "research_depth": "comprehensive",
        "max_sources": 25,
        "citation_style": "MLA",
    }


def test_query_planner_builds_legacy_worker_tasks() -> None:
    planner = ResearchQueryPlanner(
        research_depth="comprehensive",
        max_sources=25,
        citation_style="MLA",
    )
    task = AgentTask(
        id="task-1",
        agent_type="research",
        input_data={
            "domains": ["AI", "education"],
            "research_type": "qualitative",
        },
    )

    assert planner.build_worker_tasks("How does AI affect learning?", task) == {
        "literature_review": {
            "query": "How does AI affect learning?",
            "domains": ["AI", "education"],
            "max_sources": 25,
        },
        "methodology": {
            "research_question": "How does AI affect learning?",
            "type": "qualitative",
        },
        "comparative_analysis": {
            "query": "How does AI affect learning?",
            "comparison_focus": "approaches_and_findings",
        },
        "synthesis": {"synthesis_focus": "comprehensive_integration"},
        "citation": {"style": "MLA"},
    }


def test_research_supervisor_initializes_query_planner_from_config() -> None:
    supervisor = ResearchSupervisor(
        config={
            "research_depth": "rapid",
            "max_sources": 12,
            "citation_style": "Chicago",
        }
    )

    assert supervisor.query_planner.build_research_context() == {
        "research_depth": "rapid",
        "max_sources": 12,
        "citation_style": "Chicago",
    }
