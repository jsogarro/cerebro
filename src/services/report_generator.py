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
    Report,
    ReportConfiguration,
    ReportFormat,
    ReportGenerationRequest,
    ReportGenerationResponse,
    ReportOutput,
    ReportSection,
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
from src.services.report_structure_builder import ReportStructureBuilder

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
        self.structure_builder = ReportStructureBuilder(
            self.template_config, self.quality_config
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
        await self.structure_builder.build_sections(report, input_data)
    
    async def _build_introduction_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build introduction section."""
        return await self.structure_builder.build_introduction_section(report, results)
    
    async def _build_methodology_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build methodology section."""
        return await self.structure_builder.build_methodology_section(report, results)
    
    async def _build_literature_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build literature review section."""
        return await self.structure_builder.build_literature_section(report, results)
    
    async def _build_findings_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build findings section."""
        return await self.structure_builder.build_findings_section(report, results)
    
    async def _build_analysis_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build analysis section."""
        return await self.structure_builder.build_analysis_section(report, results)
    
    async def _build_discussion_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build discussion section."""
        return await self.structure_builder.build_discussion_section(report, results)
    
    async def _build_conclusions_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build conclusions section."""
        return await self.structure_builder.build_conclusions_section(report, results)
    
    async def _build_recommendations_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build recommendations section."""
        return await self.structure_builder.build_recommendations_section(report, results)
    
    async def _build_limitations_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build limitations section."""
        return await self.structure_builder.build_limitations_section(report, results)
    
    async def _build_abstract_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build abstract section for academic papers."""
        return await self.structure_builder.build_abstract_section(report, results)
    
    async def _build_results_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build results section for academic papers."""
        return await self.structure_builder.build_results_section(report, results)
    
    async def _build_key_findings_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build key findings section for executive summary."""
        return await self.structure_builder.build_key_findings_section(report, results)
    
    async def _build_insights_section(
        self, report: Report, results: dict[str, Any]
    ) -> ReportSection | None:
        """Build insights section."""
        return await self.structure_builder.build_insights_section(report, results)
    
    async def _process_citations(self, report: Report, input_data: dict[str, Any]) -> None:
        """Process and format citations from input data."""
        await self.structure_builder.process_citations(report, input_data)
    
    async def _generate_visualizations(self, report: Report, input_data: dict[str, Any]) -> None:
        """Generate visualization specifications (actual chart generation will be handled by exporters)."""
        await self.structure_builder.generate_visualizations(report, input_data)
    
    async def _build_metadata(self, report: Report, input_data: dict[str, Any]) -> None:
        """Build report metadata from input data."""
        await self.structure_builder.build_metadata(report, input_data)
    
    async def _generate_executive_summary(self, report: Report, input_data: dict[str, Any]) -> None:
        """Generate executive summary for the report."""
        await self.structure_builder.generate_executive_summary(report, input_data)
    
    async def _validate_report_quality(self, report: Report) -> None:
        """Validate report quality against configured thresholds."""
        await self.structure_builder.validate_report_quality(report)
    
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
