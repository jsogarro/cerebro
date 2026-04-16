"""
Report generation node for creating final research reports.

This node generates comprehensive research reports in various formats
based on the aggregated and quality-checked results, now integrated
with the advanced report generation system.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.models.report import (
    ReportConfiguration,
    ReportFormat,
    ReportGenerationRequest,
    ReportType,
    CitationStyle,
)
from src.orchestration.state import ResearchState, WorkflowPhase
from src.services.report_generator import ReportGenerator
from src.services.report_config import create_report_settings
from src.utils.serialization import serialize_to_str, serialize

logger = logging.getLogger(__name__)


async def report_generation_node(state: ResearchState) -> ResearchState:
    """
    Generate the final research report using the advanced report generation system.

    This node:
    1. Structures research findings into a coherent report
    2. Formats citations properly
    3. Creates executive summary
    4. Generates visualizations if needed
    5. Produces output in multiple requested formats
    6. Stores report with proper metadata

    Args:
        state: Current workflow state

    Returns:
        Updated state with generated report
    """
    logger.info("Generating research report using advanced report system")

    try:
        # Determine report configuration from state
        report_config = _build_report_configuration(state)
        
        # Determine output formats
        requested_formats = _determine_output_formats(state)
        
        # Prepare workflow data for report generation
        workflow_data = _prepare_workflow_data(state)
        
        # Create report generation request
        generation_request = ReportGenerationRequest(
            workflow_data=workflow_data,
            configuration=report_config,
            formats=requested_formats,
            save_to_storage=state.context.get("save_report", True),
            notify_completion=False
        )
        
        # Initialize report generator
        settings = create_report_settings()
        generator = ReportGenerator(settings)
        
        # Generate the report
        logger.info(f"Generating report with formats: {[f.value for f in requested_formats]}")
        response = await generator.generate_report(generation_request)
        
        if response.status == "completed":
            # Store successful generation results in state
            state.context["final_report_response"] = {
                "report_id": response.report_id,
                "status": response.status,
                "formats_generated": response.formats_generated,
                "generation_time": response.generation_time,
                "word_count": response.word_count,
                "page_count": response.page_count,
                "download_urls": response.download_urls,
            }
            
            # For backward compatibility, also store legacy format
            legacy_report = _convert_to_legacy_format(workflow_data, response)
            state.context["final_report"] = legacy_report
            
            # Mark workflow as complete if quality passed
            quality_report = state.context.get("quality_report", {})
            if quality_report.get("passed", True):  # Default to True if no quality report
                state.transition_to_phase(WorkflowPhase.COMPLETED)
                logger.info(f"Report generation complete. Generated {len(response.formats_generated)} formats in {response.generation_time:.2f}s")
            else:
                logger.warning("Report generated but quality checks did not pass")
        
        else:
            # Handle generation failure
            error_msg = f"Report generation failed: {', '.join(response.errors)}"
            logger.error(error_msg)
            state.validation_errors.append(error_msg)
            state.error_count += 1
            
            # Store error information
            state.context["final_report_response"] = {
                "status": response.status,
                "errors": response.errors,
                "generation_time": response.generation_time,
            }

    except Exception as e:
        logger.error(f"Error in advanced report generation: {e}")
        state.validation_errors.append(f"Advanced report generation failed: {e!s}")
        state.error_count += 1
        
        # Fallback to legacy report generation
        logger.info("Falling back to legacy report generation")
        try:
            state = await _fallback_legacy_generation(state)
        except Exception as fallback_error:
            logger.error(f"Fallback report generation also failed: {fallback_error}")
            state.validation_errors.append(f"All report generation methods failed: {fallback_error!s}")

    return state


def _build_report_configuration(state: ResearchState) -> ReportConfiguration:
    """Build report configuration from workflow state."""
    # Extract configuration parameters from state context
    context = state.context
    
    # Determine report type based on query complexity and domains
    report_type = _determine_report_type(state)
    
    # Determine citation style
    citation_style = CitationStyle(context.get("citation_style", "APA"))
    
    # Build configuration
    config = ReportConfiguration(
        format=ReportFormat.HTML,  # Primary format, others will be added to formats list
        type=report_type,
        citation_style=citation_style,
        include_toc=context.get("include_toc", True),
        include_executive_summary=context.get("include_executive_summary", True),
        include_visualizations=context.get("include_visualizations", True),
        include_citations=context.get("include_citations", True),
        include_methodology=context.get("include_methodology", True),
        author_name=context.get("author_name"),
        institution=context.get("institution"),
        language=context.get("language", "en"),
    )
    
    return config


def _determine_report_type(state: ResearchState) -> ReportType:
    """Determine appropriate report type based on state."""
    context = state.context
    
    # Check if specific type is requested
    requested_type = context.get("report_type")
    if requested_type:
        try:
            return ReportType(requested_type)
        except ValueError:
            logger.warning(f"Invalid report type requested: {requested_type}")
    
    # Determine based on query complexity and agent results
    domain_count = len(state.domains) if state.domains else 0
    source_count = len(context.get("aggregated_results", {}).get("sources", []))
    
    if domain_count >= 3 and source_count >= 20:
        return ReportType.COMPREHENSIVE
    elif source_count >= 10:
        return ReportType.ACADEMIC_PAPER
    elif context.get("executive_summary_only"):
        return ReportType.EXECUTIVE_SUMMARY
    else:
        return ReportType.COMPREHENSIVE  # Default


def _determine_output_formats(state: ResearchState) -> List[ReportFormat]:
    """Determine which output formats to generate."""
    context = state.context
    requested_formats = context.get("output_formats", ["html", "markdown"])
    
    formats = []
    for format_name in requested_formats:
        try:
            format_enum = ReportFormat(format_name.lower())
            formats.append(format_enum)
        except ValueError:
            logger.warning(f"Unsupported output format: {format_name}")
    
    # Ensure at least HTML is included
    if not formats:
        formats = [ReportFormat.HTML]
    elif ReportFormat.HTML not in formats:
        formats.insert(0, ReportFormat.HTML)
    
    return formats


def _prepare_workflow_data(state: ResearchState) -> Dict[str, Any]:
    """Prepare workflow data for the report generator."""
    aggregated_results = state.context.get("aggregated_results", {})
    quality_report = state.context.get("quality_report", {})
    
    # Build comprehensive workflow data
    workflow_data = {
        "title": _generate_report_title(state),
        "query": state.query,
        "domains": state.domains or [],
        "project_id": str(state.project_id) if state.project_id else None,
        "aggregated_results": aggregated_results,
        "quality_report": quality_report,
        "metadata": {
            "workflow_id": state.workflow_id,
            "agents_used": list(state.completed_agents),
            "total_sources": len(aggregated_results.get("sources", [])),
            "total_citations": len(aggregated_results.get("citations", [])),
            "generation_timestamp": datetime.utcnow().isoformat(),
        }
    }
    
    return workflow_data


def _generate_report_title(state: ResearchState) -> str:
    """Generate an appropriate title for the report."""
    context = state.context
    
    # Check if title is provided in context
    if context.get("report_title"):
        return context["report_title"]
    
    # Generate title based on query and domains
    query = state.query
    domains = state.domains or []
    
    if domains:
        domain_str = " and ".join(domains[:2])  # Use first two domains
        if len(domains) > 2:
            domain_str += " (Multi-Domain)"
        return f"Research Analysis: {query} - {domain_str} Perspective"
    else:
        return f"Research Report: {query}"


def _convert_to_legacy_format(workflow_data: Dict[str, Any], response) -> Dict[str, Any]:
    """Convert new report response to legacy format for backward compatibility."""
    return {
        "title": workflow_data["title"],
        "query": workflow_data["query"],
        "domains": workflow_data["domains"],
        "metadata": {
            "generated_at": datetime.utcnow().isoformat(),
            "report_id": response.report_id,
            "formats_generated": response.formats_generated,
            "generation_time": response.generation_time,
            "word_count": response.word_count,
            "page_count": response.page_count,
        },
        "status": "completed",
    }


async def _fallback_legacy_generation(state: ResearchState) -> ResearchState:
    """Fallback to legacy report generation if advanced system fails."""
    logger.info("Using legacy report generation as fallback")
    
    # Get aggregated results and quality report
    aggregated_results = state.context.get("aggregated_results", {})
    quality_report = state.context.get("quality_report", {})

    # Generate simple report structure using legacy methods
    report = create_report_structure(
        state=state,
        aggregated_results=aggregated_results,
        quality_report=quality_report,
        report_format="comprehensive",
    )

    # Generate executive summary
    executive_summary = generate_executive_summary(report, state)
    report["executive_summary"] = executive_summary

    # Add metadata
    report["metadata"] = {
        "generated_at": datetime.utcnow().isoformat(),
        "workflow_id": state.workflow_id,
        "project_id": state.project_id,
        "quality_score": state.quality_score,
        "total_sources": len(aggregated_results.get("sources", [])),
        "total_citations": len(aggregated_results.get("citations", [])),
        "agents_used": list(state.completed_agents),
        "generation_method": "legacy_fallback",
    }

    # Generate basic outputs
    outputs = {
        "json": serialize_to_str(report, indent=2, default=str),
        "markdown": generate_markdown_report(report),
    }

    # Store in state
    state.context["final_report"] = report
    state.context["report_outputs"] = outputs

    # Mark as complete
    if quality_report.get("passed", True):
        state.transition_to_phase(WorkflowPhase.COMPLETED)
        logger.info("Legacy report generation complete")

    return state


def create_report_structure(
    state: ResearchState,
    aggregated_results: dict[str, Any],
    quality_report: dict[str, Any],
    report_format: str,
) -> dict[str, Any]:
    """
    Create the basic report structure.

    Args:
        state: Current workflow state
        aggregated_results: Aggregated research results
        quality_report: Quality assessment report
        report_format: Type of report to generate

    Returns:
        Report structure
    """
    report = {
        "title": f"Research Report: {state.query}",
        "query": state.query,
        "domains": state.domains,
        "research_approach": state.research_plan.get(
            "research_approach", "comprehensive"
        ),
        "sections": [],
    }

    # Add sections based on report format
    if report_format == "comprehensive":
        report["sections"] = [
            create_introduction_section(state, aggregated_results),
            create_methodology_section(state, aggregated_results),
            create_literature_review_section(aggregated_results),
            create_findings_section(aggregated_results),
            create_analysis_section(aggregated_results),
            create_discussion_section(aggregated_results),
            create_conclusions_section(aggregated_results),
            create_recommendations_section(aggregated_results),
            create_limitations_section(aggregated_results, quality_report),
        ]

    elif report_format == "executive_summary":
        report["sections"] = [
            create_key_findings_section(aggregated_results),
            create_insights_section(aggregated_results),
            create_recommendations_section(aggregated_results),
        ]

    elif report_format == "academic":
        report["sections"] = [
            create_abstract_section(state, aggregated_results),
            create_introduction_section(state, aggregated_results),
            create_literature_review_section(aggregated_results),
            create_methodology_section(state, aggregated_results),
            create_results_section(aggregated_results),
            create_discussion_section(aggregated_results),
            create_conclusions_section(aggregated_results),
        ]

    else:  # default/simple format
        report["sections"] = [
            create_summary_section(state, aggregated_results),
            create_findings_section(aggregated_results),
            create_conclusions_section(aggregated_results),
        ]

    # Filter out empty sections
    report["sections"] = [s for s in report["sections"] if s is not None]

    return report


def create_introduction_section(
    state: ResearchState, results: dict[str, Any]
) -> dict[str, Any]:
    """Create introduction section."""
    query_analysis = state.context.get("query_analysis", {})

    return {
        "title": "Introduction",
        "content": f"""
This research investigates the following question: "{state.query}"

The research spans across the following domains: {', '.join(state.domains)}.

Key concepts identified: {', '.join(query_analysis.get('key_concepts', []))}

Research scope: {query_analysis.get('scope', 'moderate')}
Query type: {query_analysis.get('query_type', 'exploratory')}
        """.strip(),
        "subsections": [],
    }


def create_methodology_section(
    state: ResearchState, results: dict[str, Any]
) -> dict[str, Any]:
    """Create methodology section."""
    methodologies = results.get("methodologies", {})

    content = "## Research Methodology\n\n"

    if methodologies.get("recommended_approaches"):
        content += "### Recommended Approaches\n"
        for approach in methodologies["recommended_approaches"]:
            content += f"- {approach}\n"

    if methodologies.get("statistical_methods"):
        content += "\n### Statistical Methods\n"
        for method in methodologies["statistical_methods"]:
            content += f"- {method}\n"

    return {"title": "Methodology", "content": content, "subsections": []}


def create_literature_review_section(results: dict[str, Any]) -> dict[str, Any]:
    """Create literature review section."""
    sources = results.get("sources", [])

    content = (
        f"## Literature Review\n\nThis review examines {len(sources)} sources.\n\n"
    )

    # Group sources by year or domain
    for source in sources[:10]:  # Limit to top 10 for brevity
        content += (
            f"- {source.get('title', 'Untitled')} ({source.get('year', 'n.d.')})\n"
        )
        if source.get("summary"):
            content += f"  {source['summary'][:200]}...\n"

    return {"title": "Literature Review", "content": content, "subsections": []}


def create_findings_section(results: dict[str, Any]) -> dict[str, Any]:
    """Create findings section."""
    findings = results.get("findings", {})

    subsections = []

    for category, finding_list in findings.items():
        if finding_list:
            subsection_content = ""
            for finding in finding_list:
                subsection_content += f"- {finding.get('text', '')}\n"
                if finding.get("confidence"):
                    subsection_content += (
                        f"  (Confidence: {finding['confidence']:.2f})\n"
                    )

            subsections.append(
                {
                    "title": category.replace("_", " ").title(),
                    "content": subsection_content,
                }
            )

    return {
        "title": "Key Findings",
        "content": f"The research identified {sum(len(f) for f in findings.values())} key findings across {len(findings)} categories.",
        "subsections": subsections,
    }


def create_analysis_section(results: dict[str, Any]) -> dict[str, Any]:
    """Create analysis section."""
    comparisons = results.get("comparisons", {})

    content = "## Analysis\n\n"

    if comparisons.get("frameworks"):
        content += "### Comparison Frameworks\n"
        for framework in comparisons["frameworks"]:
            content += f"- {framework}\n"

    if comparisons.get("metrics"):
        content += "\n### Key Metrics\n"
        for metric, value in comparisons["metrics"].items():
            content += f"- {metric}: {value}\n"

    return {"title": "Analysis", "content": content, "subsections": []}


def create_discussion_section(results: dict[str, Any]) -> dict[str, Any]:
    """Create discussion section."""
    insights = results.get("insights", [])
    conflicts = results.get("conflict_resolutions", [])

    content = "## Discussion\n\n"

    if insights:
        content += "### Key Insights\n"
        for insight in insights:
            content += f"- {insight.get('text', '')}\n"
            if insight.get("importance") == "high":
                content += "  **High importance**\n"

    if conflicts:
        content += "\n### Resolved Conflicts\n"
        content += f"{len(conflicts)} conflicts were identified and resolved using confidence-weighted consensus.\n"

    return {"title": "Discussion", "content": content, "subsections": []}


def create_conclusions_section(results: dict[str, Any]) -> dict[str, Any]:
    """Create conclusions section."""
    confidence_score = results.get("confidence_score", 0)
    metrics = results.get("metrics", {})

    content = f"""## Conclusions

This research synthesized findings from {metrics.get('total_sources', 0)} sources and {metrics.get('total_citations', 0)} citations.

Overall confidence in findings: {confidence_score:.2%}

The evidence suggests that the research question has been addressed with {get_confidence_level(confidence_score)} confidence.
    """.strip()

    return {"title": "Conclusions", "content": content, "subsections": []}


def create_recommendations_section(results: dict[str, Any]) -> dict[str, Any]:
    """Create recommendations section."""
    recommendations = results.get("recommendations", [])

    if not recommendations:
        return None

    content = "## Recommendations\n\nBased on the research findings:\n\n"

    for i, rec in enumerate(recommendations, 1):
        content += f"{i}. {rec}\n"

    return {"title": "Recommendations", "content": content, "subsections": []}


def create_limitations_section(
    results: dict[str, Any], quality_report: dict[str, Any]
) -> dict[str, Any]:
    """Create limitations section."""
    limitations = results.get("limitations", [])
    issues = quality_report.get("issues_found", [])

    content = "## Limitations\n\n"

    if limitations:
        content += "### Research Limitations\n"
        for limitation in limitations:
            content += f"- {limitation}\n"

    if issues:
        content += "\n### Quality Considerations\n"
        for issue in issues:
            if issue.get("severity") == "warning":
                content += f"- {issue.get('message', '')}\n"

    return {"title": "Limitations", "content": content, "subsections": []}


def create_key_findings_section(results: dict[str, Any]) -> dict[str, Any]:
    """Create key findings section for executive summary."""
    findings = results.get("findings", {})

    # Get top findings from each category
    top_findings = []
    for category, finding_list in findings.items():
        for finding in finding_list[:2]:  # Top 2 from each category
            top_findings.append(finding.get("text", ""))

    content = "## Key Findings\n\n"
    for finding in top_findings:
        content += f"• {finding}\n"

    return {"title": "Key Findings", "content": content, "subsections": []}


def create_insights_section(results: dict[str, Any]) -> dict[str, Any]:
    """Create insights section."""
    insights = results.get("insights", [])

    # Filter high-importance insights
    high_importance = [i for i in insights if i.get("importance") == "high"]

    content = "## Strategic Insights\n\n"
    for insight in high_importance:
        content += f"• {insight.get('text', '')}\n"

    return {"title": "Strategic Insights", "content": content, "subsections": []}


def create_summary_section(
    state: ResearchState, results: dict[str, Any]
) -> dict[str, Any]:
    """Create summary section."""
    return {
        "title": "Summary",
        "content": f"""
Research Question: {state.query}

This research examined {len(results.get('sources', []))} sources and identified {sum(len(f) for f in results.get('findings', {}).values())} key findings.

Confidence Score: {results.get('confidence_score', 0):.2%}
        """.strip(),
        "subsections": [],
    }


def create_abstract_section(
    state: ResearchState, results: dict[str, Any]
) -> dict[str, Any]:
    """Create abstract section for academic report."""
    return {
        "title": "Abstract",
        "content": f"""
This study investigates: {state.query}

Methods: {state.research_plan.get('research_approach', 'comprehensive')} research approach across {len(state.domains)} domains.

Results: Analysis of {len(results.get('sources', []))} sources yielded {sum(len(f) for f in results.get('findings', {}).values())} findings.

Conclusions: {', '.join(results.get('recommendations', ['Further research recommended'])[:2])}
        """.strip(),
        "subsections": [],
    }


def create_results_section(results: dict[str, Any]) -> dict[str, Any]:
    """Create results section for academic report."""
    metrics = results.get("metrics", {})

    content = f"""## Results

### Quantitative Findings
- Total sources analyzed: {metrics.get('total_sources', 0)}
- Citations reviewed: {metrics.get('total_citations', 0)}
- Average confidence: {metrics.get('average_confidence', 0):.2%}
- Coverage score: {metrics.get('coverage_score', 0):.2%}
    """.strip()

    return {"title": "Results", "content": content, "subsections": []}


def generate_executive_summary(report: dict[str, Any], state: ResearchState) -> str:
    """
    Generate executive summary of the report.

    Args:
        report: Report structure
        state: Current workflow state

    Returns:
        Executive summary text
    """
    summary = f"""# Executive Summary

**Research Question:** {state.query}

**Approach:** {state.research_plan.get('research_approach', 'comprehensive').replace('_', ' ').title()}

**Key Outcomes:**
- Analyzed {len(state.context.get('aggregated_results', {}).get('sources', []))} sources
- Identified {sum(len(f) for f in state.context.get('aggregated_results', {}).get('findings', {}).values())} key findings
- Quality Score: {state.quality_score:.2%}

**Main Insights:**
"""

    insights = state.context.get("aggregated_results", {}).get("insights", [])
    for insight in insights[:3]:  # Top 3 insights
        summary += f"- {insight.get('text', '')}\n"

    recommendations = state.context.get("aggregated_results", {}).get(
        "recommendations", []
    )
    if recommendations:
        summary += "\n**Key Recommendations:**\n"
        for rec in recommendations[:3]:  # Top 3 recommendations
            summary += f"- {rec}\n"

    return summary


def format_citations(citations: list[dict[str, Any]], style: str = "APA") -> list[str]:
    """
    Format citations according to specified style.

    Args:
        citations: List of citation dictionaries
        style: Citation style (APA, MLA, Chicago)

    Returns:
        List of formatted citations
    """
    formatted = []

    for citation in citations:
        if style == "APA":
            formatted_citation = format_apa_citation(citation)
        elif style == "MLA":
            formatted_citation = format_mla_citation(citation)
        elif style == "Chicago":
            formatted_citation = format_chicago_citation(citation)
        else:
            formatted_citation = format_simple_citation(citation)

        formatted.append(formatted_citation)

    # Sort alphabetically
    formatted.sort()

    return formatted


def format_apa_citation(citation: dict[str, Any]) -> str:
    """Format citation in APA style."""
    author = citation.get("author", "Unknown")
    year = citation.get("year", "n.d.")
    title = citation.get("title", "Untitled")
    journal = citation.get("journal", "")

    if journal:
        return f"{author} ({year}). {title}. {journal}."
    else:
        return f"{author} ({year}). {title}."


def format_mla_citation(citation: dict[str, Any]) -> str:
    """Format citation in MLA style."""
    author = citation.get("author", "Unknown")
    title = citation.get("title", "Untitled")
    journal = citation.get("journal", "")
    year = citation.get("year", "n.d.")

    if journal:
        return f'{author}. "{title}." {journal}, {year}.'
    else:
        return f"{author}. {title}. {year}."


def format_chicago_citation(citation: dict[str, Any]) -> str:
    """Format citation in Chicago style."""
    author = citation.get("author", "Unknown")
    title = citation.get("title", "Untitled")
    journal = citation.get("journal", "")
    year = citation.get("year", "n.d.")

    if journal:
        return f'{author}. "{title}." {journal} ({year}).'
    else:
        return f"{author}. {title}. {year}."


def format_simple_citation(citation: dict[str, Any]) -> str:
    """Format citation in simple style."""
    return f"{citation.get('author', 'Unknown')} - {citation.get('title', 'Untitled')} ({citation.get('year', 'n.d.')})"


def generate_visualizations(results: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Generate visualization specifications.

    Args:
        results: Aggregated results

    Returns:
        List of visualization specifications
    """
    visualizations = []

    # Source distribution chart
    sources = results.get("sources", [])
    if sources:
        year_dist = {}
        for source in sources:
            year = source.get("year", "Unknown")
            year_dist[year] = year_dist.get(year, 0) + 1

        visualizations.append(
            {
                "type": "bar_chart",
                "title": "Source Distribution by Year",
                "data": year_dist,
                "x_label": "Year",
                "y_label": "Number of Sources",
            }
        )

    # Confidence scores chart
    findings = results.get("findings", {})
    if findings:
        confidence_data = []
        for category, finding_list in findings.items():
            avg_confidence = (
                sum(f.get("confidence", 0.5) for f in finding_list) / len(finding_list)
                if finding_list
                else 0
            )
            confidence_data.append({"category": category, "confidence": avg_confidence})

        visualizations.append(
            {
                "type": "radar_chart",
                "title": "Confidence by Category",
                "data": confidence_data,
            }
        )

    return visualizations


def generate_markdown_report(report: dict[str, Any]) -> str:
    """
    Generate markdown version of the report.

    Args:
        report: Report structure

    Returns:
        Markdown formatted report
    """
    markdown = f"# {report['title']}\n\n"

    # Add metadata
    markdown += f"**Generated:** {report['metadata']['generated_at']}\n"
    markdown += f"**Quality Score:** {report['metadata']['quality_score']:.2%}\n\n"

    # Add executive summary
    if report.get("executive_summary"):
        markdown += report["executive_summary"] + "\n\n"

    # Add sections
    for section in report.get("sections", []):
        markdown += f"## {section['title']}\n\n"
        markdown += section["content"] + "\n\n"

        for subsection in section.get("subsections", []):
            markdown += f"### {subsection['title']}\n\n"
            markdown += subsection["content"] + "\n\n"

    # Add references
    if report.get("references"):
        markdown += "## References\n\n"
        for ref in report["references"]:
            markdown += f"- {ref}\n"

    return markdown


def generate_html_report(report: dict[str, Any]) -> str:
    """
    Generate HTML version of the report.

    Args:
        report: Report structure

    Returns:
        HTML formatted report
    """
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>{report['title']}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; }}
        h1 {{ color: #333; }}
        h2 {{ color: #666; }}
        .metadata {{ background: #f0f0f0; padding: 10px; margin: 20px 0; }}
        .section {{ margin: 20px 0; }}
    </style>
</head>
<body>
    <h1>{report['title']}</h1>
    
    <div class="metadata">
        <strong>Generated:</strong> {report['metadata']['generated_at']}<br>
        <strong>Quality Score:</strong> {report['metadata']['quality_score']:.2%}
    </div>
"""

    # Add executive summary
    if report.get("executive_summary"):
        html += f"<div class='section'>{report['executive_summary']}</div>"

    # Add sections
    for section in report.get("sections", []):
        html += f"<div class='section'><h2>{section['title']}</h2>"
        html += f"<p>{section['content']}</p>"

        for subsection in section.get("subsections", []):
            html += f"<h3>{subsection['title']}</h3>"
            html += f"<p>{subsection['content']}</p>"

        html += "</div>"

    # Add references
    if report.get("references"):
        html += "<div class='section'><h2>References</h2><ul>"
        for ref in report["references"]:
            html += f"<li>{ref}</li>"
        html += "</ul></div>"

    html += "</body></html>"

    return html


def calculate_generation_time(state: ResearchState) -> float:
    """
    Calculate total workflow execution time.

    Args:
        state: Current workflow state

    Returns:
        Time in seconds
    """
    if state.metadata:
        start = state.metadata.started_at
        end = datetime.utcnow()
        return (end - start).total_seconds()
    return 0.0


def get_confidence_level(score: float) -> str:
    """
    Get confidence level description from score.

    Args:
        score: Confidence score (0-1)

    Returns:
        Confidence level description
    """
    if score >= 0.9:
        return "very high"
    elif score >= 0.7:
        return "high"
    elif score >= 0.5:
        return "moderate"
    elif score >= 0.3:
        return "low"
    else:
        return "very low"


__all__ = ["report_generation_node"]
