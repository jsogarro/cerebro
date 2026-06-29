"""Tests for report models and report configuration."""

from src.models.report import (
    Citation,
    CitationStyle,
    Report,
    ReportConfiguration,
    ReportFormat,
    ReportMetadata,
    ReportSection,
    ReportType,
    Visualization,
    VisualizationType,
)
from src.services.report_config import create_report_settings


class TestReportModels:
    """Test report data models."""

    def test_citation_creation(self) -> None:
        """Test citation model creation and formatting."""
        citation = Citation(
            id="test-1",
            authors=["Smith, J.", "Doe, A."],
            title="Test Research Paper",
            year=2024,
            journal="Test Journal",
            volume="10",
            issue="2",
            pages="123-456",
            doi="10.1000/test",
        )

        assert citation.id == "test-1"
        assert len(citation.authors) == 2
        assert citation.year == 2024

        apa_citation = citation.format_citation(CitationStyle.APA)
        assert "Smith, J., Doe, A. (2024)" in apa_citation
        assert "Test Research Paper" in apa_citation
        assert "Test Journal" in apa_citation

        mla_citation = citation.format_citation(CitationStyle.MLA)
        assert 'Smith, J., Doe, A. "Test Research Paper."' in mla_citation

    def test_visualization_creation(self) -> None:
        """Test visualization model creation."""
        viz = Visualization(
            id="chart-1",
            type=VisualizationType.BAR_CHART,
            title="Test Chart",
            data={"x": [1, 2, 3], "y": [10, 20, 30]},
            config={"x_label": "X Axis", "y_label": "Y Axis"},
            width=800,
            height=600,
        )

        assert viz.id == "chart-1"
        assert viz.type == VisualizationType.BAR_CHART
        assert viz.title == "Test Chart"
        assert viz.width == 800
        assert viz.height == 600
        assert viz.data["x"] == [1, 2, 3]

    def test_report_section_creation(self) -> None:
        """Test report section creation."""
        section = ReportSection(
            title="Introduction",
            content="This is the introduction content.",
            level=1,
            metadata={"type": "intro"},
        )

        assert section.title == "Introduction"
        assert section.content == "This is the introduction content."
        assert section.level == 1
        assert section.metadata["type"] == "intro"
        assert len(section.subsections) == 0

    def test_report_creation(self) -> None:
        """Test complete report creation."""
        config = ReportConfiguration(
            format=ReportFormat.HTML,
            type=ReportType.COMPREHENSIVE,
            citation_style=CitationStyle.APA,
        )

        metadata = ReportMetadata(
            total_sources=25,
            total_citations=30,
            quality_score=0.85,
            confidence_score=0.78,
        )

        report = Report(
            id="report-1",
            title="Test Research Report",
            query="What is the impact of AI on education?",
            domains=["AI", "Education"],
            configuration=config,
            metadata=metadata,
        )

        report.add_section("Introduction", "This research investigates...", level=1)

        citation = Citation(
            id="cite-1",
            authors=["Author, A."],
            title="AI in Education",
            year=2024,
        )
        report.add_citation(citation)

        viz = Visualization(
            id="viz-1",
            type=VisualizationType.PIE_CHART,
            title="Domain Distribution",
            data={"labels": ["AI", "Education"], "values": [1, 1]},
        )
        report.add_visualization(viz)

        assert report.id == "report-1"
        assert len(report.sections) == 1
        assert len(report.citations) == 1
        assert len(report.visualizations) == 1
        assert report.get_word_count() > 0
        assert report.estimate_page_count() >= 1


class TestReportConfiguration:
    """Test report configuration and settings."""

    def test_report_settings_creation(self) -> None:
        """Test report settings creation."""
        settings = create_report_settings()

        assert settings.report_storage_path
        assert settings.default_format == ReportFormat.HTML
        assert settings.default_citation_style == CitationStyle.APA
        assert settings.enable_pdf_generation is True
        assert settings.max_report_size_mb > 0

    def test_report_configuration_validation(self) -> None:
        """Test report configuration validation."""
        config = ReportConfiguration(
            format=ReportFormat.PDF,
            type=ReportType.ACADEMIC_PAPER,
            citation_style=CitationStyle.APA,
            include_toc=True,
            include_visualizations=True,
            pdf_settings={
                "page_size": "A4",
                "margin_top": "2cm",
                "font_family": "Times New Roman",
            },
        )

        assert config.format == ReportFormat.PDF
        assert config.type == ReportType.ACADEMIC_PAPER
        assert config.include_toc is True
        assert config.pdf_settings["page_size"] == "A4"
