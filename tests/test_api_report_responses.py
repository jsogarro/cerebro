"""Tests for report API response model conversions."""

from unittest.mock import MagicMock
from uuid import uuid4

from src.api.routes.reports import ReportResponse
from src.models.db.generated_report import GeneratedReport


class TestReportResponseModels:
    """Test report response model conversions."""

    def test_report_response_from_db_report(self) -> None:
        """Test creating response from database report."""
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
