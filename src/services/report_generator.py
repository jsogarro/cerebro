"""
Main report generation service.

This service orchestrates the generation of research reports in multiple formats,
following functional programming principles with pure transformation functions.
"""

import hashlib
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from src.models.report import (
    Citation,
    Report,
    ReportConfiguration,
    ReportFormat,
    ReportGenerationRequest,
    ReportGenerationResponse,
    ReportMetadata,
    ReportOutput,
    ReportSection,
    Visualization,
    VisualizationType,
)
from src.services.report_config import (
    ReportFormatConfig,
    ReportQualityConfig,
    ReportSettings,
    ReportTemplateConfig,
    create_format_config,
    create_quality_config,
    create_report_settings,
    create_template_config,
)
from src.services.report_output_generator import ReportOutputGenerator

logger = logging.getLogger(__name__)


class ReportGenerationError(Exception):
    """Exception raised during report generation."""
    pass


class ReportGenerator:
    """Main service for generating research reports in multiple formats."""
    
    def __init__(
        self,
        settings: ReportSettings | None = None,
        template_config: ReportTemplateConfig | None = None,
        format_config: ReportFormatConfig | None = None,
        quality_config: ReportQualityConfig | None = None,
    ):
        """Initialize the report generator."""
        self.settings = settings or create_report_settings()
        self.template_config = template_config or create_template_config(self.settings)
        self.format_config = format_config or create_format_config(self.settings)
        self.quality_config = quality_config or create_quality_config(self.settings)
        
        # Initialize storage directory
        self._ensure_storage_directory()

        self.output_generator = ReportOutputGenerator(
            self.settings, self.format_config, self.template_config
        )
    
    def _ensure_storage_directory(self) -> None:
        """Ensure the report storage directory exists."""
        try:
            os.makedirs(self.settings.report_storage_path, exist_ok=True)
        except OSError as e:
            logger.error(f"Failed to create storage directory: {e}")
            raise ReportGenerationError(f"Storage setup failed: {e}") from e
    
    async def generate_report(
        self,
        request: ReportGenerationRequest
    ) -> ReportGenerationResponse:
        """
        Generate a research report from the request.
        
        This is the main entry point for report generation, following a functional
        pipeline: data extraction -> structure building -> format generation.
        
        Args:
            request: Report generation request with configuration
            
        Returns:
            Response with generated report information
        """
        start_time = time.time()
        report_id = self._generate_report_id()
        
        logger.info(f"Starting report generation {report_id}")
        
        try:
            # Extract and validate input data
            input_data = await self._extract_input_data(request)
            
            # Build report structure
            report = await self._build_report_structure(
                input_data, request.configuration, report_id
            )
            
            # Generate outputs in requested formats
            outputs = await self._generate_formats(report, request.formats)
            
            # Save outputs if requested
            if request.save_to_storage:
                await self._save_outputs(report, outputs)
            
            # Calculate metrics
            generation_time = time.time() - start_time
            word_count = report.get_word_count()
            page_count = report.estimate_page_count()
            
            # Build response
            response = ReportGenerationResponse(
                report_id=report_id,
                status="completed",
                formats_generated=list(outputs.keys()),
                generation_time=generation_time,
                word_count=word_count,
                page_count=page_count,
                download_urls=self._build_download_urls(report_id, outputs),
                errors=[]
            )
            
            logger.info(
                f"Report generation {report_id} completed in {generation_time:.2f}s"
            )
            
            return response
            
        except Exception as e:
            generation_time = time.time() - start_time
            logger.error(f"Report generation {report_id} failed: {e}")
            
            return ReportGenerationResponse(
                report_id=report_id,
                status="failed",
                formats_generated=[],
                generation_time=generation_time,
                word_count=0,
                page_count=0,
                download_urls={},
                errors=[str(e)]
            )
    
    async def _extract_input_data(
        self, request: ReportGenerationRequest
    ) -> dict[str, Any]:
        """
        Extract and validate input data from the request.
        
        This function normalizes data from different sources (project ID or direct data)
        into a consistent format for report generation.
        """
        if request.project_id:
            # Load data from project ID (would integrate with database)
            return await self._load_project_data(str(request.project_id))
        elif request.workflow_data:
            # Use direct workflow data
            return self._validate_workflow_data(request.workflow_data)
        else:
            raise ReportGenerationError("No data source provided (project_id or workflow_data)")
    
    async def _load_project_data(self, project_id: str) -> dict[str, Any]:
        """Load project data from database (mock implementation)."""
        # In a real implementation, this would query the database
        # For now, return mock data structure
        return {
            "title": f"Research Project {project_id}",
            "query": "Sample research question",
            "domains": ["AI", "Technology"],
            "aggregated_results": {
                "sources": [],
                "findings": {},
                "insights": [],
                "citations": [],
                "recommendations": [],
            },
            "quality_report": {
                "passed": True,
                "issues_found": [],
            },
            "metadata": {
                "workflow_id": str(uuid4()),
                "agents_used": ["literature_review", "synthesis"],
                "total_sources": 0,
                "total_citations": 0,
            }
        }
    
    def _validate_workflow_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize workflow data."""
        required_fields = ["title", "query"]
        for field in required_fields:
            if field not in data:
                raise ReportGenerationError(f"Missing required field: {field}")
        
        # Ensure expected structure exists
        if "aggregated_results" not in data:
            data["aggregated_results"] = {}
        if "quality_report" not in data:
            data["quality_report"] = {"passed": True, "issues_found": []}
        if "metadata" not in data:
            data["metadata"] = {}
            
        return data
    
    async def _build_report_structure(
        self,
        input_data: dict[str, Any],
        config: ReportConfiguration,
        report_id: str
    ) -> Report:
        """
        Build the complete report structure from input data.
        
        This function transforms raw research data into a structured Report object
        with sections, citations, and metadata.
        """
        # Extract basic information
        title = input_data.get("title", "Research Report")
        query = input_data.get("query", "")
        domains = input_data.get("domains", [])
        
        # Create report instance
        report = Report(
            id=report_id,
            title=title,
            query=query,
            domains=domains,
            abstract=None,
            executive_summary=None,
            configuration=config
        )
        
        # Build sections based on report type
        await self._build_sections(report, input_data)
        
        # Process citations
        await self._process_citations(report, input_data)
        
        # Generate visualizations if enabled
        if config.include_visualizations:
            await self._generate_visualizations(report, input_data)
        
        # Build metadata
        await self._build_metadata(report, input_data)
        
        # Generate executive summary
        if config.include_executive_summary:
            await self._generate_executive_summary(report, input_data)
        
        # Quality validation
        await self._validate_report_quality(report)
        
        return report
    
    async def _build_sections(self, report: Report, input_data: dict[str, Any]) -> None:
        """Build report sections based on configuration and input data."""
        report_type = report.configuration.type
        required_sections = self.template_config.get_required_sections(report_type)
        aggregated_results = input_data.get("aggregated_results", {})
        
        section_builders = {
            "introduction": self._build_introduction_section,
            "methodology": self._build_methodology_section,
            "literature_review": self._build_literature_section,
            "findings": self._build_findings_section,
            "analysis": self._build_analysis_section,
            "discussion": self._build_discussion_section,
            "conclusions": self._build_conclusions_section,
            "recommendations": self._build_recommendations_section,
            "limitations": self._build_limitations_section,
            "abstract": self._build_abstract_section,
            "results": self._build_results_section,
            "key_findings": self._build_key_findings_section,
            "strategic_insights": self._build_insights_section,
        }
        
        for section_name in required_sections:
            if section_name in section_builders:
                section = await section_builders[section_name](report, aggregated_results)
                if section:
                    report.sections.append(section)
    
    async def _build_introduction_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build introduction section."""
        content = f"""
        This research investigates the following question: "{report.query}"
        
        The research spans across the following domains: {', '.join(report.domains)}.
        
        This report presents a comprehensive analysis of the available literature and
        synthesizes key findings to provide insights and recommendations.
        """.strip()
        
        return ReportSection(
            title="Introduction",
            content=content,
            level=1
        )
    
    async def _build_methodology_section(
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
        
        return ReportSection(
            title="Methodology",
            content=content,
            level=1
        )
    
    async def _build_literature_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build literature review section."""
        sources = results.get("sources", [])
        
        content = f"## Literature Review\n\nThis review examines {len(sources)} sources.\n\n"
        
        # Include top sources
        for source in sources[:10]:
            title = source.get("title", "Untitled")
            year = source.get("year", "n.d.")
            content += f"- {title} ({year})\n"
            if source.get("summary"):
                content += f"  {source['summary'][:200]}...\n"
        
        return ReportSection(
            title="Literature Review",
            content=content,
            level=1
        )
    
    async def _build_findings_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build findings section."""
        findings = results.get("findings", {})
        
        if not findings:
            return None
        
        section = ReportSection(
            title="Key Findings",
            content=f"The research identified {sum(len(f) for f in findings.values())} key findings across {len(findings)} categories.",
            level=1
        )
        
        # Add subsections for each category
        for category, finding_list in findings.items():
            if finding_list:
                subsection_content = ""
                for finding in finding_list:
                    text = finding.get("text", "") if isinstance(finding, dict) else str(finding)
                    subsection_content += f"- {text}\n"
                    if isinstance(finding, dict) and finding.get("confidence"):
                        subsection_content += f"  (Confidence: {finding['confidence']:.2f})\n"
                
                subsection = ReportSection(
                    title=category.replace("_", " ").title(),
                    content=subsection_content,
                    level=2
                )
                section.subsections.append(subsection)
        
        return section
    
    async def _build_analysis_section(
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
        
        return ReportSection(
            title="Analysis",
            content=content,
            level=1
        )
    
    async def _build_discussion_section(
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
        
        return ReportSection(
            title="Discussion",
            content=content,
            level=1
        )
    
    async def _build_conclusions_section(
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
        {self._get_confidence_level(confidence_score)} confidence.
        """.strip()
        
        return ReportSection(
            title="Conclusions",
            content=content,
            level=1
        )
    
    async def _build_recommendations_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build recommendations section."""
        recommendations = results.get("recommendations", [])
        
        if not recommendations:
            return None
        
        content = "## Recommendations\n\nBased on the research findings:\n\n"
        
        for i, rec in enumerate(recommendations, 1):
            content += f"{i}. {rec}\n"
        
        return ReportSection(
            title="Recommendations",
            content=content,
            level=1
        )
    
    async def _build_limitations_section(
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
        
        return ReportSection(
            title="Limitations",
            content=content,
            level=1
        )
    
    async def _build_abstract_section(
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
        
        return ReportSection(
            title="Abstract",
            content=content,
            level=1
        )
    
    async def _build_results_section(
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
        
        return ReportSection(
            title="Results",
            content=content,
            level=1
        )
    
    async def _build_key_findings_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build key findings section for executive summary."""
        findings = results.get("findings", {})
        
        # Get top findings from each category
        top_findings = []
        for _category, finding_list in findings.items():
            for finding in finding_list[:2]:  # Top 2 from each category
                if isinstance(finding, dict):
                    top_findings.append(finding.get("text", ""))
                else:
                    top_findings.append(str(finding))
        
        content = "## Key Findings\n\n"
        for finding in top_findings:
            content += f"• {finding}\n"
        
        return ReportSection(
            title="Key Findings",
            content=content,
            level=1
        )
    
    async def _build_insights_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build insights section."""
        insights = results.get("insights", [])
        
        # Filter high-importance insights
        high_importance = []
        for insight in insights:
            if isinstance(insight, dict) and insight.get("importance") == "high":
                high_importance.append(insight.get("text", ""))
            elif isinstance(insight, str):
                high_importance.append(insight)
        
        content = "## Strategic Insights\n\n"
        for insight in high_importance:
            content += f"• {insight}\n"
        
        return ReportSection(
            title="Strategic Insights",
            content=content,
            level=1
        )
    
    async def _process_citations(self, report: Report, input_data: dict[str, Any]) -> None:
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
    
    async def _generate_visualizations(self, report: Report, input_data: dict[str, Any]) -> None:
        """Generate visualization specifications (actual chart generation will be handled by exporters)."""
        aggregated_results = input_data.get("aggregated_results", {})
        
        # Source distribution visualization
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
                    data={"years": list(year_dist.keys()), "counts": list(year_dist.values())},
                    config={"x_label": "Year", "y_label": "Number of Sources"},
                    caption=None,
                    width=None,
                    height=None
                )
                report.add_visualization(viz)
        
        # Domain distribution visualization
        if len(report.domains) > 1:
            viz = Visualization(
                id="domain_distribution",
                type=VisualizationType.PIE_CHART,
                title="Research Domains Coverage",
                data={"labels": report.domains, "values": [1] * len(report.domains)},
                config={},
                caption=None,
                width=None,
                height=None
            )
            report.add_visualization(viz)
    
    async def _build_metadata(self, report: Report, input_data: dict[str, Any]) -> None:
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
    
    async def _generate_executive_summary(self, report: Report, input_data: dict[str, Any]) -> None:
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
        
        # Add top insights
        for insight in insights[:3]:
            if isinstance(insight, dict):
                summary += f"- {insight.get('text', '')}\n"
            else:
                summary += f"- {insight}\n"
        
        # Add recommendations
        if recommendations:
            summary += "\n**Key Recommendations:**\n"
            for rec in recommendations[:3]:
                summary += f"- {rec}\n"
        
        report.executive_summary = summary
    
    async def _validate_report_quality(self, report: Report) -> None:
        """Validate report quality against configured thresholds."""
        report_type = report.configuration.type
        
        # Check word count
        word_count = report.get_word_count()
        min_words = self.quality_config.get_min_word_count(report_type)
        if word_count < min_words:
            logger.warning(f"Report word count ({word_count}) below minimum ({min_words})")
        
        # Check sources
        source_count = report.metadata.total_sources
        min_sources = self.quality_config.get_min_sources(report_type)
        if source_count < min_sources:
            logger.warning(f"Report source count ({source_count}) below minimum ({min_sources})")
        
        # Check citations if required
        if self.quality_config.requires_citations(report_type) and not report.citations:
            logger.warning("Report missing required citations")
    
    async def _generate_formats(
        self, report: Report, formats: list[ReportFormat]
    ) -> dict[ReportFormat, ReportOutput]:
        """Generate report outputs in the requested formats."""
        return await self.output_generator.generate_formats(report, formats)
    
    async def _generate_single_format(
        self, report: Report, format: ReportFormat
    ) -> ReportOutput:
        """Generate report output in a single format."""
        return await self.output_generator.generate_single_format(report, format)
    
    async def _generate_html(self, report: Report) -> ReportOutput:
        """Generate HTML report output."""
        return await self.output_generator.generate_html(report)
    
    async def _generate_markdown(self, report: Report) -> ReportOutput:
        """Generate Markdown report output."""
        return await self.output_generator.generate_markdown(report)
    
    async def _generate_json(self, report: Report) -> ReportOutput:
        """Generate JSON report output."""
        return await self.output_generator.generate_json(report)
    
    async def _generate_pdf(self, report: Report) -> ReportOutput:
        """Generate PDF report output."""
        return await self.output_generator.generate_pdf(report)
    
    async def _generate_latex(self, report: Report) -> ReportOutput:
        """Generate LaTeX report output."""
        return await self.output_generator.generate_latex(report)
    
    async def _generate_docx(self, report: Report) -> ReportOutput:
        """Generate DOCX report output."""
        return await self.output_generator.generate_docx(report)
    
    def _generate_basic_html(self, report: Report) -> str:
        """Generate basic HTML without templates (temporary implementation)."""
        return self.output_generator.generate_basic_html(report)
    
    async def _save_outputs(
        self, report: Report, outputs: dict[ReportFormat, ReportOutput]
    ) -> None:
        """Save generated outputs to storage."""
        report_dir = os.path.join(self.settings.report_storage_path, report.id)
        os.makedirs(report_dir, exist_ok=True)
        
        for format, output in outputs.items():
            extension = self.format_config.get_file_extension(format)
            file_path = os.path.join(report_dir, f"report{extension}")
            
            try:
                if output.is_binary:
                    with open(file_path, 'wb') as f:
                        content_bytes = output.content if isinstance(output.content, bytes) else output.content.encode(output.encoding)
                        f.write(content_bytes)
                else:
                    with open(file_path, 'w', encoding=output.encoding) as f:
                        content_str = output.content if isinstance(output.content, str) else output.content.decode(output.encoding)
                        f.write(content_str)
                
                # Update output with file path and size
                output.file_path = file_path
                output.file_size = os.path.getsize(file_path)
                
                logger.info(f"Saved {format} report to {file_path}")
                
            except Exception as e:
                logger.error(f"Failed to save {format} report: {e}")
    
    def _build_download_urls(
        self, report_id: str, outputs: dict[ReportFormat, ReportOutput]
    ) -> dict[ReportFormat, str]:
        """Build download URLs for generated outputs."""
        urls = {}
        for format in outputs:
            extension = self.format_config.get_file_extension(format)
            urls[format] = f"/api/reports/{report_id}/download{extension}"
        return urls
    
    def _generate_report_id(self) -> str:
        """Generate a unique report ID."""
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        random_suffix = hashlib.md5(str(uuid4()).encode()).hexdigest()[:8]
        return f"report_{timestamp}_{random_suffix}"
    
    def _get_confidence_level(self, score: float) -> str:
        """Get confidence level description from score."""
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


__all__ = [
    "ReportGenerationError",
    "ReportGenerator",
]
