"""Validation tests for report API request models."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


class TestReportsAPIValidation:
    """Test report API validation behavior."""

    @pytest.fixture
    def client(self) -> TestClient:
        """Create test client."""
        from src.api.main import app
        return TestClient(app)

    def test_validation_errors(self, client: TestClient) -> None:
        """Test request validation errors."""
        invalid_request = {
            "query": "Test query",
        }

        response = client.post("/api/v1/reports/generate", json=invalid_request)

        assert response.status_code == 422

        invalid_request = {
            "title": "",
            "query": "Test query",
            "report_type": "invalid_type",
        }

        response = client.post("/api/v1/reports/generate", json=invalid_request)

        assert response.status_code == 422

    def test_report_type_enum_validation(self, client: TestClient) -> None:
        """Test report type enum validation."""
        request_data = {
            "title": "Test Report",
            "query": "Test query",
            "report_type": "comprehensive",
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

    def test_citation_style_enum_validation(self, client: TestClient) -> None:
        """Test citation style enum validation."""
        request_data = {
            "title": "Test Report",
            "query": "Test query",
            "citation_style": "MLA",
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

    def test_format_enum_validation(self, client: TestClient) -> None:
        """Test format enum validation."""
        request_data = {
            "title": "Test Report",
            "query": "Test query",
            "formats": ["html", "pdf", "markdown"],
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
