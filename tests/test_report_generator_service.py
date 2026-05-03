"""Unit-style tests for the report generator service."""

import os
import tempfile
from collections.abc import Generator
from typing import Any

import pytest

from src.models.report import (
    Report,
    ReportConfiguration,
    ReportFormat,
    ReportType,
)
from src.services.report_config import ReportSettings
from src.services.report_generator import ReportGenerator


class TestReportGenerator:
    """Test report generator service."""

    @pytest.fixture
    def temp_settings(self) -> Generator[ReportSettings]:
        """Create temporary settings for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = ReportSettings(
                report_storage_path=temp_dir,
                template_path=os.path.join(temp_dir, "templates"),
                enable_pdf_generation=False,
                enable_latex_generation=False,
            )
            os.makedirs(settings.template_path, exist_ok=True)
            yield settings

    @pytest.fixture
    def mock_input_data(self) -> dict[str, Any]:
        """Create mock input data for testing."""
        return {
            "title": "Test Research Report",
            "query": "How does AI affect education?",
            "domains": ["AI", "Education"],
            "aggregated_results": {
                "sources": [
                    {
                        "id": "source-1",
                        "title": "AI in Education Research",
                        "authors": ["Smith, J."],
                        "year": 2024,
                        "summary": "This paper explores...",
                    }
                ],
                "findings": {
                    "key_insights": [
                        {"text": "AI improves learning outcomes", "confidence": 0.8}
                    ]
                },
                "citations": [
                    {
                        "id": "cite-1",
                        "authors": ["Smith, J."],
                        "title": "AI in Education Research",
                        "year": 2024,
                    }
                ],
                "recommendations": [
                    "Implement AI tutoring systems",
                    "Train teachers in AI tools",
                ],
            },
            "quality_report": {
                "passed": True,
                "quality_score": 0.85,
                "issues_found": [],
            },
            "metadata": {
                "workflow_id": "wf-123",
                "agents_used": ["literature_review", "synthesis"],
                "total_sources": 1,
                "total_citations": 1,
            },
        }

    @pytest.mark.asyncio
    async def test_report_structure_building(
        self, temp_settings: ReportSettings, mock_input_data: dict[str, Any]
    ) -> None:
        """Test building report structure from input data."""
        generator = ReportGenerator(temp_settings)

        config = ReportConfiguration(type=ReportType.COMPREHENSIVE)

        report = await generator._build_report_structure(
            mock_input_data, config, "test-report-1"
        )

        assert report.id == "test-report-1"
        assert report.title == "Test Research Report"
        assert report.query == "How does AI affect education?"
        assert "AI" in report.domains
        assert "Education" in report.domains
        assert len(report.sections) > 0
        assert len(report.citations) == 1
        assert report.metadata.total_sources == 1

    @pytest.mark.asyncio
    async def test_section_building(
        self, temp_settings: ReportSettings, mock_input_data: dict[str, Any]
    ) -> None:
        """Test building specific sections."""
        generator = ReportGenerator(temp_settings)

        config = ReportConfiguration(type=ReportType.COMPREHENSIVE)
        report = Report(
            id="test",
            title="Test",
            query="Test query",
            configuration=config,
        )

        intro_section = await generator._build_introduction_section(
            report, mock_input_data["aggregated_results"]
        )

        assert intro_section is not None
        assert intro_section.title == "Introduction"
        assert report.query in intro_section.content

        findings_section = await generator._build_findings_section(
            report, mock_input_data["aggregated_results"]
        )

        assert findings_section is not None
        assert findings_section.title == "Key Findings"
        assert len(findings_section.subsections) > 0

    @pytest.mark.asyncio
    async def test_citation_processing(
        self, temp_settings: ReportSettings, mock_input_data: dict[str, Any]
    ) -> None:
        """Test citation processing."""
        generator = ReportGenerator(temp_settings)

        report = Report(
            id="test",
            title="Test",
            query="Test query",
            configuration=ReportConfiguration(),
        )

        await generator._process_citations(report, mock_input_data)

        assert len(report.citations) == 1
        citation = report.citations[0]
        assert citation.authors == ["Smith, J."]
        assert citation.title == "AI in Education Research"
        assert citation.year == 2024

    @pytest.mark.asyncio
    async def test_markdown_generation(
        self, temp_settings: ReportSettings, mock_input_data: dict[str, Any]
    ) -> None:
        """Test markdown report generation."""
        generator = ReportGenerator(temp_settings)

        report = Report(
            id="test",
            title="Test Report",
            query="Test query",
            configuration=ReportConfiguration(),
        )

        report.add_section("Introduction", "This is a test.", level=1)

        output = await generator._generate_markdown(report)

        assert output.format == ReportFormat.MARKDOWN
        assert "# Test Report" in output.content
        assert "This is a test." in output.content
        assert output.mime_type == "text/markdown"

    @pytest.mark.asyncio
    async def test_html_generation(
        self, temp_settings: ReportSettings, mock_input_data: dict[str, Any]
    ) -> None:
        """Test HTML report generation."""
        generator = ReportGenerator(temp_settings)

        report = Report(
            id="test",
            title="Test Report",
            query="Test query",
            configuration=ReportConfiguration(),
        )

        report.add_section("Introduction", "This is a test.", level=1)

        output = await generator._generate_html(report)

        assert output.format == ReportFormat.HTML
        assert "<title>Test Report</title>" in output.content
        assert "This is a test." in output.content
        assert output.mime_type == "text/html"
