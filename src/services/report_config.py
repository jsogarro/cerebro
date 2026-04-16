"""
Report generation configuration and settings.

This module provides configuration management for report generation,
following functional programming principles with immutable configurations.
"""

import os
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings

from src.models.report import CitationStyle, ReportFormat, ReportType


class ReportSettings(BaseSettings):
    """Report generation settings loaded from environment variables."""

    report_storage_path: str = Field(
        default="./reports",
        description="Base path for storing generated reports",
        validation_alias="REPORT_STORAGE_PATH"
    )

    max_report_size_mb: int = Field(
        default=100,
        description="Maximum report size in MB",
        validation_alias="MAX_REPORT_SIZE_MB"
    )

    default_format: ReportFormat = Field(
        default=ReportFormat.HTML,
        description="Default report format",
        validation_alias="DEFAULT_REPORT_FORMAT"
    )

    default_citation_style: CitationStyle = Field(
        default=CitationStyle.APA,
        description="Default citation style",
        validation_alias="DEFAULT_CITATION_STYLE"
    )

    enable_pdf_generation: bool = Field(
        default=True,
        description="Enable PDF generation",
        validation_alias="ENABLE_PDF_GENERATION"
    )

    enable_latex_generation: bool = Field(
        default=True,
        description="Enable LaTeX generation",
        validation_alias="ENABLE_LATEX_GENERATION"
    )

    template_path: str = Field(
        default="./src/templates/reports",
        description="Path to report templates",
        validation_alias="REPORT_TEMPLATE_PATH"
    )

    custom_css_path: str | None = Field(
        default=None,
        description="Path to custom CSS files",
        validation_alias="CUSTOM_CSS_PATH"
    )

    generation_timeout_seconds: int = Field(
        default=300,
        description="Timeout for report generation in seconds",
        validation_alias="REPORT_GENERATION_TIMEOUT"
    )

    parallel_generation: bool = Field(
        default=True,
        description="Enable parallel format generation",
        validation_alias="PARALLEL_REPORT_GENERATION"
    )

    max_concurrent_generations: int = Field(
        default=3,
        description="Maximum concurrent report generations",
        validation_alias="MAX_CONCURRENT_GENERATIONS"
    )

    enable_visualizations: bool = Field(
        default=True,
        description="Enable visualization generation",
        validation_alias="ENABLE_VISUALIZATIONS"
    )

    max_visualizations_per_report: int = Field(
        default=20,
        description="Maximum visualizations per report",
        validation_alias="MAX_VISUALIZATIONS_PER_REPORT"
    )

    default_chart_width: int = Field(
        default=800,
        description="Default chart width in pixels",
        validation_alias="DEFAULT_CHART_WIDTH"
    )

    default_chart_height: int = Field(
        default=600,
        description="Default chart height in pixels",
        validation_alias="DEFAULT_CHART_HEIGHT"
    )

    enable_template_cache: bool = Field(
        default=True,
        description="Enable template caching",
        validation_alias="ENABLE_TEMPLATE_CACHE"
    )

    template_cache_ttl_seconds: int = Field(
        default=3600,
        description="Template cache TTL in seconds",
        validation_alias="TEMPLATE_CACHE_TTL"
    )

    min_word_count: int = Field(
        default=100,
        description="Minimum word count for valid reports",
        validation_alias="MIN_REPORT_WORD_COUNT"
    )

    min_sources: int = Field(
        default=1,
        description="Minimum number of sources required",
        validation_alias="MIN_REPORT_SOURCES"
    )

    require_citations: bool = Field(
        default=True,
        description="Require citations in reports",
        validation_alias="REQUIRE_CITATIONS"
    )

    model_config = {
        "env_file": ".env",
        "env_prefix": "RESEARCH_"
    }


class ReportTemplateConfig:
    """Configuration for report templates with immutable settings."""
    
    def __init__(self, settings: ReportSettings):
        """Initialize template configuration."""
        self._settings = settings
        self._template_configs = self._build_template_configs()
    
    def _build_template_configs(self) -> dict[ReportType, dict[str, Any]]:
        """Build template configurations for each report type."""
        return {
            ReportType.COMPREHENSIVE: {
                "template_name": "comprehensive_report.html.j2",
                "sections": [
                    "executive_summary",
                    "introduction", 
                    "methodology",
                    "literature_review",
                    "findings",
                    "analysis",
                    "discussion",
                    "conclusions",
                    "recommendations",
                    "limitations",
                    "references"
                ],
                "include_toc": True,
                "include_visualizations": True,
                "include_appendices": True,
                "estimated_pages": 15,
            },
            ReportType.EXECUTIVE_SUMMARY: {
                "template_name": "executive_summary.html.j2",
                "sections": [
                    "key_findings",
                    "strategic_insights", 
                    "recommendations"
                ],
                "include_toc": False,
                "include_visualizations": True,
                "include_appendices": False,
                "estimated_pages": 3,
            },
            ReportType.ACADEMIC_PAPER: {
                "template_name": "academic_paper.html.j2",
                "sections": [
                    "abstract",
                    "introduction",
                    "literature_review", 
                    "methodology",
                    "results",
                    "discussion",
                    "conclusions",
                    "references"
                ],
                "include_toc": True,
                "include_visualizations": True,
                "include_appendices": True,
                "estimated_pages": 20,
            },
            ReportType.LITERATURE_REVIEW: {
                "template_name": "literature_review.html.j2",
                "sections": [
                    "introduction",
                    "search_methodology",
                    "literature_analysis",
                    "synthesis",
                    "gaps_identified",
                    "conclusions",
                    "references"
                ],
                "include_toc": True,
                "include_visualizations": False,
                "include_appendices": False,
                "estimated_pages": 12,
            },
            ReportType.METHODOLOGY_REPORT: {
                "template_name": "methodology_report.html.j2",
                "sections": [
                    "introduction",
                    "research_design",
                    "data_collection",
                    "analysis_methods",
                    "validation",
                    "limitations",
                    "recommendations",
                    "references"
                ],
                "include_toc": True,
                "include_visualizations": True,
                "include_appendices": True,
                "estimated_pages": 10,
            },
            ReportType.SYNTHESIS_REPORT: {
                "template_name": "synthesis_report.html.j2",
                "sections": [
                    "executive_summary",
                    "methodology",
                    "key_themes",
                    "synthesis",
                    "implications",
                    "recommendations",
                    "references"
                ],
                "include_toc": True,
                "include_visualizations": True,
                "include_appendices": False,
                "estimated_pages": 8,
            }
        }
    
    def get_template_config(self, report_type: ReportType) -> dict[str, Any]:
        """Get template configuration for a specific report type."""
        return self._template_configs.get(report_type, self._template_configs[ReportType.COMPREHENSIVE])
    
    def get_template_path(self, report_type: ReportType) -> str:
        """Get the template file path for a report type."""
        config = self.get_template_config(report_type)
        template_name = config["template_name"]
        return os.path.join(self._settings.template_path, template_name)
    
    def get_required_sections(self, report_type: ReportType) -> list[str]:
        config = self.get_template_config(report_type)
        result: list[str] = config.get("sections", [])
        return result
    
    def should_include_toc(self, report_type: ReportType) -> bool:
        config = self.get_template_config(report_type)
        result: bool = config.get("include_toc", True)
        return result

    def should_include_visualizations(self, report_type: ReportType) -> bool:
        config = self.get_template_config(report_type)
        result: bool = config.get("include_visualizations", True)
        return result

    def should_include_appendices(self, report_type: ReportType) -> bool:
        config = self.get_template_config(report_type)
        result: bool = config.get("include_appendices", False)
        return result

    def estimate_pages(self, report_type: ReportType) -> int:
        config = self.get_template_config(report_type)
        result: int = config.get("estimated_pages", 10)
        return result


class ReportFormatConfig:
    """Configuration for different output formats."""
    
    def __init__(self, settings: ReportSettings):
        """Initialize format configuration."""
        self._settings = settings
        self._format_configs = self._build_format_configs()
    
    def _build_format_configs(self) -> dict[ReportFormat, dict[str, Any]]:
        """Build format-specific configurations."""
        return {
            ReportFormat.HTML: {
                "mime_type": "text/html",
                "file_extension": ".html",
                "encoding": "utf-8",
                "supports_interactive": True,
                "supports_css": True,
                "supports_images": True,
            },
            ReportFormat.PDF: {
                "mime_type": "application/pdf",
                "file_extension": ".pdf", 
                "encoding": "binary",
                "supports_interactive": False,
                "supports_css": True,
                "supports_images": True,
                "engine": "weasyprint",
            },
            ReportFormat.LATEX: {
                "mime_type": "application/x-latex",
                "file_extension": ".tex",
                "encoding": "utf-8",
                "supports_interactive": False,
                "supports_css": False,
                "supports_images": True,
                "compile_to_pdf": True,
            },
            ReportFormat.DOCX: {
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "file_extension": ".docx",
                "encoding": "binary",
                "supports_interactive": False,
                "supports_css": False,
                "supports_images": True,
            },
            ReportFormat.MARKDOWN: {
                "mime_type": "text/markdown",
                "file_extension": ".md",
                "encoding": "utf-8",
                "supports_interactive": False,
                "supports_css": False,
                "supports_images": True,
            },
            ReportFormat.JSON: {
                "mime_type": "application/json",
                "file_extension": ".json",
                "encoding": "utf-8",
                "supports_interactive": False,
                "supports_css": False,
                "supports_images": False,
            }
        }
    
    def get_format_config(self, format: ReportFormat) -> dict[str, Any]:
        """Get configuration for a specific format."""
        return self._format_configs.get(format, {})
    
    def get_mime_type(self, format: ReportFormat) -> str:
        config = self.get_format_config(format)
        result: str = config.get("mime_type", "application/octet-stream")
        return result

    def get_file_extension(self, format: ReportFormat) -> str:
        config = self.get_format_config(format)
        result: str = config.get("file_extension", ".txt")
        return result

    def get_encoding(self, format: ReportFormat) -> str:
        config = self.get_format_config(format)
        result: str = config.get("encoding", "utf-8")
        return result

    def supports_interactive_elements(self, format: ReportFormat) -> bool:
        config = self.get_format_config(format)
        result: bool = config.get("supports_interactive", False)
        return result

    def supports_css_styling(self, format: ReportFormat) -> bool:
        config = self.get_format_config(format)
        result: bool = config.get("supports_css", False)
        return result

    def supports_images(self, format: ReportFormat) -> bool:
        config = self.get_format_config(format)
        result: bool = config.get("supports_images", True)
        return result


class ReportQualityConfig:
    """Configuration for report quality assessment."""
    
    def __init__(self, settings: ReportSettings):
        """Initialize quality configuration."""
        self._settings = settings
    
    def get_min_word_count(self, report_type: ReportType) -> int:
        """Get minimum word count for a report type."""
        minimums = {
            ReportType.EXECUTIVE_SUMMARY: 300,
            ReportType.LITERATURE_REVIEW: 1500,
            ReportType.METHODOLOGY_REPORT: 800,
            ReportType.SYNTHESIS_REPORT: 1000,
            ReportType.ACADEMIC_PAPER: 2500,
            ReportType.COMPREHENSIVE: 2000,
        }
        return minimums.get(report_type, self._settings.min_word_count)
    
    def get_min_sources(self, report_type: ReportType) -> int:
        """Get minimum number of sources for a report type."""
        minimums = {
            ReportType.EXECUTIVE_SUMMARY: 3,
            ReportType.LITERATURE_REVIEW: 15,
            ReportType.METHODOLOGY_REPORT: 5,
            ReportType.SYNTHESIS_REPORT: 8,
            ReportType.ACADEMIC_PAPER: 20,
            ReportType.COMPREHENSIVE: 10,
        }
        return minimums.get(report_type, self._settings.min_sources)
    
    def requires_citations(self, report_type: ReportType) -> bool:
        """Check if citations are required for a report type."""
        return self._settings.require_citations
    
    def get_quality_thresholds(self, report_type: ReportType) -> dict[str, float]:
        """Get quality score thresholds for a report type."""
        return {
            "excellent": 0.9,
            "good": 0.75,
            "acceptable": 0.6,
            "poor": 0.4,
            "minimum": 0.3,
        }


def create_report_settings() -> ReportSettings:
    """Factory function to create report settings."""
    return ReportSettings()


def create_template_config(settings: ReportSettings | None[ReportSettings] = None) -> ReportTemplateConfig:
    """Factory function to create template configuration."""
    if settings is None:
        settings = create_report_settings()
    return ReportTemplateConfig(settings)


def create_format_config(settings: ReportSettings | None[ReportSettings] = None) -> ReportFormatConfig:
    """Factory function to create format configuration."""
    if settings is None:
        settings = create_report_settings()
    return ReportFormatConfig(settings)


def create_quality_config(settings: ReportSettings | None[ReportSettings] = None) -> ReportQualityConfig:
    """Factory function to create quality configuration."""
    if settings is None:
        settings = create_report_settings()
    return ReportQualityConfig(settings)


__all__ = [
    "ReportFormatConfig",
    "ReportQualityConfig",
    "ReportSettings",
    "ReportTemplateConfig",
    "create_format_config",
    "create_quality_config",
    "create_report_settings",
    "create_template_config",
]