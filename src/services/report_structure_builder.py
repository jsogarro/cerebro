"""Build structured report objects from normalized workflow data."""

import logging
from typing import Any
from uuid import uuid4

from src.models.report import (
    Citation,
    Report,
    ReportMetadata,
    ReportSection,
    Visualization,
    VisualizationType,
)
from src.services.report_config import ReportQualityConfig, ReportTemplateConfig

logger = logging.getLogger(__name__)


class ReportStructureBuilder:
    """Build report sections, citations, metadata, summaries, and visualization specs."""

    def __init__(
        self,
        template_config: ReportTemplateConfig,
        quality_config: ReportQualityConfig,
    ) -> None:
        self.template_config = template_config
        self.quality_config = quality_config

    async def build_sections(self, report: Report, input_data: dict[str, Any]) -> None:
        """Build report sections based on configuration and input data."""
        report_type = report.configuration.type
        required_sections = self.template_config.get_required_sections(report_type)
        aggregated_results = input_data.get("aggregated_results", {})

        section_builders = {
            "introduction": self.build_introduction_section,
            "methodology": self.build_methodology_section,
            "literature_review": self.build_literature_section,
            "findings": self.build_findings_section,
            "analysis": self.build_analysis_section,
            "discussion": self.build_discussion_section,
            "conclusions": self.build_conclusions_section,
            "recommendations": self.build_recommendations_section,
            "limitations": self.build_limitations_section,
            "abstract": self.build_abstract_section,
            "results": self.build_results_section,
            "key_findings": self.build_key_findings_section,
            "strategic_insights": self.build_insights_section,
        }

        for section_name in required_sections:
            if section_name in section_builders:
                section = await section_builders[section_name](report, aggregated_results)
                if section:
                    report.sections.append(section)

    async def build_introduction_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build introduction section."""
        content = f"""
        This research investigates the following question: "{report.query}"

        The research spans across the following domains: {', '.join(report.domains)}.

        This report presents a comprehensive analysis of the available literature and
        synthesizes key findings to provide insights and recommendations.
        """.strip()

        return ReportSection(title="Introduction", content=content, level=1)

    async def build_methodology_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build methodology section."""
        methodologies = results.get("methodologies", {})

        content = "## Research Methodology\n\n"
        content += "This research employed a systematic approach to data collection and analysis.\n\n"

        if methodologies.get("recommended_approaches"):
            content += "### Recommended Approaches\n"
            for approach in methodologies["recommended_approaches"]:
                content += f"- {approach}\n"

        if methodologies.get("statistical_methods"):
            content += "\n### Statistical Methods\n"
            for method in methodologies["statistical_methods"]:
                content += f"- {method}\n"

        return ReportSection(title="Methodology", content=content, level=1)

    async def build_literature_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build literature review section."""
        sources = results.get("sources", [])

        content = f"## Literature Review\n\nThis review examines {len(sources)} sources.\n\n"

        for source in sources[:10]:
            title = source.get("title", "Untitled")
            year = source.get("year", "n.d.")
            content += f"- {title} ({year})\n"
            if source.get("summary"):
                content += f"  {source['summary'][:200]}...\n"

        return ReportSection(title="Literature Review", content=content, level=1)

    async def build_findings_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build findings section."""
        findings = results.get("findings", {})

        if not findings:
            return None

        section = ReportSection(
            title="Key Findings",
            content=(
                f"The research identified {sum(len(f) for f in findings.values())} "
                f"key findings across {len(findings)} categories."
            ),
            level=1,
        )

        for category, finding_list in findings.items():
            if finding_list:
                subsection_content = ""
                for finding in finding_list:
                    text = (
                        finding.get("text", "")
                        if isinstance(finding, dict)
                        else str(finding)
                    )
                    subsection_content += f"- {text}\n"
                    if isinstance(finding, dict) and finding.get("confidence"):
                        subsection_content += f"  (Confidence: {finding['confidence']:.2f})\n"

                subsection = ReportSection(
                    title=category.replace("_", " ").title(),
                    content=subsection_content,
                    level=2,
                )
                section.subsections.append(subsection)

        return section

    async def build_analysis_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build analysis section."""
        comparisons = results.get("comparisons", {})

        content = "## Analysis\n\n"
        content += "This section presents the analytical framework and key comparisons.\n\n"

        if comparisons.get("frameworks"):
            content += "### Comparison Frameworks\n"
            for framework in comparisons["frameworks"]:
                content += f"- {framework}\n"

        if comparisons.get("metrics"):
            content += "\n### Key Metrics\n"
            for metric, value in comparisons["metrics"].items():
                content += f"- {metric}: {value}\n"

        return ReportSection(title="Analysis", content=content, level=1)

    async def build_discussion_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build discussion section."""
        insights = results.get("insights", [])
        conflicts = results.get("conflict_resolutions", [])

        content = "## Discussion\n\n"
        content += "This section discusses the implications of the findings.\n\n"

        if insights:
            content += "### Key Insights\n"
            for insight in insights:
                if isinstance(insight, dict):
                    content += f"- {insight.get('text', '')}\n"
                    if insight.get("importance") == "high":
                        content += "  **High importance**\n"
                else:
                    content += f"- {insight}\n"

        if conflicts:
            content += "\n### Resolved Conflicts\n"
            content += f"{len(conflicts)} conflicts were identified and resolved.\n"

        return ReportSection(title="Discussion", content=content, level=1)

    async def build_conclusions_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build conclusions section."""
        confidence_score = results.get("confidence_score", 0.7)
        metrics = results.get("metrics", {})

        content = f"""## Conclusions

        This research synthesized findings from {metrics.get('total_sources', 0)} sources
        and {metrics.get('total_citations', 0)} citations.

        Overall confidence in findings: {confidence_score:.2%}

        The evidence suggests that the research question has been addressed with
        {self.get_confidence_level(confidence_score)} confidence.
        """.strip()

        return ReportSection(title="Conclusions", content=content, level=1)

    async def build_recommendations_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build recommendations section."""
        recommendations = results.get("recommendations", [])

        if not recommendations:
            return None

        content = "## Recommendations\n\nBased on the research findings:\n\n"

        for i, rec in enumerate(recommendations, 1):
            content += f"{i}. {rec}\n"

        return ReportSection(title="Recommendations", content=content, level=1)

    async def build_limitations_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build limitations section."""
        limitations = results.get("limitations", [])
        quality_report = results.get("quality_report", {})
        issues = quality_report.get("issues_found", [])

        content = "## Limitations\n\n"

        if limitations:
            content += "### Research Limitations\n"
            for limitation in limitations:
                content += f"- {limitation}\n"

        if issues:
            content += "\n### Quality Considerations\n"
            for issue in issues:
                if isinstance(issue, dict) and issue.get("severity") == "warning":
                    content += f"- {issue.get('message', '')}\n"

        return ReportSection(title="Limitations", content=content, level=1)

    async def build_abstract_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build abstract section for academic papers."""
        content = f"""
        This study investigates: {report.query}

        Methods: Comprehensive research approach across {len(report.domains)} domains.

        Results: Analysis of {len(results.get('sources', []))} sources yielded
        {sum(len(f) for f in results.get('findings', {}).values())} findings.

        Conclusions: {', '.join(results.get('recommendations', ['Further research recommended'])[:2])}
        """.strip()

        return ReportSection(title="Abstract", content=content, level=1)

    async def build_results_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build results section for academic papers."""
        metrics = results.get("metrics", {})

        content = f"""## Results

        ### Quantitative Findings
        - Total sources analyzed: {metrics.get('total_sources', 0)}
        - Citations reviewed: {metrics.get('total_citations', 0)}
        - Average confidence: {metrics.get('average_confidence', 0):.2%}
        - Coverage score: {metrics.get('coverage_score', 0):.2%}
        """.strip()

        return ReportSection(title="Results", content=content, level=1)

    async def build_key_findings_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build key findings section for executive summary."""
        findings = results.get("findings", {})

        top_findings = []
        for _category, finding_list in findings.items():
            for finding in finding_list[:2]:
                if isinstance(finding, dict):
                    top_findings.append(finding.get("text", ""))
                else:
                    top_findings.append(str(finding))

        content = "## Key Findings\n\n"
        for finding in top_findings:
            content += f"• {finding}\n"

        return ReportSection(title="Key Findings", content=content, level=1)

    async def build_insights_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build insights section."""
        insights = results.get("insights", [])

        high_importance = []
        for insight in insights:
            if isinstance(insight, dict) and insight.get("importance") == "high":
                high_importance.append(insight.get("text", ""))
            elif isinstance(insight, str):
                high_importance.append(insight)

        content = "## Strategic Insights\n\n"
        for insight in high_importance:
            content += f"• {insight}\n"

        return ReportSection(title="Strategic Insights", content=content, level=1)

    async def process_citations(self, report: Report, input_data: dict[str, Any]) -> None:
        """Process and format citations from input data."""
        aggregated_results = input_data.get("aggregated_results", {})
        citations_data = aggregated_results.get("citations", [])

        for citation_data in citations_data:
            if isinstance(citation_data, dict):
                citation = Citation(
                    id=citation_data.get("id", str(uuid4())),
                    authors=citation_data.get("authors", ["Unknown"]),
                    title=citation_data.get("title", "Untitled"),
                    year=citation_data.get("year"),
                    journal=citation_data.get("journal"),
                    volume=citation_data.get("volume"),
                    issue=citation_data.get("issue"),
                    pages=citation_data.get("pages"),
                    doi=citation_data.get("doi"),
                    url=citation_data.get("url"),
                    publisher=citation_data.get("publisher"),
                    location=citation_data.get("location"),
                    isbn=citation_data.get("isbn"),
                )
                report.add_citation(citation)

    async def generate_visualizations(
        self, report: Report, input_data: dict[str, Any]
    ) -> None:
        """Generate visualization specifications."""
        aggregated_results = input_data.get("aggregated_results", {})

        sources = aggregated_results.get("sources", [])
        if sources and len(sources) > 1:
            year_dist: dict[str, int] = {}
            for source in sources:
                year = source.get("year", "Unknown")
                year_dist[str(year)] = year_dist.get(str(year), 0) + 1

            if len(year_dist) > 1:
                viz = Visualization(
                    id="source_distribution",
                    type=VisualizationType.BAR_CHART,
                    title="Source Distribution by Year",
                    data={
                        "years": list(year_dist.keys()),
                        "counts": list(year_dist.values()),
                    },
                    config={"x_label": "Year", "y_label": "Number of Sources"},
                    caption=None,
                    width=None,
                    height=None,
                )
                report.add_visualization(viz)

        if len(report.domains) > 1:
            viz = Visualization(
                id="domain_distribution",
                type=VisualizationType.PIE_CHART,
                title="Research Domains Coverage",
                data={"labels": report.domains, "values": [1] * len(report.domains)},
                config={},
                caption=None,
                width=None,
                height=None,
            )
            report.add_visualization(viz)

    async def build_metadata(self, report: Report, input_data: dict[str, Any]) -> None:
        """Build report metadata from input data."""
        metadata_input = input_data.get("metadata", {})
        aggregated_results = input_data.get("aggregated_results", {})

        report.metadata = ReportMetadata(
            workflow_id=metadata_input.get("workflow_id"),
            project_id=input_data.get("project_id"),
            user_id=None,
            total_sources=len(aggregated_results.get("sources", [])),
            total_citations=len(aggregated_results.get("citations", [])),
            agents_used=metadata_input.get("agents_used", []),
            quality_score=input_data.get("quality_report", {}).get("quality_score", 0.8),
            confidence_score=aggregated_results.get("confidence_score", 0.75),
            generation_time_seconds=0.0,
            word_count=0,
            page_count=None,
        )

    async def generate_executive_summary(
        self, report: Report, input_data: dict[str, Any]
    ) -> None:
        """Generate executive summary for the report."""
        aggregated_results = input_data.get("aggregated_results", {})
        insights = aggregated_results.get("insights", [])
        recommendations = aggregated_results.get("recommendations", [])

        summary = f"""# Executive Summary

**Research Question:** {report.query}

**Approach:** {report.configuration.type.value.replace('_', ' ').title()}

**Key Outcomes:**
- Analyzed {len(aggregated_results.get('sources', []))} sources
- Identified {sum(len(f) for f in aggregated_results.get('findings', {}).values())} key findings
- Quality Score: {report.metadata.quality_score:.2%}

**Main Insights:**
"""

        for insight in insights[:3]:
            if isinstance(insight, dict):
                summary += f"- {insight.get('text', '')}\n"
            else:
                summary += f"- {insight}\n"

        if recommendations:
            summary += "\n**Key Recommendations:**\n"
            for rec in recommendations[:3]:
                summary += f"- {rec}\n"

        report.executive_summary = summary

    async def validate_report_quality(self, report: Report) -> None:
        """Validate report quality against configured thresholds."""
        report_type = report.configuration.type

        word_count = report.get_word_count()
        min_words = self.quality_config.get_min_word_count(report_type)
        if word_count < min_words:
            logger.warning(f"Report word count ({word_count}) below minimum ({min_words})")

        source_count = report.metadata.total_sources
        min_sources = self.quality_config.get_min_sources(report_type)
        if source_count < min_sources:
            logger.warning(f"Report source count ({source_count}) below minimum ({min_sources})")

        if self.quality_config.requires_citations(report_type) and not report.citations:
            logger.warning("Report missing required citations")

    def get_confidence_level(self, score: float) -> str:
        """Convert confidence score to text."""
        if score >= 0.9:
            return "high"
        if score >= 0.7:
            return "moderate"
        if score >= 0.5:
            return "low"
        return "very low"


__all__ = ["ReportStructureBuilder"]
