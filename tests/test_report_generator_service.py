"""Unit-style tests for the report generator service."""

import os
import tempfile
from collections.abc import Generator
from pathlib import Path
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


@pytest.fixture(autouse=True)
def isolate_dotenv_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import src.core.environment as shared_environment

    monkeypatch.setattr(
        shared_environment,
        "PROJECT_ENV_PATH",
        tmp_path / "fake-project" / ".env",
    )
    monkeypatch.setattr(
        shared_environment,
        "HOME_ENV_PATH",
        tmp_path / "fake-home" / ".env",
    )


class TestReportGenerator:
    """Test report generator service."""

    def test_explicit_report_setting_beats_ambient_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REPORT_TEMPLATE_PATH", "/ambient/templates")

        settings = ReportSettings(template_path="/explicit/templates")

        assert settings.template_path == "/explicit/templates"

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


TEMPLATES = Path(__file__).parents[1] / "src" / "templates" / "reports"


class TestTruthfulReportInput:
    """Unknown quality stays unknown; nothing is defaulted to a passing grade."""

    @pytest.fixture
    def generator(self, tmp_path: Path) -> ReportGenerator:
        settings = ReportSettings(
            report_storage_path=str(tmp_path),
            template_path=str(TEMPLATES),
            enable_pdf_generation=False,
            enable_latex_generation=False,
        )
        return ReportGenerator(settings)

    @pytest.mark.asyncio
    async def test_direct_workflow_data_without_quality_is_not_graded(
        self, generator: ReportGenerator
    ) -> None:
        from src.models.report import ReportGenerationRequest

        request = ReportGenerationRequest(
            workflow_data={"title": "T", "query": "q", "domains": []}
        )

        input_data = await generator._extract_input_data(request)
        report = await generator._build_report_structure(
            input_data, ReportConfiguration(), "r1"
        )

        assert "quality_report" not in input_data
        assert report.metadata.quality_score is None
        assert report.metadata.confidence_score is None
        assert report.metadata.source_kind == "user_supplied"
        assert report.metadata.evaluation_availability == "not_attempted"

    @pytest.mark.asyncio
    async def test_unknown_quality_is_explicit_in_every_output(
        self, generator: ReportGenerator
    ) -> None:
        from src.models.report import ReportGenerationRequest

        request = ReportGenerationRequest(
            workflow_data={"title": "T", "query": "q", "domains": []}
        )
        report = await generator._build_report_structure(
            await generator._extract_input_data(request), ReportConfiguration(), "r1"
        )

        markdown = (await generator._generate_markdown(report)).content
        basic_html = generator._generate_basic_html(report)
        rendered_html = (await generator._generate_html(report)).content
        json_text = (await generator._generate_json(report)).content

        assert "Not evaluated" in markdown
        assert "Not evaluated" in basic_html
        assert "Not evaluated" in rendered_html
        assert '"quality_score": null' in json_text
        for text in (markdown, basic_html, rendered_html):
            assert "0.0%" not in text and "0.00%" not in text and "80.0%" not in text

    @pytest.mark.asyncio
    async def test_user_supplied_quality_is_kept_and_labelled(
        self, generator: ReportGenerator
    ) -> None:
        from src.models.report import ReportGenerationRequest

        request = ReportGenerationRequest(
            workflow_data={
                "title": "T",
                "query": "q",
                "quality_report": {"quality_score": 0.42},
            }
        )
        report = await generator._build_report_structure(
            await generator._extract_input_data(request), ReportConfiguration(), "r1"
        )

        assert report.metadata.quality_score == 0.42
        assert report.metadata.source_kind == "user_supplied"

    @pytest.mark.asyncio
    async def test_project_projection_feeds_the_report_and_is_labelled(
        self, generator: ReportGenerator
    ) -> None:
        from uuid import uuid4

        from src.models.report import ReportGenerationRequest
        from src.services.report_source_projection import (
            EvaluationAvailability,
            ReportSourceProjection,
            SourceKind,
        )

        projection = ReportSourceProjection(
            source_kind=SourceKind.PROJECT_RESULTS,
            evaluation=EvaluationAvailability.NOT_ATTEMPTED,
            input_data={
                "title": "Stored title",
                "query": "STORED-QUERY-SENTINEL",
                "domains": ["A", "B"],
                "aggregated_results": {"sources": []},
            },
        )
        request = ReportGenerationRequest(project_id=uuid4())

        input_data = await generator._extract_input_data(request, projection)
        report = await generator._build_report_structure(
            input_data, ReportConfiguration(), "r1"
        )

        assert report.query == "STORED-QUERY-SENTINEL"
        assert report.metadata.source_kind == "project_results"
        assert report.metadata.quality_score is None

    @pytest.mark.asyncio
    async def test_project_request_without_loaded_projection_is_refused(
        self, generator: ReportGenerator
    ) -> None:
        from uuid import uuid4

        from src.models.report import ReportGenerationRequest
        from src.services.report_generator import ReportGenerationError

        request = ReportGenerationRequest(
            project_id=uuid4(),
            workflow_data={"title": "caller", "query": "SAMPLE-CALLER-QUERY"},
        )

        with pytest.raises(ReportGenerationError):
            await generator._extract_input_data(request)


class TestDomainVisualization:
    """The domain pie plots measured counts, never a constant 1 per label."""

    @pytest.mark.asyncio
    async def test_pie_values_are_source_counts_per_domain(
        self, tmp_path: Path
    ) -> None:
        settings = ReportSettings(
            report_storage_path=str(tmp_path), template_path=str(tmp_path / "t")
        )
        generator = ReportGenerator(settings)
        report = Report(
            id="r",
            title="T",
            query="q",
            domains=["ai", "edu"],
            configuration=ReportConfiguration(),
        )
        input_data = {
            "aggregated_results": {
                "sources": [
                    {"title": "a", "domain": "ai"},
                    {"title": "b", "domain": "ai"},
                    {"title": "c", "domain": "ai"},
                    {"title": "d", "domain": "edu"},
                ]
            }
        }

        await generator._generate_visualizations(report, input_data)

        pie = next(v for v in report.visualizations if v.id == "domain_distribution")
        assert dict(zip(pie.data["labels"], pie.data["values"], strict=True)) == {
            "ai": 3,
            "edu": 1,
        }

    @pytest.mark.asyncio
    async def test_no_pie_when_sources_carry_no_domain(self, tmp_path: Path) -> None:
        settings = ReportSettings(
            report_storage_path=str(tmp_path), template_path=str(tmp_path / "t")
        )
        generator = ReportGenerator(settings)
        report = Report(
            id="r",
            title="T",
            query="q",
            domains=["ai", "edu"],
            configuration=ReportConfiguration(),
        )

        await generator._generate_visualizations(
            report, {"aggregated_results": {"sources": [{"title": "a"}]}}
        )

        assert not [v for v in report.visualizations if v.id == "domain_distribution"]

    @pytest.mark.asyncio
    async def test_html_embeds_generated_chart_and_real_values(
        self, tmp_path: Path
    ) -> None:
        from unittest.mock import patch

        from src.models.report import Visualization, VisualizationType

        settings = ReportSettings(
            report_storage_path=str(tmp_path),
            template_path=str(TEMPLATES),
        )
        generator = ReportGenerator(settings)
        report = Report(
            id="r", title="T", query="q", configuration=ReportConfiguration()
        )
        report.add_visualization(
            Visualization(
                id="domain_distribution",
                type=VisualizationType.PIE_CHART,
                title="Domains",
                data={"labels": ["ai", "edu"], "values": [3, 1]},
                config={},
                caption=None,
                width=None,
                height=None,
            )
        )
        png = {"domain_distribution": {"format": "png", "data": "QUJD"}}

        with patch(
            "src.services.report_output_generator.VisualizationGenerator"
        ) as gen_cls:
            gen_cls.return_value.generate_report_visualizations.return_value = png
            html = (await generator._generate_html(report)).content

        gen_cls.return_value.generate_report_visualizations.assert_called_once()
        assert "data:image/png;base64,QUJD" in html

    @pytest.mark.asyncio
    async def test_html_falls_back_to_a_values_table_without_a_chart_image(
        self, tmp_path: Path
    ) -> None:
        from unittest.mock import patch

        from src.models.report import Visualization, VisualizationType

        settings = ReportSettings(
            report_storage_path=str(tmp_path),
            template_path=str(TEMPLATES),
        )
        generator = ReportGenerator(settings)
        report = Report(
            id="r", title="T", query="q", configuration=ReportConfiguration()
        )
        report.add_visualization(
            Visualization(
                id="domain_distribution",
                type=VisualizationType.PIE_CHART,
                title="Domains",
                data={"labels": ["ai", "<b>edu</b>"], "values": [3, 1]},
                config={},
                caption=None,
                width=None,
                height=None,
            )
        )

        with patch(
            "src.services.report_output_generator.VisualizationGenerator"
        ) as gen_cls:
            gen_cls.return_value.generate_report_visualizations.return_value = {}
            html = (await generator._generate_html(report)).content

        assert "would be rendered here" not in html
        assert "<td>ai</td>" in html and "<td>3</td>" in html
        assert "&lt;b&gt;edu&lt;/b&gt;" in html and "<b>edu</b>" not in html
