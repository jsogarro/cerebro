"""Insight and trade-off synthesis helpers for comparative analysis."""

from collections.abc import Awaitable, Callable
from typing import Any

LogCallback = Callable[[str], None]


class ComparativeInsightSynthesizer:
    """Synthesizes trade-off analysis, research summaries, and fallback insights."""

    def analyze_trade_offs(
        self,
        trade_offs: list[str],
        matrix: dict[str, dict[str, float]],
    ) -> dict[str, Any]:
        """Analyze trade-offs between compared items."""
        return {
            "total_trade_offs": len(trade_offs),
            "trade_off_categories": self.categorize_trade_offs(trade_offs),
            "severity": self.assess_trade_off_severity(trade_offs, matrix),
        }

    def categorize_trade_offs(self, trade_offs: list[str]) -> dict[str, list[str]]:
        """Categorize trade-offs by type."""
        categories: dict[str, list[str]] = {
            "performance": [],
            "cost": [],
            "complexity": [],
            "time": [],
            "quality": [],
            "other": [],
        }

        for trade_off in trade_offs:
            trade_off_lower = trade_off.lower()
            categorized = False

            for category in ["performance", "cost", "complexity", "time", "quality"]:
                if category in trade_off_lower:
                    categories[category].append(trade_off)
                    categorized = True
                    break

            if not categorized:
                categories["other"].append(trade_off)

        return {key: value for key, value in categories.items() if value}

    def assess_trade_off_severity(
        self,
        trade_offs: list[str],
        matrix: dict[str, dict[str, float]],
    ) -> str:
        """Assess the severity of trade-offs."""
        if not trade_offs:
            return "none"

        if matrix:
            all_scores: list[float] = []
            for scores in matrix.values():
                all_scores.extend(scores.values())

            if all_scores:
                variance = sum((score - 0.5) ** 2 for score in all_scores) / len(
                    all_scores
                )

                if variance > 0.2:
                    return "high"
                if variance > 0.1:
                    return "moderate"
                return "low"

        if len(trade_offs) > 5:
            return "high"
        if len(trade_offs) > 2:
            return "moderate"
        return "low"

    def analyze_trade_offs_with_relationships(
        self,
        trade_offs: list[str],
        matrix: dict[str, dict[str, float]],
        relationship_graph: dict[str, Any],
    ) -> dict[str, Any]:
        """Analyze trade-offs enhanced with relationship insights."""
        analysis = self.analyze_trade_offs(trade_offs, matrix)

        if relationship_graph.get("success"):
            entities = relationship_graph.get("entities", [])
            relationships = relationship_graph.get("relationships", [])

            analysis["relationship_insights"] = {
                "entities_identified": len(entities),
                "relationships_found": len(relationships),
                "relationship_types": sorted(
                    {relationship.get("type", "unknown") for relationship in relationships}
                ),
            }

            entity_texts = [entity.get("text", "").lower() for entity in entities]
            trade_off_coverage = 0
            for trade_off in trade_offs:
                trade_off_lower = trade_off.lower()
                for entity_text in entity_texts:
                    if entity_text in trade_off_lower:
                        trade_off_coverage += 1
                        break

            analysis["entity_coverage"] = (
                trade_off_coverage / len(trade_offs) if trade_offs else 0
            )

        return analysis

    async def cite_comparison_methodologies(
        self,
        research_data: dict[str, Any],
        mcp_integration: Any,
        log_info: LogCallback,
        log_error: LogCallback,
    ) -> dict[str, Any]:
        """Generate citations for comparison methodologies using an MCP service."""
        if not mcp_integration or not research_data.get("success"):
            return {"success": False, "citations": []}

        sources = research_data.get("sources", [])
        if not sources:
            return {"success": True, "citations": []}

        methodology_sources = []
        methodology_keywords = [
            "comparison",
            "comparative",
            "methodology",
            "framework",
            "approach",
        ]
        for source in sources[:5]:
            title = source.get("title", "").lower()
            abstract = source.get("abstract", "").lower()

            if any(
                keyword in title or keyword in abstract
                for keyword in methodology_keywords
            ):
                methodology_sources.append(source)

        if not methodology_sources:
            return {
                "success": True,
                "citations": [],
                "note": "No methodology-specific sources found",
            }

        citation_sources = [
            {
                "title": source.get("title", "Unknown Title"),
                "authors": source.get("authors", ["Unknown Author"]),
                "year": source.get("year", "n.d."),
                "journal": source.get("journal", ""),
                "doi": source.get("doi", ""),
            }
            for source in methodology_sources
        ]

        try:
            format_citations: Callable[..., Awaitable[dict[str, Any]]] = (
                mcp_integration.format_citations
            )
            result = await format_citations(sources=citation_sources, style="APA")

            if result.get("success"):
                log_info(f"Generated {len(citation_sources)} methodology citations")
                return {
                    "success": True,
                    "citations": result.get("formatted_citations", []),
                    "methodology_count": len(citation_sources),
                }

            raise Exception(f"Citation formatting failed: {result.get('error')}")

        except Exception as exc:
            log_error(f"Methodology citation generation failed: {exc}")
            return {"success": False, "error": str(exc)}

    def summarize_research_findings(self, research_data: dict[str, Any]) -> str:
        """Summarize research findings for Gemini analysis."""
        if not research_data.get("success"):
            return "No research data available."

        sources = research_data.get("sources", [])
        if not sources:
            return "No research sources found."

        summary_parts = []
        for index, source in enumerate(sources[:3], 1):
            title = source.get("title", "Unknown Title")
            year = source.get("year", "n.d.")
            abstract = source.get("abstract", "")

            source_summary = f"{index}. {title} ({year})"
            if abstract:
                abstract_snippet = (
                    abstract[:150] + "..." if len(abstract) > 150 else abstract
                )
                source_summary += f": {abstract_snippet}"

            summary_parts.append(source_summary)

        return "\n".join(summary_parts)

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

    def fallback_statistical_analysis(
        self,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Create fallback statistical analysis when MCP tools are unavailable."""
        return {
            "success": True,
            "tests_performed": ["basic_comparison"],
            "descriptive_stats": {
                "mean": 0.5,
                "std_dev": 0.2,
                "count": len(input_data.get("items", [])),
            },
            "data_quality": "limited",
            "fallback": True,
        }

    def generate_mock_analysis_with_mcp(
        self,
        base_analysis: dict[str, Any],
        research_data: dict[str, Any],
        statistical_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Enhance mock comparative analysis with available MCP metadata."""
        analysis = base_analysis.copy()

        if research_data.get("success"):
            analysis["research_informed"] = True
            analysis["research_sources"] = research_data.get("total_found", 0)

        if statistical_data.get("success"):
            analysis["statistically_enhanced"] = True
            analysis["statistical_tests"] = statistical_data.get("tests_performed", [])

        analysis["mcp_enhanced"] = True

        return analysis
