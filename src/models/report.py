"""
Report data models and schemas.

This module defines the Pydantic models for representing research reports
and their components following functional programming principles.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ReportFormat(StrEnum):
    """Supported report output formats."""
    
    MARKDOWN = "markdown"
    HTML = "html"
    PDF = "pdf"
    LATEX = "latex"
    DOCX = "docx"
    JSON = "json"


class ReportType(StrEnum):
    """Different types of research reports."""
    
    COMPREHENSIVE = "comprehensive"
    EXECUTIVE_SUMMARY = "executive_summary"
    ACADEMIC_PAPER = "academic"
    LITERATURE_REVIEW = "literature_review"
    METHODOLOGY_REPORT = "methodology"
    SYNTHESIS_REPORT = "synthesis"


class CitationStyle(StrEnum):
    """Supported citation styles."""
    
    APA = "APA"
    MLA = "MLA"
    CHICAGO = "Chicago"
    IEEE = "IEEE"
    HARVARD = "Harvard"


class VisualizationType(StrEnum):
    """Types of visualizations that can be generated."""
    
    BAR_CHART = "bar_chart"
    LINE_CHART = "line_chart"
    PIE_CHART = "pie_chart"
    SCATTER_PLOT = "scatter_plot"
    RADAR_CHART = "radar_chart"
    HEATMAP = "heatmap"
    NETWORK_GRAPH = "network_graph"
    WORD_CLOUD = "word_cloud"
    HISTOGRAM = "histogram"
    BOX_PLOT = "box_plot"


class ReportSection(BaseModel):
    """A section within a research report."""
    
    title: str = Field(..., description="Section title")
    content: str = Field(..., description="Section content in markdown")
    subsections: list["ReportSection"] = Field(
        default_factory=list, description="Nested subsections"
    )
    level: int = Field(default=1, ge=1, le=6, description="Heading level (1-6)")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional section metadata"
    )
    
    @field_validator('level')
    @classmethod
    def validate_level(cls, v: int) -> int:
        """Ensure heading level is valid."""
        return max(1, min(6, v))


class Visualization(BaseModel):
    """Specification for a visualization in the report."""
    
    id: str = Field(..., description="Unique identifier for the visualization")
    type: VisualizationType = Field(..., description="Type of visualization")
    title: str = Field(..., description="Visualization title")
    data: dict[str, Any] = Field(..., description="Data for the visualization")
    config: dict[str, Any] = Field(
        default_factory=dict, description="Visualization configuration"
    )
    caption: str | None = Field(None, description="Caption text")
    width: int | None = Field(None, ge=100, description="Width in pixels")
    height: int | None = Field(None, ge=100, description="Height in pixels")


class Citation(BaseModel):
    """A formatted citation."""
    
    id: str = Field(..., description="Unique citation identifier")
    authors: list[str] = Field(..., description="List of authors")
    title: str = Field(..., description="Publication title")
    year: int | None = Field(None, description="Publication year")
    journal: str | None = Field(None, description="Journal name")
    volume: str | None = Field(None, description="Volume number")
    issue: str | None = Field(None, description="Issue number")
    pages: str | None = Field(None, description="Page range")
    doi: str | None = Field(None, description="Digital Object Identifier")
    url: str | None = Field(None, description="URL if available")
    publisher: str | None = Field(None, description="Publisher name")
    location: str | None = Field(None, description="Publication location")
    isbn: str | None = Field(None, description="ISBN for books")
    source_type: str = Field(default="journal", description="Type of source")
    
    def format_citation(self, style: CitationStyle) -> str:
        """Format the citation according to the specified style."""
        authors_str = ", ".join(self.authors) if self.authors else "Unknown"
        year_str = f"({self.year})" if self.year else "(n.d.)"
        
        if style == CitationStyle.APA:
            return self._format_apa(authors_str, year_str)
        elif style == CitationStyle.MLA:
            return self._format_mla(authors_str)
        elif style == CitationStyle.CHICAGO:
            return self._format_chicago(authors_str, year_str)
        elif style == CitationStyle.IEEE:
            return self._format_ieee(authors_str, year_str)
        elif style == CitationStyle.HARVARD:
            return self._format_harvard(authors_str, year_str)
        else:
            return f"{authors_str} {year_str}. {self.title}."
    
    def _format_apa(self, authors: str, year: str) -> str:
        """Format citation in APA style."""
        citation = f"{authors} {year}. {self.title}."
        if self.journal:
            citation += f" {self.journal}"
            if self.volume:
                citation += f", {self.volume}"
                if self.issue:
                    citation += f"({self.issue})"
            if self.pages:
                citation += f", {self.pages}"
        return citation + "."
    
    def _format_mla(self, authors: str) -> str:
        """Format citation in MLA style."""
        citation = f'{authors.rstrip(".")}. "{self.title}."'
        if self.journal:
            citation += f" {self.journal}"
            if self.volume:
                citation += f", vol. {self.volume}"
            if self.issue:
                citation += f", no. {self.issue}"
            if self.year:
                citation += f", {self.year}"
            if self.pages:
                citation += f", pp. {self.pages}"
        return citation + "."
    
    def _format_chicago(self, authors: str, year: str) -> str:
        """Format citation in Chicago style."""
        citation = f'{authors}. "{self.title}."'
        if self.journal:
            citation += f" {self.journal}"
            if self.volume:
                citation += f" {self.volume}"
                if self.issue:
                    citation += f", no. {self.issue}"
            citation += f" {year.strip('()')}"
            if self.pages:
                citation += f": {self.pages}"
        return citation + "."
    
    def _format_ieee(self, authors: str, year: str) -> str:
        """Format citation in IEEE style."""
        citation = f'{authors}, "{self.title},"'
        if self.journal:
            citation += f" {self.journal}"
            if self.volume:
                citation += f", vol. {self.volume}"
            if self.issue:
                citation += f", no. {self.issue}"
            if self.pages:
                citation += f", pp. {self.pages}"
            if self.year:
                citation += f", {self.year}"
        return citation + "."
    
    def _format_harvard(self, authors: str, year: str) -> str:
        """Format citation in Harvard style."""
        citation = f"{authors} {year}, '{self.title}'"
        if self.journal:
            citation += f", {self.journal}"
            if self.volume:
                citation += f", vol. {self.volume}"
                if self.issue:
                    citation += f", no. {self.issue}"
            if self.pages:
                citation += f", pp. {self.pages}"
        return citation + "."


class ReportMetadata(BaseModel):
    """Metadata for a research report."""
    
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    workflow_id: str | None = Field(None, description="Associated workflow ID")
    project_id: UUID | None = Field(None, description="Associated project ID")
    user_id: UUID | None = Field(None, description="Report creator ID")
    quality_score: float = Field(0.0, ge=0.0, le=1.0, description="Quality assessment score")
    confidence_score: float = Field(0.0, ge=0.0, le=1.0, description="Overall confidence")
    total_sources: int = Field(0, ge=0, description="Number of sources analyzed")
    total_citations: int = Field(0, ge=0, description="Number of citations")
    agents_used: list[str] = Field(default_factory=list, description="Agents involved")
    generation_time_seconds: float = Field(0.0, ge=0.0, description="Generation time")
    word_count: int = Field(0, ge=0, description="Total word count")
    page_count: int | None = Field(None, ge=1, description="Estimated page count")
    version: str = Field(default="1.0", description="Report version")
    
    @field_validator('quality_score', 'confidence_score')
    @classmethod
    def validate_scores(cls, v: float) -> float:
        """Ensure scores are between 0 and 1."""
        return max(0.0, min(1.0, v))


class ReportConfiguration(BaseModel):
    """Configuration settings for report generation."""
    
    format: ReportFormat = Field(default=ReportFormat.HTML)
    type: ReportType = Field(default=ReportType.COMPREHENSIVE)
    citation_style: CitationStyle = Field(default=CitationStyle.APA)
    include_toc: bool = Field(default=True, description="Include table of contents")
    include_executive_summary: bool = Field(default=True)
    include_visualizations: bool = Field(default=True)
    include_appendices: bool = Field(default=False)
    include_citations: bool = Field(default=True)
    include_methodology: bool = Field(default=True)
    max_sections: int | None = Field(None, ge=1, description="Maximum sections")
    custom_css: str | None = Field(None, description="Custom CSS styles")
    template_name: str | None = Field(None, description="Custom template name")
    language: str = Field(default="en", description="Report language")
    author_name: str | None = Field(None, description="Report author")
    institution: str | None = Field(None, description="Author institution")
    
    # PDF-specific settings
    pdf_settings: dict[str, Any] = Field(
        default_factory=lambda: {
            "page_size": "A4",
            "margin_top": "2cm",
            "margin_bottom": "2cm",
            "margin_left": "2cm",
            "margin_right": "2cm",
            "header_text": "",
            "footer_text": "Page {page_number}",
            "font_family": "Arial",
            "font_size": "11pt",
        }
    )
    
    # LaTeX-specific settings
    latex_settings: dict[str, Any] = Field(
        default_factory=lambda: {
            "document_class": "article",
            "packages": ["geometry", "graphicx", "hyperref", "cite"],
            "bibliography_style": "plain",
            "font_size": "11pt",
            "paper_size": "a4paper",
        }
    )


class ReportOutput(BaseModel):
    """Generated report output in a specific format."""
    
    format: ReportFormat = Field(..., description="Output format")
    content: str | bytes = Field(..., description="Report content")
    file_path: str | None = Field(None, description="File path if saved")
    file_size: int | None = Field(None, ge=0, description="File size in bytes")
    mime_type: str = Field(..., description="MIME type of the content")
    encoding: str = Field(default="utf-8", description="Character encoding")
    
    @property
    def is_binary(self) -> bool:
        """Check if the output is binary content."""
        return isinstance(self.content, bytes)


class Report(BaseModel):
    """Complete research report structure."""
    
    id: str = Field(..., description="Unique report identifier")
    title: str = Field(..., description="Report title")
    query: str = Field(..., description="Original research question")
    domains: list[str] = Field(default_factory=list, description="Research domains")
    abstract: str | None = Field(None, description="Report abstract")
    executive_summary: str | None = Field(None, description="Executive summary")
    
    # Report structure
    sections: list[ReportSection] = Field(
        default_factory=list, description="Main report sections"
    )
    appendices: list[ReportSection] = Field(
        default_factory=list, description="Report appendices"
    )
    
    # Content elements
    citations: list[Citation] = Field(
        default_factory=list, description="Bibliography"
    )
    visualizations: list[Visualization] = Field(
        default_factory=list, description="Charts and graphs"
    )
    
    # Metadata and configuration
    metadata: ReportMetadata = Field(
        default_factory=lambda: ReportMetadata(
            workflow_id=None,
            project_id=None,
            user_id=None,
            quality_score=0.0,
            confidence_score=0.0,
            total_sources=0,
            total_citations=0,
            generation_time_seconds=0.0,
            word_count=0,
            page_count=None,
        ),
        description="Report metadata"
    )
    configuration: ReportConfiguration = Field(
        default_factory=lambda: ReportConfiguration(
            max_sections=None,
            custom_css=None,
            template_name=None,
            author_name=None,
            institution=None,
        ),
        description="Generation configuration"
    )

    # Generated outputs
    outputs: dict[ReportFormat, ReportOutput] = Field(
        default_factory=dict, description="Generated outputs by format"
    )
    
    def add_section(
        self,
        title: str,
        content: str,
        level: int = 1,
        metadata: dict[str, Any] | None = None
    ) -> ReportSection:
        """Add a new section to the report."""
        section = ReportSection(
            title=title,
            content=content,
            level=level,
            metadata=metadata or {}
        )
        self.sections.append(section)
        return section
    
    def add_citation(self, citation: Citation) -> None:
        """Add a citation to the bibliography."""
        # Avoid duplicates
        existing_ids = {c.id for c in self.citations}
        if citation.id not in existing_ids:
            self.citations.append(citation)
    
    def add_visualization(self, visualization: Visualization) -> None:
        """Add a visualization to the report."""
        self.visualizations.append(visualization)
    
    def get_word_count(self) -> int:
        """Calculate total word count of the report."""
        word_count = 0
        
        # Count words in abstract and executive summary
        if self.abstract:
            word_count += len(self.abstract.split())
        if self.executive_summary:
            word_count += len(self.executive_summary.split())
        
        # Count words in sections
        def count_section_words(section: ReportSection) -> int:
            count = len(section.content.split())
            for subsection in section.subsections:
                count += count_section_words(subsection)
            return count
        
        for section in self.sections:
            word_count += count_section_words(section)
        
        for appendix in self.appendices:
            word_count += count_section_words(appendix)
        
        # Update metadata
        self.metadata.word_count = word_count
        return word_count
    
    def estimate_page_count(self, words_per_page: int = 250) -> int:
        """Estimate the number of pages based on word count."""
        word_count = self.get_word_count()
        page_count = max(1, (word_count + words_per_page - 1) // words_per_page)
        self.metadata.page_count = page_count
        return page_count


# Enable forward references for recursive models
ReportSection.model_rebuild()


class ReportGenerationRequest(BaseModel):
    """Request model for report generation."""
    
    project_id: UUID | None = Field(None, description="Project ID to generate report for")
    workflow_data: dict[str, Any] | None = Field(
        None, description="Direct workflow data if no project"
    )
    configuration: ReportConfiguration = Field(
        default_factory=lambda: ReportConfiguration(
            max_sections=None,
            custom_css=None,
            template_name=None,
            author_name=None,
            institution=None,
        ),
        description="Generation configuration"
    )
    formats: list[ReportFormat] = Field(
        default=[ReportFormat.HTML], description="Desired output formats"
    )
    save_to_storage: bool = Field(default=True, description="Save generated reports")
    notify_completion: bool = Field(default=False, description="Send completion notification")


class ReportGenerationResponse(BaseModel):
    """Response model for report generation."""
    
    report_id: str = Field(..., description="Generated report ID")
    status: str = Field(..., description="Generation status")
    formats_generated: list[ReportFormat] = Field(
        ..., description="Successfully generated formats"
    )
    generation_time: float = Field(..., description="Total generation time in seconds")
    word_count: int = Field(..., description="Report word count")
    page_count: int = Field(..., description="Estimated page count")
    download_urls: dict[ReportFormat, str] = Field(
        default_factory=dict, description="Download URLs by format"
    )
    errors: list[str] = Field(default_factory=list, description="Generation errors")


__all__ = [
    "Citation",
    "CitationStyle",
    "Report",
    "ReportConfiguration",
    "ReportFormat",
    "ReportGenerationRequest",
    "ReportGenerationResponse",
    "ReportMetadata",
    "ReportOutput",
    "ReportSection",
    "ReportType",
    "Visualization",
    "VisualizationType",
]
