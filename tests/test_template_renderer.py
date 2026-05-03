"""Tests for report template rendering."""

import os
import tempfile
from collections.abc import Generator

import pytest

from src.models.report import Report, ReportConfiguration
from src.services.report_config import ReportSettings
from src.services.template_renderer import TemplateRenderer


class TestTemplateRenderer:
    """Test template rendering functionality."""

    @pytest.fixture
    def temp_template_dir(self) -> Generator[str]:
        """Create temporary template directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base_template = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ report.title }}</title>
</head>
<body>
    <h1>{{ report.title }}</h1>
    <div class="query">{{ report.query }}</div>
    {% for section in report.sections %}
    <div class="section">
        <h2>{{ section.title }}</h2>
        <div>{{ section.content | markdown | safe }}</div>
    </div>
    {% endfor %}
</body>
</html>
            """.strip()

            with open(os.path.join(temp_dir, "base.html.j2"), "w") as f:
                f.write(base_template)

            yield temp_dir

    def test_template_renderer_creation(self, temp_template_dir: str) -> None:
        """Test creating template renderer."""
        settings = ReportSettings(template_path=temp_template_dir)
        renderer = TemplateRenderer(settings)

        assert renderer.settings.template_path == temp_template_dir
        assert renderer.env is not None

    def test_custom_filters(self, temp_template_dir: str) -> None:
        """Test custom Jinja2 filters."""
        settings = ReportSettings(template_path=temp_template_dir)
        renderer = TemplateRenderer(settings)

        markdown_result = renderer._markdown_filter("**Bold text**")
        assert "<strong>Bold text</strong>" in markdown_result

        long_text = "This is a very long text that should be truncated"
        truncated = renderer._truncate_words_filter(long_text, 5)
        assert len(truncated.split()) <= 6

        large_number = renderer._format_number_filter(1234567)
        assert "," in large_number

        percentage = renderer._format_percentage_filter(0.85, 1)
        assert percentage == "85.0%"

    def test_report_rendering(self, temp_template_dir: str) -> None:
        """Test rendering a complete report."""
        settings = ReportSettings(template_path=temp_template_dir)
        renderer = TemplateRenderer(settings)

        report = Report(
            id="test",
            title="Test Report",
            query="What is the impact of AI?",
            configuration=ReportConfiguration(),
        )

        report.add_section("Introduction", "This is the **introduction**.", level=1)
        report.add_section("Conclusion", "This is the conclusion.", level=1)

        html_output = renderer.render_report(report, "base.html.j2")

        assert "<title>Test Report</title>" in html_output
        assert "<h1>Test Report</h1>" in html_output
        assert "What is the impact of AI?" in html_output
        assert "<h2>Introduction</h2>" in html_output
        assert "<h2>Conclusion</h2>" in html_output
        assert "<strong>introduction</strong>" in html_output
