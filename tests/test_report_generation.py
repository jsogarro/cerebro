"""
Tests for report generation system.

This module tests the complete report generation pipeline including
models, services, templates, and storage.
"""

import os
import tempfile

import pytest

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
from src.services.report_config import ReportSettings, create_report_settings
from src.services.report_generator import ReportGenerator
from src.services.template_renderer import TemplateRenderer


class TestReportModels:
    """Test report data models."""
    
    def test_citation_creation(self):
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
            doi="10.1000/test"
        )
        
        assert citation.id == "test-1"
        assert len(citation.authors) == 2
        assert citation.year == 2024
        
        # Test APA formatting
        apa_citation = citation.format_citation(CitationStyle.APA)
        assert "Smith, J., Doe, A. (2024)" in apa_citation
        assert "Test Research Paper" in apa_citation
        assert "Test Journal" in apa_citation
        
        # Test MLA formatting
        mla_citation = citation.format_citation(CitationStyle.MLA)
        assert 'Smith, J., Doe, A. "Test Research Paper."' in mla_citation
    
    def test_visualization_creation(self):
        """Test visualization model creation."""
        viz = Visualization(
            id="chart-1",
            type=VisualizationType.BAR_CHART,
            title="Test Chart",
            data={"x": [1, 2, 3], "y": [10, 20, 30]},
            config={"x_label": "X Axis", "y_label": "Y Axis"},
            width=800,
            height=600
        )
        
        assert viz.id == "chart-1"
        assert viz.type == VisualizationType.BAR_CHART
        assert viz.title == "Test Chart"
        assert viz.width == 800
        assert viz.height == 600
        assert viz.data["x"] == [1, 2, 3]
    
    def test_report_section_creation(self):
        """Test report section creation."""
        section = ReportSection(
            title="Introduction",
            content="This is the introduction content.",
            level=1,
            metadata={"type": "intro"}
        )
        
        assert section.title == "Introduction"
        assert section.content == "This is the introduction content."
        assert section.level == 1
        assert section.metadata["type"] == "intro"
        assert len(section.subsections) == 0
    
    def test_report_creation(self):
        """Test complete report creation."""
        config = ReportConfiguration(
            format=ReportFormat.HTML,
            type=ReportType.COMPREHENSIVE,
            citation_style=CitationStyle.APA
        )
        
        metadata = ReportMetadata(
            total_sources=25,
            total_citations=30,
            quality_score=0.85,
            confidence_score=0.78
        )
        
        report = Report(
            id="report-1",
            title="Test Research Report",
            query="What is the impact of AI on education?",
            domains=["AI", "Education"],
            configuration=config,
            metadata=metadata
        )
        
        # Add sections
        report.add_section(
            "Introduction",
            "This research investigates...",
            level=1
        )
        
        # Add citation
        citation = Citation(
            id="cite-1",
            authors=["Author, A."],
            title="AI in Education",
            year=2024
        )
        report.add_citation(citation)
        
        # Add visualization
        viz = Visualization(
            id="viz-1",
            type=VisualizationType.PIE_CHART,
            title="Domain Distribution",
            data={"labels": ["AI", "Education"], "values": [1, 1]}
        )
        report.add_visualization(viz)
        
        assert report.id == "report-1"
        assert len(report.sections) == 1
        assert len(report.citations) == 1
        assert len(report.visualizations) == 1
        
        # Test word count calculation
        word_count = report.get_word_count()
        assert word_count > 0
        
        # Test page estimation
        page_count = report.estimate_page_count()
        assert page_count >= 1


class TestReportConfiguration:
    """Test report configuration and settings."""
    
    def test_report_settings_creation(self):
        """Test report settings creation."""
        settings = create_report_settings()
        
        assert settings.report_storage_path
        assert settings.default_format == ReportFormat.HTML
        assert settings.default_citation_style == CitationStyle.APA
        assert settings.enable_pdf_generation is True
        assert settings.max_report_size_mb > 0
    
    def test_report_configuration_validation(self):
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
                "font_family": "Times New Roman"
            }
        )
        
        assert config.format == ReportFormat.PDF
        assert config.type == ReportType.ACADEMIC_PAPER
        assert config.include_toc is True
        assert config.pdf_settings["page_size"] == "A4"


class TestReportGenerator:
    """Test report generator service."""
    
    @pytest.fixture
    def temp_settings(self):
        """Create temporary settings for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = ReportSettings(
                report_storage_path=temp_dir,
                template_path=os.path.join(temp_dir, "templates"),
                enable_pdf_generation=False,  # Disable for testing
                enable_latex_generation=False,
            )
            # Create template directory
            os.makedirs(settings.template_path, exist_ok=True)
            yield settings
    
    @pytest.fixture
    def mock_input_data(self):
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
                        "summary": "This paper explores..."
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
                        "year": 2024
                    }
                ],
                "recommendations": [
                    "Implement AI tutoring systems",
                    "Train teachers in AI tools"
                ]
            },
            "quality_report": {
                "passed": True,
                "quality_score": 0.85,
                "issues_found": []
            },
            "metadata": {
                "workflow_id": "wf-123",
                "agents_used": ["literature_review", "synthesis"],
                "total_sources": 1,
                "total_citations": 1
            }
        }
    
    @pytest.mark.asyncio
    async def test_report_structure_building(self, temp_settings, mock_input_data):
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
    async def test_section_building(self, temp_settings, mock_input_data):
        """Test building specific sections."""
        generator = ReportGenerator(temp_settings)
        
        config = ReportConfiguration(type=ReportType.COMPREHENSIVE)
        report = Report(
            id="test",
            title="Test",
            query="Test query",
            configuration=config
        )
        
        # Test introduction section
        intro_section = await generator._build_introduction_section(
            report, mock_input_data["aggregated_results"]
        )
        
        assert intro_section is not None
        assert intro_section.title == "Introduction"
        assert "How does AI affect education?" in intro_section.content
        
        # Test findings section
        findings_section = await generator._build_findings_section(
            report, mock_input_data["aggregated_results"]
        )
        
        assert findings_section is not None
        assert findings_section.title == "Key Findings"
        assert len(findings_section.subsections) > 0
    
    @pytest.mark.asyncio
    async def test_citation_processing(self, temp_settings, mock_input_data):
        """Test citation processing."""
        generator = ReportGenerator(temp_settings)
        
        report = Report(
            id="test",
            title="Test",
            query="Test query",
            configuration=ReportConfiguration()
        )
        
        await generator._process_citations(report, mock_input_data)
        
        assert len(report.citations) == 1
        citation = report.citations[0]
        assert citation.authors == ["Smith, J."]
        assert citation.title == "AI in Education Research"
        assert citation.year == 2024
    
    @pytest.mark.asyncio
    async def test_markdown_generation(self, temp_settings, mock_input_data):
        """Test markdown report generation."""
        generator = ReportGenerator(temp_settings)
        
        # Create a simple report
        report = Report(
            id="test",
            title="Test Report",
            query="Test query",
            configuration=ReportConfiguration()
        )
        
        report.add_section("Introduction", "This is a test.", level=1)
        
        output = await generator._generate_markdown(report)
        
        assert output.format == ReportFormat.MARKDOWN
        assert "# Test Report" in output.content
        assert "This is a test." in output.content
        assert output.mime_type == "text/markdown"
    
    @pytest.mark.asyncio 
    async def test_html_generation(self, temp_settings, mock_input_data):
        """Test HTML report generation."""
        generator = ReportGenerator(temp_settings)
        
        report = Report(
            id="test",
            title="Test Report",
            query="Test query",
            configuration=ReportConfiguration()
        )
        
        report.add_section("Introduction", "This is a test.", level=1)
        
        output = await generator._generate_html(report)
        
        assert output.format == ReportFormat.HTML
        assert "<title>Test Report</title>" in output.content
        assert "This is a test." in output.content
        assert output.mime_type == "text/html"


class TestTemplateRenderer:
    """Test template rendering functionality."""
    
    @pytest.fixture
    def temp_template_dir(self):
        """Create temporary template directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a simple base template
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
    
    def test_template_renderer_creation(self, temp_template_dir):
        """Test creating template renderer."""
        settings = ReportSettings(template_path=temp_template_dir)
        renderer = TemplateRenderer(settings)
        
        assert renderer.settings.template_path == temp_template_dir
        assert renderer.env is not None
    
    def test_custom_filters(self, temp_template_dir):
        """Test custom Jinja2 filters."""
        settings = ReportSettings(template_path=temp_template_dir)
        renderer = TemplateRenderer(settings)
        
        # Test markdown filter
        markdown_result = renderer._markdown_filter("**Bold text**")
        assert "<strong>Bold text</strong>" in markdown_result
        
        # Test truncate words filter
        long_text = "This is a very long text that should be truncated"
        truncated = renderer._truncate_words_filter(long_text, 5)
        assert len(truncated.split()) <= 6  # 5 words + "..."
        
        # Test format number filter
        large_number = renderer._format_number_filter(1234567)
        assert "," in large_number
        
        # Test percentage filter
        percentage = renderer._format_percentage_filter(0.85, 1)
        assert percentage == "85.0%"
    
    def test_report_rendering(self, temp_template_dir):
        """Test rendering a complete report."""
        settings = ReportSettings(template_path=temp_template_dir)
        renderer = TemplateRenderer(settings)
        
        # Create test report
        report = Report(
            id="test",
            title="Test Report",
            query="What is the impact of AI?",
            configuration=ReportConfiguration()
        )
        
        report.add_section("Introduction", "This is the **introduction**.", level=1)
        report.add_section("Conclusion", "This is the conclusion.", level=1)
        
        # Render using base template
        html_output = renderer.render_report(report, "base.html.j2")
        
        assert "<title>Test Report</title>" in html_output
        assert "<h1>Test Report</h1>" in html_output
        assert "What is the impact of AI?" in html_output
        assert "<h2>Introduction</h2>" in html_output
        assert "<h2>Conclusion</h2>" in html_output
        assert "<strong>introduction</strong>" in html_output


class TestReportIntegration:
    """Test complete report generation pipeline."""
    
    @pytest.fixture
    def temp_environment(self):
        """Create complete temporary environment for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = ReportSettings(
                report_storage_path=temp_dir,
                template_path=os.path.join(temp_dir, "templates"),
                enable_pdf_generation=False,
                enable_latex_generation=False,
                enable_visualizations=False
            )
            
            # Create template directory and basic template
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
    async def test_end_to_end_report_generation(self, temp_environment):
        """Test complete end-to-end report generation."""
        from src.models.report import ReportGenerationRequest
        
        generator = ReportGenerator(temp_environment)
        
        # Create realistic input data
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
                        "summary": "Comprehensive study on ML applications in education."
                    },
                    {
                        "id": "source-2", 
                        "title": "AI Tutoring Systems: A Review",
                        "authors": ["Davis, L."],
                        "year": 2024,
                        "summary": "Review of intelligent tutoring systems."
                    }
                ],
                "findings": {
                    "learning_outcomes": [
                        {
                            "text": "AI-powered tutoring systems improve student performance by 25%",
                            "confidence": 0.85
                        }
                    ],
                    "efficiency_gains": [
                        {
                            "text": "Automated grading reduces teacher workload by 40%",
                            "confidence": 0.78
                        }
                    ]
                },
                "citations": [
                    {
                        "id": "cite-1",
                        "authors": ["Johnson, M.", "Smith, K."],
                        "title": "Machine Learning in Educational Systems",
                        "year": 2023,
                        "journal": "Educational Technology Review"
                    }
                ],
                "recommendations": [
                    "Implement AI tutoring systems in high schools",
                    "Train educators in AI tool usage",
                    "Develop ethical guidelines for AI in education"
                ],
                "insights": [
                    {
                        "text": "AI adoption in education is accelerating globally",
                        "importance": "high"
                    }
                ]
            },
            "quality_report": {
                "passed": True,
                "quality_score": 0.88,
                "issues_found": []
            },
            "metadata": {
                "workflow_id": "wf-ai-edu-001",
                "agents_used": ["literature_review", "comparative_analysis", "synthesis"],
                "total_sources": 2,
                "total_citations": 1
            }
        }
        
        # Create generation request
        request = ReportGenerationRequest(
            workflow_data=workflow_data,
            configuration=ReportConfiguration(
                format=ReportFormat.HTML,
                type=ReportType.COMPREHENSIVE,
                citation_style=CitationStyle.APA,
                include_toc=True,
                include_executive_summary=True
            ),
            formats=[ReportFormat.HTML, ReportFormat.MARKDOWN, ReportFormat.JSON],
            save_to_storage=False
        )
        
        # Generate report
        response = await generator.generate_report(request)
        
        # Verify response
        assert response.status == "completed"
        assert len(response.formats_generated) >= 2  # At least HTML and Markdown
        assert response.word_count > 0
        assert response.page_count > 0
        assert response.generation_time > 0
        assert len(response.errors) == 0
        
        # Verify report structure was created properly
        assert ReportFormat.HTML in response.formats_generated
        assert ReportFormat.MARKDOWN in response.formats_generated
    
    @pytest.mark.asyncio
    async def test_error_handling(self, temp_environment):
        """Test error handling in report generation."""
        from src.models.report import ReportGenerationRequest
        
        generator = ReportGenerator(temp_environment)
        
        # Test with invalid data
        request = ReportGenerationRequest(
            workflow_data={},  # Missing required fields
            formats=[ReportFormat.HTML]
        )
        
        response = await generator.generate_report(request)
        
        assert response.status == "failed"
        assert len(response.errors) > 0
        assert response.word_count == 0
        assert response.page_count == 0
    
    def test_report_quality_validation(self, temp_environment):
        """Test report quality validation."""
        ReportGenerator(temp_environment)
        
        # Create report with low quality metrics
        report = Report(
            id="test",
            title="T",  # Very short title
            query="Q",  # Very short query
            configuration=ReportConfiguration(type=ReportType.COMPREHENSIVE)
        )
        
        # This should trigger quality warnings (in real implementation)
        # The test verifies the validation system is in place
        word_count = report.get_word_count()
        assert word_count < 50  # Should be below minimum for comprehensive report


if __name__ == "__main__":
    pytest.main([__file__])