"""Query planning helpers for the research supervisor."""

from typing import Any

from ..models import AgentTask


class ResearchQueryPlanner:
    """Builds research context and worker task payloads."""

    def __init__(
        self,
        research_depth: str,
        max_sources: int,
        citation_style: str,
    ) -> None:
        self.research_depth = research_depth
        self.max_sources = max_sources
        self.citation_style = citation_style

    def build_research_context(self) -> dict[str, Any]:
        """Build research-specific context for supervision state."""
        return {
            "research_depth": self.research_depth,
            "max_sources": self.max_sources,
            "citation_style": self.citation_style,
        }

    def build_worker_tasks(
        self,
        research_query: str,
        task: AgentTask,
    ) -> dict[str, dict[str, Any]]:
        """Build worker-specific task payloads for the research workflow."""
        return {
            "literature_review": {
                "query": research_query,
                "domains": task.input_data.get("domains", []),
                "max_sources": self.max_sources,
            },
            "methodology": {
                "research_question": research_query,
                "type": task.input_data.get("research_type", "mixed"),
            },
            "comparative_analysis": {
                "query": research_query,
                "comparison_focus": "approaches_and_findings",
            },
            "synthesis": {"synthesis_focus": "comprehensive_integration"},
            "citation": {"style": self.citation_style},
        }
