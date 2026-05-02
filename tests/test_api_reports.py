"""
Tests for reports API endpoints.

This module tests the REST API endpoints for report generation,
retrieval, and management functionality.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.models.db.generated_report import GeneratedReport

# Test client
client = TestClient(app)


class TestReportsAPI:
    """Test reports API endpoints."""

    def test_create_report_endpoint(self):
        """Test report creation endpoint."""
        request_data = {
            "title": "Test Report",
            "query": "What is the impact of AI on education?",
            "domains": ["AI", "Education"],
            "report_type": "comprehensive",
            "citation_style": "APA",
            "formats": ["html", "markdown"],
            "workflow_data": {
                "aggregated_results": {
                    "sources": [
                        {
                            "title": "AI in Education",
                            "authors": ["Smith, J."],
                            "year": 2024,
                        }
                    ]
                }
            },
        }

        with patch("src.api.routes.reports.get_report_services") as mock_services:
            # Mock the services
            mock_generator = MagicMock()
            mock_storage = MagicMock()
            mock_repo = MagicMock()
            mock_format_repo = MagicMock()

            mock_services.return_value = (
                mock_generator,
                mock_storage,
                mock_repo,
                mock_format_repo,
            )

            response = client.post("/api/v1/reports/generate", json=request_data)

            assert response.status_code == 202
            data = response.json()

            assert data["title"] == "Test Report"
            assert data["query"] == "What is the impact of AI on education?"
            assert data["report_type"] == "comprehensive"
            assert data["generation_status"] == "generating"

    def test_get_report_endpoint(self):
        """Test get report endpoint."""
        report_id = uuid4()

        # Mock database report
        mock_report = MagicMock(spec=GeneratedReport)
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
        mock_report.formats = []

        with patch("src.api.routes.reports.get_report_services") as mock_services:
            mock_generator = MagicMock()
            mock_storage = MagicMock()
            mock_storage.retrieve_report = AsyncMock(return_value=mock_report)
            mock_storage.update_report_access = AsyncMock()
            mock_repo = MagicMock()
            mock_format_repo = MagicMock()

            mock_services.return_value = (
                mock_generator,
                mock_storage,
                mock_repo,
                mock_format_repo,
            )

            response = client.get(f"/api/v1/reports/{report_id}")

            assert response.status_code == 200
            data = response.json()

            assert data["id"] == str(report_id)
            assert data["title"] == "Test Report"
            assert data["generation_status"] == "completed"
            assert data["word_count"] == 1500

    def test_get_report_not_found(self):
        """Test get report when report doesn't exist."""
        report_id = uuid4()

        with patch("src.api.routes.reports.get_report_services") as mock_services:
            mock_generator = MagicMock()
            mock_storage = MagicMock()
            mock_storage.retrieve_report = AsyncMock(return_value=None)
            mock_repo = MagicMock()
            mock_format_repo = MagicMock()

            mock_services.return_value = (
                mock_generator,
                mock_storage,
                mock_repo,
                mock_format_repo,
            )

            response = client.get(f"/api/v1/reports/{report_id}")

            assert response.status_code == 404
            assert "not found" in response.json()["error"]["message"]

    def test_download_report_endpoint(self):
        """Test download report endpoint."""
        report_id = uuid4()
        format_type = "html"

        mock_content = b"<html><body>Test Report</body></html>"
        mock_mime_type = "text/html"

        with patch("src.api.routes.reports.get_report_services") as mock_services:
            mock_generator = MagicMock()
            mock_storage = MagicMock()
            mock_storage.retrieve_report_content = AsyncMock(
                return_value=(mock_content, mock_mime_type)
            )
            mock_storage.update_report_access = AsyncMock()
            mock_repo = MagicMock()
            mock_format_repo = MagicMock()

            mock_services.return_value = (
                mock_generator,
                mock_storage,
                mock_repo,
                mock_format_repo,
            )

            response = client.get(f"/api/v1/reports/{report_id}/download/{format_type}")

            assert response.status_code == 200
            assert response.headers["content-type"].startswith(mock_mime_type)
            assert "attachment" in response.headers["content-disposition"]

    def test_download_report_not_found(self):
        """Test download when report format doesn't exist."""
        report_id = uuid4()
        format_type = "pdf"

        with patch("src.api.routes.reports.get_report_services") as mock_services:
            mock_generator = MagicMock()
            mock_storage = MagicMock()
            mock_storage.retrieve_report_content = AsyncMock(return_value=None)
            mock_repo = MagicMock()
            mock_format_repo = MagicMock()

            mock_services.return_value = (
                mock_generator,
                mock_storage,
                mock_repo,
                mock_format_repo,
            )

            response = client.get(f"/api/v1/reports/{report_id}/download/{format_type}")

            assert response.status_code == 404

    def test_list_reports_endpoint(self):
        """Test list reports endpoint."""
        user_id = uuid4()

        # Mock reports
        mock_reports = [
            MagicMock(
                id=uuid4(),
                title="Report 1",
                query="Query 1",
                report_type="comprehensive",
                generation_status="completed",
                word_count=1000,
                page_count=3,
                quality_score=0.8,
                confidence_score=0.7,
                created_at=MagicMock(),
                generation_time_seconds=30.0,
                formats=[],
            ),
            MagicMock(
                id=uuid4(),
                title="Report 2",
                query="Query 2",
                report_type="executive_summary",
                generation_status="completed",
                word_count=500,
                page_count=2,
                quality_score=0.9,
                confidence_score=0.8,
                created_at=MagicMock(),
                generation_time_seconds=20.0,
                formats=[],
            ),
        ]

        for mock_report in mock_reports:
            mock_report.created_at.isoformat.return_value = "2024-01-01T00:00:00Z"

        with patch("src.api.routes.reports.get_report_services") as mock_services:
            mock_generator = MagicMock()
            mock_storage = MagicMock()
            mock_storage.list_user_reports = AsyncMock(return_value=mock_reports)
            mock_repo = MagicMock()
            mock_format_repo = MagicMock()

            mock_services.return_value = (
                mock_generator,
                mock_storage,
                mock_repo,
                mock_format_repo,
            )

            response = client.get(
                f"/api/v1/reports?user_id={user_id}&page=1&page_size=10"
            )

            assert response.status_code == 200
            data = response.json()

            assert len(data["reports"]) == 2
            assert data["page"] == 1
            assert data["page_size"] == 10
            assert data["has_more"] is False

    def test_search_reports_endpoint(self):
        """Test search reports endpoint."""
        search_request = {
            "search_term": "AI education",
            "user_id": str(uuid4()),
            "report_type": "comprehensive",
            "min_quality_score": 0.7,
            "limit": 20,
            "offset": 0,
        }

        mock_reports = [
            MagicMock(
                id=uuid4(),
                title="AI in Education Report",
                query="How does AI impact education?",
                report_type="comprehensive",
                generation_status="completed",
                word_count=2000,
                page_count=7,
                quality_score=0.85,
                confidence_score=0.8,
                created_at=MagicMock(),
                generation_time_seconds=60.0,
                formats=[],
            )
        ]

        mock_reports[0].created_at.isoformat.return_value = "2024-01-01T00:00:00Z"

        with patch("src.api.routes.reports.get_report_services") as mock_services:
            mock_generator = MagicMock()
            mock_storage = MagicMock()
            mock_storage.search_reports = AsyncMock(return_value=(mock_reports, 1))
            mock_repo = MagicMock()
            mock_format_repo = MagicMock()

            mock_services.return_value = (
                mock_generator,
                mock_storage,
                mock_repo,
                mock_format_repo,
            )

            response = client.post("/api/v1/reports/search", json=search_request)

            assert response.status_code == 200
            data = response.json()

            assert len(data["reports"]) == 1
            assert data["total_count"] == 1
            assert data["reports"][0]["title"] == "AI in Education Report"

    def test_get_statistics_endpoint(self):
        """Test get statistics endpoint."""
        mock_stats = {
            "total_reports": 50,
            "status_counts": {"completed": 45, "failed": 3, "generating": 2},
            "type_counts": {
                "comprehensive": 30,
                "executive_summary": 15,
                "academic_paper": 5,
            },
            "average_quality_score": 0.82,
            "average_confidence_score": 0.78,
            "average_generation_time": 45.5,
            "average_word_count": 1500,
            "total_access_count": 250,
            "total_storage_mb": 125.5,
            "total_files": 150,
        }

        with patch("src.api.routes.reports.get_report_services") as mock_services:
            mock_generator = MagicMock()
            mock_storage = MagicMock()
            mock_storage.get_storage_statistics = AsyncMock(return_value=mock_stats)
            mock_repo = MagicMock()
            mock_format_repo = MagicMock()

            mock_services.return_value = (
                mock_generator,
                mock_storage,
                mock_repo,
                mock_format_repo,
            )

            response = client.get("/api/v1/reports/statistics")

            assert response.status_code == 200
            data = response.json()

            assert data["total_reports"] == 50
            assert data["status_counts"]["completed"] == 45
            assert data["average_quality_score"] == 0.82
            assert data["storage_statistics"]["total_storage_mb"] == 125.5

    def test_delete_report_endpoint(self):
        """Test delete report endpoint."""
        report_id = uuid4()

        with patch("src.api.routes.reports.get_report_services") as mock_services:
            mock_generator = MagicMock()
            mock_storage = MagicMock()
            mock_storage.delete_report = AsyncMock(return_value=True)
            mock_repo = MagicMock()
            mock_format_repo = MagicMock()

            mock_services.return_value = (
                mock_generator,
                mock_storage,
                mock_repo,
                mock_format_repo,
            )

            response = client.delete(f"/api/v1/reports/{report_id}?delete_files=true")

            assert response.status_code == 204
            mock_storage.delete_report.assert_called_once_with(report_id, True)

    def test_delete_report_not_found(self):
        """Test delete report when report doesn't exist."""
        report_id = uuid4()

        with patch("src.api.routes.reports.get_report_services") as mock_services:
            mock_generator = MagicMock()
            mock_storage = MagicMock()
            mock_storage.delete_report = AsyncMock(return_value=False)
            mock_repo = MagicMock()
            mock_format_repo = MagicMock()

            mock_services.return_value = (
                mock_generator,
                mock_storage,
                mock_repo,
                mock_format_repo,
            )

            response = client.delete(f"/api/v1/reports/{report_id}")

            assert response.status_code == 404

    def test_verify_integrity_endpoint(self):
        """Test verify report integrity endpoint."""
        report_id = uuid4()

        mock_integrity = {
            "report_id": str(report_id),
            "status": "ok",
            "formats_checked": 3,
            "formats_valid": 3,
            "formats_invalid": [],
            "missing_files": [],
            "errors": [],
        }

        with patch("src.api.routes.reports.get_report_services") as mock_services:
            mock_generator = MagicMock()
            mock_storage = MagicMock()
            mock_storage.verify_report_integrity = AsyncMock(
                return_value=mock_integrity
            )
            mock_repo = MagicMock()
            mock_format_repo = MagicMock()

            mock_services.return_value = (
                mock_generator,
                mock_storage,
                mock_repo,
                mock_format_repo,
            )

            response = client.get(f"/api/v1/reports/{report_id}/integrity")

            assert response.status_code == 200
            data = response.json()

            assert data["status"] == "ok"
            assert data["formats_checked"] == 3
            assert data["formats_valid"] == 3

    def test_service_unavailable_handling(self):
        """Test handling when services are unavailable."""
        with patch("src.api.routes.reports.get_report_services") as mock_services:
            # Simulate service unavailability
            mock_services.return_value = (None, None, None, None)

            response = client.get("/api/v1/reports/statistics")

            assert response.status_code == 503
            assert "not available" in response.json()["error"]["message"]

    def test_validation_errors(self):
        """Test request validation errors."""
        # Test with missing required fields
        invalid_request = {
            "query": "Test query"
            # Missing title
        }

        response = client.post("/api/v1/reports/generate", json=invalid_request)

        assert response.status_code == 422  # Validation error

        # Test with invalid field values
        invalid_request = {
            "title": "",  # Empty title
            "query": "Test query",
            "report_type": "invalid_type",
        }

        response = client.post("/api/v1/reports/generate", json=invalid_request)

        assert response.status_code == 422

    def test_report_type_enum_validation(self):
        """Test report type enum validation."""
        request_data = {
            "title": "Test Report",
            "query": "Test query",
            "report_type": "comprehensive",  # Valid enum value
            "citation_style": "APA",
            "formats": ["html"],
        }

        with patch("src.api.routes.reports.get_report_services") as mock_services:
            mock_services.return_value = (
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
            )

            response = client.post("/api/v1/reports/generate", json=request_data)

            assert response.status_code == 202

    def test_citation_style_enum_validation(self):
        """Test citation style enum validation."""
        request_data = {
            "title": "Test Report",
            "query": "Test query",
            "citation_style": "MLA",  # Valid enum value
            "formats": ["html"],
        }

        with patch("src.api.routes.reports.get_report_services") as mock_services:
            mock_services.return_value = (
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
            )

            response = client.post("/api/v1/reports/generate", json=request_data)

            assert response.status_code == 202

    def test_format_enum_validation(self):
        """Test format enum validation."""
        request_data = {
            "title": "Test Report",
            "query": "Test query",
            "formats": ["html", "pdf", "markdown"],  # Valid enum values
        }

        with patch("src.api.routes.reports.get_report_services") as mock_services:
            mock_services.return_value = (
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
            )

            response = client.post("/api/v1/reports/generate", json=request_data)

            assert response.status_code == 202


class TestReportResponseModels:
    """Test report response model conversions."""

    def test_report_response_from_db_report(self):
        """Test creating response from database report."""
        from src.api.routes.reports import ReportResponse

        # Mock database report
        mock_report = MagicMock(spec=GeneratedReport)
        mock_report.id = uuid4()
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

        response = ReportResponse.from_db_report(
            mock_report, base_url="http://localhost:8000"
        )

        assert response.id == mock_report.id
        assert response.title == "Test Report"
        assert response.generation_status == "completed"
        assert len(response.formats_generated) == 2
        assert "html" in response.formats_generated
        assert "pdf" in response.formats_generated
        assert len(response.download_urls) == 2
        assert "html" in response.download_urls
        assert "pdf" in response.download_urls


if __name__ == "__main__":
    pytest.main([__file__])
