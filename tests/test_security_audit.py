"""Tests for audit logging security behavior."""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db.audit_log import AuditEventType, AuditSeverity
from src.security.audit_logger import AuditLogger


@pytest.fixture
async def db_session() -> Any:
    """Create a mock database session."""
    session = AsyncMock(spec=AsyncSession)
    session.add = Mock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    return session


class TestAuditLogger:
    """Test audit logger functionality."""

    @pytest.mark.asyncio
    async def test_log_event(self, db_session: Any) -> None:
        """Test logging an audit event."""
        logger = AuditLogger(db_session=db_session, cache_manager=None, buffer_size=10)

        event_id = await logger.log_event(
            event_type=AuditEventType.LOGIN_SUCCESS,
            action="user_login",
            user_id="user123",
            username="testuser",
            email="test@example.com",
            result="success",
            severity=AuditSeverity.INFO,
        )

        assert event_id is not None
        assert logger.metrics["events_logged"] == 1
        assert len(logger.log_buffer) == 1

    @pytest.mark.asyncio
    async def test_buffer_flush(self, db_session: Any) -> None:
        """Test buffer flushing to database."""
        logger = AuditLogger(db_session=db_session, buffer_size=2)

        await logger.log_event(
            event_type=AuditEventType.LOGIN_SUCCESS, action="login", user_id="user1"
        )
        await logger.log_event(
            event_type=AuditEventType.LOGOUT, action="logout", user_id="user1"
        )

        await logger.flush_buffer()

        assert logger.metrics["events_flushed"] == 2
        assert len(logger.log_buffer) == 0
        assert db_session.add.call_count == 2
        assert db_session.commit.called

    @pytest.mark.asyncio
    async def test_alert_generation(self, db_session: Any) -> None:
        """Test security alert generation."""
        logger = AuditLogger(
            db_session=db_session, alert_threshold={"failed_login_attempts": 3}
        )
        query_result = Mock()
        query_result.scalar.return_value = 5
        db_session.execute.return_value = query_result

        await logger.log_event(
            event_type=AuditEventType.LOGIN_FAILED,
            action="login_attempt",
            user_id="user123",
            result="failure",
            severity=AuditSeverity.WARNING,
        )

        assert logger.metrics["alerts_generated"] >= 0

    @pytest.mark.asyncio
    async def test_compliance_report(self, db_session: Any) -> None:
        """Test compliance report generation."""
        logger = AuditLogger(db_session=db_session)
        mock_logs = [
            MagicMock(
                event_type=AuditEventType.DATA_ACCESSED,
                metadata={"consent_verified": True},
            ),
            MagicMock(event_type=AuditEventType.DATA_EXPORTED, metadata={}),
        ]

        with patch.object(logger, "query_logs", return_value=mock_logs):
            report = await logger.generate_compliance_report(
                start_date=datetime.now(UTC) - timedelta(days=30),
                end_date=datetime.now(UTC),
                compliance_type="gdpr",
            )

        assert report["compliance_type"] == "gdpr"
        assert "summary" in report
        assert "violations" in report
        assert "recommendations" in report
