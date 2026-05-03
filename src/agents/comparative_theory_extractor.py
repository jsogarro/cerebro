"""Theory and literature extraction helpers for comparative analysis."""

from collections.abc import Awaitable, Callable
from typing import Any

LogCallback = Callable[[str], None]


class TheoryExtractor:
    """Extracts comparative research context and theory hints from literature."""

    def build_research_query(self, input_data: dict[str, Any]) -> str:
        """Build the academic search query used for comparative studies."""
        items = input_data.get("items", [])
        criteria = input_data.get("criteria", [])

        items_text = " vs ".join(items[:3])
        criteria_text = " ".join(criteria[:3])
        return f"comparative analysis {items_text} {criteria_text} comparison study"

    async def search_comparative_studies(
        self,
        input_data: dict[str, Any],
        mcp_integration: Any,
        log_info: LogCallback,
        log_warning: LogCallback,
        log_error: LogCallback,
    ) -> dict[str, Any]:
        """Search for comparative studies using the configured MCP integration."""
        if not mcp_integration:
            log_warning("MCP integration not available for research search")
            return self.fallback_comparative_research(input_data)

        query = self.build_research_query(input_data)

        try:
            search_sources: Callable[..., Awaitable[dict[str, Any]]] = (
                mcp_integration.search_academic_sources
            )
            result = await search_sources(
                query=query, databases=["arxiv", "pubmed"], max_results=10
            )

            if result.get("success"):
                log_info(f"Found {result.get('total_found', 0)} comparative studies")
                return dict(result)

            raise Exception(f"Comparative research search failed: {result.get('error')}")

        except Exception as exc:
            log_error(f"MCP comparative research search failed: {exc}")
            return self.fallback_comparative_research(input_data)

    def extract_theories_from_sources(
        self,
        research_data: dict[str, Any],
    ) -> list[dict[str, str]]:
        """Extract lightweight theory/framework hints from source metadata."""
        if not research_data.get("success"):
            return []

        theory_keywords = ("theory", "framework", "model", "approach", "method")
        theories: list[dict[str, str]] = []
        seen: set[str] = set()

        for source in research_data.get("sources", []):
            title = str(source.get("title", ""))
            abstract = str(source.get("abstract", ""))
            haystack = f"{title} {abstract}".lower()
            if not any(keyword in haystack for keyword in theory_keywords):
                continue

            theory_name = title or "Untitled comparative theory"
            if theory_name in seen:
                continue

            seen.add(theory_name)
            theories.append(
                {
                    "name": theory_name,
                    "source": str(source.get("source", "literature")),
                    "year": str(source.get("year", "n.d.")),
                }
            )

        return theories

    def fallback_comparative_research(
        self,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Create fallback comparative research when MCP tools are unavailable."""
        items = input_data.get("items", [])
        criteria = input_data.get("criteria", [])

        mock_sources = []
        for index in range(3):
            mock_sources.append(
                {
                    "title": (
                        f"Comparative Study {index + 1}: "
                        f"{' vs '.join(items[:2])} Analysis"
                    ),
                    "authors": [f"Researcher {index + 1}"],
                    "year": 2024 - index,
                    "journal": "Journal of Comparative Analysis",
                    "abstract": (
                        f"This study compares {items[0]} and "
                        f"{items[1] if len(items) > 1 else 'alternatives'} across "
                        f"{criteria[0] if criteria else 'multiple criteria'}..."
                    ),
                    "source": "fallback",
                }
            )

        return {
            "success": True,
            "sources": mock_sources,
            "total_found": len(mock_sources),
            "search_strategy": "Fallback comparative research",
            "fallback": True,
        }
