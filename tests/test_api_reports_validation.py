"""Validation tests for report API request models."""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_jwt_service
from src.middleware.tenant_context import TenantContext, get_tenant_context
from src.models.db.generated_report import GeneratedReport
from src.models.db.session import get_session

AUTH_USER_ID = uuid4()
AUTH_ORG_ID = uuid4()
AUTH_TOKEN = "test-token"


class _SuccessfulDurableAuditLogger:
    """In-memory durable audit double for the lifespan-free test client."""

    def __init__(self) -> None:
        self.pending_events: list[dict[str, Any]] = []
        self.persisted_events: list[dict[str, Any]] = []

    async def log_event(self, **event: Any) -> str:
        """Buffer an audit event and return a deterministic event identifier."""
        self.pending_events.append(event)
        return f"test-audit-event-{len(self.pending_events)}"

    async def flush_buffer(self) -> None:
        """Model a successful durable flush."""
        self.persisted_events.extend(self.pending_events)
        self.pending_events.clear()


class _TestJWTService:
    """Return the fixture tenant identity at the application auth boundary."""

    async def validate_token(self, token: str) -> TokenPayload:
        """Validate the deterministic test bearer token."""
        assert token == AUTH_TOKEN
        now = datetime.now(UTC)
        return TokenPayload(
            sub=str(AUTH_USER_ID),
            email="test@example.com",
            organization_id=str(AUTH_ORG_ID),
            jti="test-jti",
            iat=now,
            exp=now + timedelta(minutes=5),
        )


def _pending_report() -> MagicMock:
    """Return the persisted row a valid generate request should expose."""
    report = MagicMock(spec=GeneratedReport)
    report.id = uuid4()
    report.title = "Test Report"
    report.query = "Test query"
    report.report_type = "comprehensive"
    report.generation_status = "pending"
    report.formats = []
    report.word_count = 0
    report.page_count = 0
    report.quality_score = 0.0
    report.confidence_score = 0.0
    report.created_at = datetime.now()
    report.generation_time_seconds = None
    return report


class TestReportsAPIValidation:
    """Test report API validation behavior."""

    @pytest.fixture
    def client(self) -> TestClient:
        """Create test client."""
        from src.api.main import app

        session = AsyncMock(spec=AsyncSession)

        async def override_session():
            yield session

        async def override_tenant_context() -> TenantContext:
            return TenantContext(
                user_id=str(AUTH_USER_ID), organization_id=str(AUTH_ORG_ID)
            )

        app.dependency_overrides[get_session] = override_session
        app.dependency_overrides[get_tenant_context] = override_tenant_context
        app.dependency_overrides[get_jwt_service] = _TestJWTService
        missing = object()
        previous_audit_logger = getattr(app.state, "audit_logger", missing)
        app.state.audit_logger = _SuccessfulDurableAuditLogger()
        try:
            yield TestClient(app, headers={"Authorization": f"Bearer {AUTH_TOKEN}"})
        finally:
            app.dependency_overrides.pop(get_session, None)
            app.dependency_overrides.pop(get_tenant_context, None)
            app.dependency_overrides.pop(get_jwt_service, None)
            if previous_audit_logger is missing:
                delattr(app.state, "audit_logger")
            else:
                app.state.audit_logger = previous_audit_logger

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
            mock_repo = MagicMock()
            mock_repo.create_report = AsyncMock(return_value=_pending_report())
            mock_repo.get_report_with_formats = AsyncMock(
                return_value=mock_repo.create_report.return_value
            )
            mock_services.return_value = (
                MagicMock(),
                MagicMock(),
                mock_repo,
                MagicMock(),
            )

            with patch(
                "src.api.routes.reports._generate_report_task",
                new=AsyncMock(),
            ):
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
            mock_repo = MagicMock()
            mock_repo.create_report = AsyncMock(return_value=_pending_report())
            mock_repo.get_report_with_formats = AsyncMock(
                return_value=mock_repo.create_report.return_value
            )
            mock_services.return_value = (
                MagicMock(),
                MagicMock(),
                mock_repo,
                MagicMock(),
            )

            with patch(
                "src.api.routes.reports._generate_report_task",
                new=AsyncMock(),
            ):
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
            mock_repo = MagicMock()
            mock_repo.create_report = AsyncMock(return_value=_pending_report())
            mock_repo.get_report_with_formats = AsyncMock(
                return_value=mock_repo.create_report.return_value
            )
            mock_services.return_value = (
                MagicMock(),
                MagicMock(),
                mock_repo,
                MagicMock(),
            )

            with patch(
                "src.api.routes.reports._generate_report_task",
                new=AsyncMock(),
            ):
                response = client.post("/api/v1/reports/generate", json=request_data)

            assert response.status_code == 202
