"""Tests for audit logging security behavior."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
import redis.asyncio as redis
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db.audit_log import AuditEventType, AuditSeverity
from src.security.audit_logger import AuditLogger
from src.security.rate_limiter import RateLimiter
from src.security.validators import SecurityValidator


@pytest.fixture
async def redis_client() -> Any:
    """Create a mock Redis client."""
    client = AsyncMock(spec=redis.Redis)
    pipeline = Mock()
    pipeline.execute = AsyncMock()
    client.pipeline = Mock(return_value=pipeline)
    client.zrange = AsyncMock(return_value=[])
    return client


@pytest.fixture
async def db_session() -> Any:
    """Create a mock database session."""
    session = AsyncMock(spec=AsyncSession)
    session.add = Mock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def mock_request() -> Request:
    """Create a mock FastAPI request."""
    request = Mock(spec=Request)
    request.url.path = "/api/v1/test"
    request.client = Mock()
    request.client.host = "192.168.1.1"
    request.headers = {
        "User-Agent": "TestAgent/1.0",
        "X-Request-ID": "test-request-123",
    }
    request.state = Mock()
    return cast(Request, request)


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


class TestSecurityAuditIntegration:
    """Integration tests for security components."""

    @pytest.mark.asyncio
    async def test_rate_limit_with_audit(
        self, redis_client: Any, db_session: Any, mock_request: Request
    ) -> None:
        """Test rate limiting with audit logging."""
        rate_limiter = RateLimiter(redis_client, default_limit=2, default_window=60)
        audit_logger = AuditLogger(db_session)

        redis_client.pipeline.return_value.execute.return_value = [None, None, 3, None]
        redis_client.zrange.return_value = [(b"timestamp", 1234567890.0)]
        query_result = Mock()
        query_result.scalar.return_value = 1
        db_session.execute.return_value = query_result

        allowed, metadata = await rate_limiter.check_rate_limit(
            mock_request, identifier="user123"
        )

        if not allowed:
            await audit_logger.log_event(
                event_type=AuditEventType.RATE_LIMIT_EXCEEDED,
                action="rate_limit_check",
                user_id="user123",
                severity=AuditSeverity.WARNING,
                metadata=metadata,
            )

        assert not allowed
        assert audit_logger.metrics["events_logged"] > 0

    @pytest.mark.asyncio
    async def test_validation_with_audit(self, db_session: Any) -> None:
        """Test input validation with audit logging."""
        audit_logger = AuditLogger(db_session)

        malicious_input = "'; DROP TABLE users; --"

        if SecurityValidator.detect_sql_injection(malicious_input):
            await audit_logger.log_event(
                event_type=AuditEventType.SQL_INJECTION_ATTEMPT,
                action="input_validation",
                severity=AuditSeverity.CRITICAL,
                metadata={"input": malicious_input[:100]},
            )

        assert audit_logger.metrics["events_logged"] == 1
        assert len(audit_logger.log_buffer) == 1
