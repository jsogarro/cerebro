"""End-to-end tests for the report generation pipeline."""

import os
import tempfile
from collections.abc import Generator

import pytest

from src.models.report import (
    CitationStyle,
    Report,
    ReportConfiguration,
    ReportFormat,
    ReportGenerationRequest,
    ReportType,
)
from src.services.report_config import ReportSettings
from src.services.report_generator import ReportGenerator


class TestReportIntegration:
    """Test complete report generation pipeline."""

    @pytest.fixture
    def temp_environment(self) -> Generator[ReportSettings]:
        """Create complete temporary environment for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = ReportSettings(
                report_storage_path=temp_dir,
                template_path=os.path.join(temp_dir, "templates"),
                enable_pdf_generation=False,
                enable_latex_generation=False,
                enable_visualizations=False,
            )

            os.makedirs(settings.template_path, exist_ok=True)

            base_template = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ report.title }}</title>
</head>
<body>
    <h1>{{ report.title }}</h1>
    <p>{{ report.query }}</p>
    {% for section in report.sections %}
    <h2>{{ section.title }}</h2>
    <div>{{ section.content }}</div>
    {% endfor %}
</body>
</html>
            """.strip()

            with open(os.path.join(settings.template_path, "base.html.j2"), "w") as f:
                f.write(base_template)

            yield settings

    @pytest.mark.asyncio
    async def test_end_to_end_report_generation(
        self, temp_environment: ReportSettings
    ) -> None:
        """Test complete end-to-end report generation."""
        generator = ReportGenerator(temp_environment)

        workflow_data = {
            "title": "AI Impact Assessment",
            "query": "How does artificial intelligence impact modern education?",
            "domains": ["Artificial Intelligence", "Education", "Technology"],
            "aggregated_results": {
                "sources": [
                    {
                        "id": "source-1",
                        "title": "Machine Learning in Educational Systems",
                        "authors": ["Johnson, M.", "Smith, K."],
                        "year": 2023,
                        "summary": "Comprehensive study on ML applications in education.",
                    },
                    {
                        "id": "source-2",
                        "title": "AI Tutoring Systems: A Review",
                        "authors": ["Davis, L."],
                        "year": 2024,
                        "summary": "Review of intelligent tutoring systems.",
                    },
                ],
                "findings": {
                    "learning_outcomes": [
                        {
                            "text": (
                                "AI-powered tutoring systems improve student "
                                "performance by 25%"
                            ),
                            "confidence": 0.85,
                        }
                    ],
                    "efficiency_gains": [
                        {
                            "text": (
                                "Automated grading reduces teacher workload by 40%"
                            ),
                            "confidence": 0.78,
                        }
                    ],
                },
                "citations": [
                    {
                        "id": "cite-1",
                        "authors": ["Johnson, M.", "Smith, K."],
                        "title": "Machine Learning in Educational Systems",
                        "year": 2023,
                        "journal": "Educational Technology Review",
                    }
                ],
                "recommendations": [
                    "Implement AI tutoring systems in high schools",
                    "Train educators in AI tool usage",
                    "Develop ethical guidelines for AI in education",
                ],
                "insights": [
                    {
                        "text": "AI adoption in education is accelerating globally",
                        "importance": "high",
                    }
                ],
            },
            "quality_report": {
                "passed": True,
                "quality_score": 0.88,
                "issues_found": [],
            },
            "metadata": {
                "workflow_id": "wf-ai-edu-001",
                "agents_used": [
                    "literature_review",
                    "comparative_analysis",
                    "synthesis",
                ],
                "total_sources": 2,
                "total_citations": 1,
            },
        }

        request = ReportGenerationRequest(
            workflow_data=workflow_data,
            configuration=ReportConfiguration(
                format=ReportFormat.HTML,
                type=ReportType.COMPREHENSIVE,
                citation_style=CitationStyle.APA,
                include_toc=True,
                include_executive_summary=True,
            ),
            formats=[ReportFormat.HTML, ReportFormat.MARKDOWN, ReportFormat.JSON],
            save_to_storage=False,
        )

        response = await generator.generate_report(request)

        assert response.status == "completed"
        assert len(response.formats_generated) >= 2
        assert response.word_count > 0
        assert response.page_count > 0
        assert response.generation_time > 0
        assert len(response.errors) == 0
        assert ReportFormat.HTML in response.formats_generated
        assert ReportFormat.MARKDOWN in response.formats_generated

    @pytest.mark.asyncio
    async def test_error_handling(self, temp_environment: ReportSettings) -> None:
        """Test error handling in report generation."""
        generator = ReportGenerator(temp_environment)

        request = ReportGenerationRequest(
            workflow_data={},
            formats=[ReportFormat.HTML],
        )

        response = await generator.generate_report(request)

        assert response.status == "failed"
        assert len(response.errors) > 0
        assert response.word_count == 0
        assert response.page_count == 0

    def test_report_quality_validation(self, temp_environment: ReportSettings) -> None:
        """Test report quality validation."""
        ReportGenerator(temp_environment)

        report = Report(
            id="test",
            title="T",
            query="Q",
            configuration=ReportConfiguration(type=ReportType.COMPREHENSIVE),
        )

        word_count = report.get_word_count()
        assert word_count < 50
