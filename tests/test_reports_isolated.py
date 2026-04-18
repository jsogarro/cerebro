"""
Isolated tests for reports API functionality.

This module tests the report API logic without importing the full application,
avoiding SQLAlchemy model conflicts.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.api.routes.reports import (
    CreateReportRequest,
    ReportListResponse,
    ReportResponse,
    ReportSearchRequest,
    ReportStatisticsResponse,
)
from src.models.report import CitationStyle, ReportFormat, ReportType


class TestReportModels:
    """Test report API models."""
    
    def test_create_report_request_validation(self):
        """Test CreateReportRequest validation."""
        # Valid request
        request = CreateReportRequest(
            title="Test Report",
            query="What is the impact of AI on education?",
            domains=["AI", "Education"],
            report_type=ReportType.COMPREHENSIVE,
            citation_style=CitationStyle.APA,
            formats=[ReportFormat.HTML, ReportFormat.MARKDOWN]
        )
        
        assert request.title == "Test Report"
        assert request.query == "What is the impact of AI on education?"
        assert request.domains == ["AI", "Education"]
        assert request.report_type == ReportType.COMPREHENSIVE
        assert request.citation_style == CitationStyle.APA
        assert ReportFormat.HTML in request.formats
        assert ReportFormat.MARKDOWN in request.formats
        
        # Test defaults
        assert request.include_toc is True
        assert request.include_executive_summary is True
        assert request.include_visualizations is True
        assert request.save_to_storage is True
        assert request.notify_completion is False
    
    def test_create_report_request_minimal(self):
        """Test CreateReportRequest with minimal data."""
        request = CreateReportRequest(
            title="Minimal Report",
            query="Simple query"
        )
        
        assert request.title == "Minimal Report"
        assert request.query == "Simple query"
        assert request.domains == []
        assert request.report_type == ReportType.COMPREHENSIVE
        assert request.citation_style == CitationStyle.APA
        assert request.formats == [ReportFormat.HTML, ReportFormat.MARKDOWN]
    
    def test_report_response_creation(self):
        """Test ReportResponse creation."""
        report_id = uuid4()
        
        response = ReportResponse(
            id=report_id,
            title="Test Response",
            query="Test query",
            report_type="comprehensive",
            generation_status="completed",
            formats_generated=["html", "pdf"],
            word_count=1500,
            page_count=5,
            quality_score=0.85,
            confidence_score=0.78,
            created_at="2024-01-01T00:00:00Z",
            generation_time_seconds=45.0,
            download_urls={
                "html": f"/reports/{report_id}/download/html",
                "pdf": f"/reports/{report_id}/download/pdf"
            }
        )
        
        assert response.id == report_id
        assert response.title == "Test Response"
        assert response.generation_status == "completed"
        assert len(response.formats_generated) == 2
        assert "html" in response.formats_generated
        assert "pdf" in response.formats_generated
        assert response.word_count == 1500
        assert response.quality_score == 0.85
        assert len(response.download_urls) == 2
    
    def test_report_response_from_db_report(self):
        """Test creating ReportResponse from mock database report."""
        report_id = uuid4()
        
        # Mock database report
        mock_report = MagicMock()
        mock_report.id = report_id
        mock_report.title = "Test Report"
        mock_report.query = "Test query"
        mock_report.report_type = "comprehensive"
        mock_report.generation_status = "completed"
        mock_report.word_count = 1500
        mock_report.page_count = 5
        mock_report.quality_score = 0.85
        mock_report.confidence_score = 0.78
        mock_report.created_at.isoformat.return_value = "2024-01-01T00:00:00Z"
        mock_report.generation_time_seconds = 45.0
        
        # Mock formats
        mock_format1 = MagicMock()
        mock_format1.format_type = "html"
        mock_format2 = MagicMock()
        mock_format2.format_type = "pdf"
        mock_report.formats = [mock_format1, mock_format2]
        
        response = ReportResponse.from_db_report(mock_report, base_url="http://localhost:8000")
        
        assert response.id == report_id
        assert response.title == "Test Report"
        assert response.generation_status == "completed"
        assert len(response.formats_generated) == 2
        assert "html" in response.formats_generated
        assert "pdf" in response.formats_generated
        assert len(response.download_urls) == 2
        assert response.download_urls["html"] == f"http://localhost:8000/reports/{report_id}/download/html"
        assert response.download_urls["pdf"] == f"http://localhost:8000/reports/{report_id}/download/pdf"
    
    def test_report_list_response(self):
        """Test ReportListResponse creation."""
        reports = [
            ReportResponse(
                id=uuid4(),
                title="Report 1",
                query="Query 1",
                report_type="comprehensive",
                generation_status="completed",
                formats_generated=["html"],
                word_count=1000,
                page_count=3,
                quality_score=0.8,
                confidence_score=0.7,
                created_at="2024-01-01T00:00:00Z"
            ),
            ReportResponse(
                id=uuid4(),
                title="Report 2",
                query="Query 2",
                report_type="executive_summary",
                generation_status="completed",
                formats_generated=["html", "pdf"],
                word_count=500,
                page_count=2,
                quality_score=0.9,
                confidence_score=0.8,
                created_at="2024-01-01T00:00:00Z"
            )
        ]
        
        list_response = ReportListResponse(
            reports=reports,
            total_count=2,
            page=1,
            page_size=10,
            has_more=False
        )
        
        assert len(list_response.reports) == 2
        assert list_response.total_count == 2
        assert list_response.page == 1
        assert list_response.page_size == 10
        assert list_response.has_more is False
    
    def test_report_search_request(self):
        """Test ReportSearchRequest validation."""
        request = ReportSearchRequest(
            search_term="AI education",
            user_id=uuid4(),
            report_type="comprehensive",
            min_quality_score=0.7,
            limit=20,
            offset=0
        )
        
        assert request.search_term == "AI education"
        assert request.user_id is not None
        assert request.report_type == "comprehensive"
        assert request.min_quality_score == 0.7
        assert request.limit == 20
        assert request.offset == 0
    
    def test_report_search_request_minimal(self):
        """Test ReportSearchRequest with minimal data."""
        request = ReportSearchRequest(search_term="test")
        
        assert request.search_term == "test"
        assert request.user_id is None
        assert request.report_type is None
        assert request.min_quality_score is None
        assert request.limit == 20  # Default
        assert request.offset == 0  # Default
    
    def test_report_statistics_response(self):
        """Test ReportStatisticsResponse creation."""
        stats = ReportStatisticsResponse(
            total_reports=50,
            status_counts={"completed": 45, "failed": 3, "generating": 2},
            type_counts={"comprehensive": 30, "executive_summary": 15, "academic_paper": 5},
            average_quality_score=0.82,
            average_confidence_score=0.78,
            average_generation_time=45.5,
            average_word_count=1500,
            total_access_count=250,
            storage_statistics={"total_storage_mb": 125.5, "total_files": 150}
        )
        
        assert stats.total_reports == 50
        assert stats.status_counts["completed"] == 45
        assert stats.type_counts["comprehensive"] == 30
        assert stats.average_quality_score == 0.82
        assert stats.storage_statistics["total_storage_mb"] == 125.5


class TestReportLogic:
    """Test report API business logic."""
    
    def test_report_configuration_building(self):
        """Test building report configuration from request."""
        from src.models.report import ReportConfiguration
        
        request = CreateReportRequest(
            title="Test Report",
            query="Test query",
            report_type=ReportType.ACADEMIC_PAPER,
            citation_style=CitationStyle.MLA,
            formats=[ReportFormat.PDF, ReportFormat.LATEX],
            include_toc=False,
            include_visualizations=False
        )
        
        # Simulate the configuration building logic from the API
        config = ReportConfiguration(
            format=request.formats[0] if request.formats else ReportFormat.HTML,
            type=request.report_type,
            citation_style=request.citation_style,
            include_toc=request.include_toc,
            include_executive_summary=request.include_executive_summary,
            include_visualizations=request.include_visualizations,
            include_citations=request.include_citations,
            include_methodology=request.include_methodology,
        )
        
        assert config.format == ReportFormat.PDF
        assert config.type == ReportType.ACADEMIC_PAPER
        assert config.citation_style == CitationStyle.MLA
        assert config.include_toc is False
        assert config.include_visualizations is False
        assert config.include_executive_summary is True  # Default from request
        assert config.include_citations is True
        assert config.include_methodology is True
    
    def test_workflow_data_building(self):
        """Test building workflow data from request."""
        request = CreateReportRequest(
            title="Research Report",
            query="How does AI impact education?",
            domains=["AI", "Education", "Technology"],
            project_id=uuid4(),
            workflow_data={
                "aggregated_results": {
                    "sources": [{"title": "AI Paper", "year": 2024}],
                    "findings": {"key_insights": ["AI improves learning"]}
                }
            }
        )
        
        # Simulate the workflow data building logic from the API
        workflow_data = {
            "title": request.title,
            "query": request.query,
            "domains": request.domains,
            "project_id": str(request.project_id) if request.project_id else None,
            **request.workflow_data
        }
        
        assert workflow_data["title"] == "Research Report"
        assert workflow_data["query"] == "How does AI impact education?"
        assert len(workflow_data["domains"]) == 3
        assert "AI" in workflow_data["domains"]
        assert workflow_data["project_id"] == str(request.project_id)
        assert "aggregated_results" in workflow_data
        assert len(workflow_data["aggregated_results"]["sources"]) == 1
    
    def test_download_filename_generation(self):
        """Test download filename generation logic."""
        report_id = uuid4()
        
        # Simulate the filename generation logic from the API
        extensions = {
            "html": ".html",
            "pdf": ".pdf",
            "latex": ".tex",
            "docx": ".docx",
            "markdown": ".md",
            "json": ".json"
        }
        
        test_cases = [
            ("html", ".html"),
            ("pdf", ".pdf"),
            ("latex", ".tex"),
            ("docx", ".docx"),
            ("markdown", ".md"),
            ("json", ".json"),
            ("unknown", ".unknown")
        ]
        
        for format_type, expected_ext in test_cases:
            extension = extensions.get(format_type, f".{format_type}")
            filename = f"report_{report_id}{extension}"
            
            assert filename.startswith("report_")
            assert filename.endswith(expected_ext)
            assert str(report_id) in filename
    
    def test_pagination_logic(self):
        """Test pagination calculation logic."""
        # Simulate pagination logic from the API
        def calculate_pagination(page: int, page_size: int, total_items: int):
            offset = (page - 1) * page_size
            has_more = offset + page_size < total_items
            return offset, has_more
        
        # Test cases
        assert calculate_pagination(1, 10, 25) == (0, True)
        assert calculate_pagination(2, 10, 25) == (10, True)
        assert calculate_pagination(3, 10, 25) == (20, False)
        assert calculate_pagination(1, 20, 15) == (0, False)
    
    def test_search_filters_building(self):
        """Test search filters building logic."""
        request = ReportSearchRequest(
            search_term="AI education",
            report_type="comprehensive",
            min_quality_score=0.8,
            limit=15,
            offset=30
        )
        
        # Simulate the search filters building logic from the API
        filters = {}
        if request.report_type:
            filters["report_type"] = request.report_type
        if request.min_quality_score is not None:
            filters["min_quality_score"] = request.min_quality_score
        
        assert filters["report_type"] == "comprehensive"
        assert filters["min_quality_score"] == 0.8
        
        # Test pagination calculation for search
        page = (request.offset // request.limit) + 1
        assert page == 3  # (30 // 15) + 1 = 3


if __name__ == "__main__":
    pytest.main([__file__])