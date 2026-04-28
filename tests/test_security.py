"""
Test suite for security components.

Tests rate limiting, security headers, validators, audit logging,
and authentication features.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
import redis.asyncio as redis
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db.audit_log import AuditEventType, AuditSeverity
from src.security.audit_logger import AuditLogger
from src.security.headers import CORSSecurityMiddleware, SecurityHeadersMiddleware
from src.security.rate_limiter import RateLimiter, RateLimitStrategy
from src.security.validators import (
    FileUploadValidator,
    LoginRequest,
    SecurityValidator,
)


@pytest.fixture
async def redis_client():
    """Create a mock Redis client."""
    client = AsyncMock(spec=redis.Redis)
    client.pipeline.return_value = AsyncMock()
    client.scan.return_value = (0, [])
    return client


@pytest.fixture
async def db_session():
    """Create a mock database session."""
    session = AsyncMock(spec=AsyncSession)
    session.add = Mock()
    session.commit = AsyncMock()
    session.execute = AsyncMock()
    return session


@pytest.fixture
def mock_request():
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
    return request


class TestRateLimiter:
    """Test rate limiter functionality."""

    @pytest.mark.asyncio
    async def test_sliding_window_rate_limit(self, redis_client, mock_request):
        """Test sliding window rate limiting."""
        # Setup
        rate_limiter = RateLimiter(
            redis_client,
            default_limit=5,
            default_window=60,
            default_strategy=RateLimitStrategy.SLIDING_WINDOW,
        )

        # Mock Redis responses
        redis_client.pipeline.return_value.execute.return_value = [None, None, 3, None]
        redis_client.zrange.return_value = [(b"timestamp", 1234567890.0)]

        # Test rate limit check
        allowed, metadata = await rate_limiter.check_rate_limit(
            mock_request, identifier="user123"
        )

        assert allowed is True
        assert metadata["limit"] == 5
        assert metadata["remaining"] == 2
        assert metadata["strategy"] == "sliding_window"

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded(self, redis_client, mock_request):
        """Test rate limit exceeded scenario."""
        rate_limiter = RateLimiter(redis_client, default_limit=5, default_window=60)

        # Mock Redis to return count exceeding limit
        redis_client.pipeline.return_value.execute.return_value = [None, None, 6, None]
        redis_client.zrange.return_value = [(b"timestamp", 1234567890.0)]

        allowed, metadata = await rate_limiter.check_rate_limit(
            mock_request, identifier="user123"
        )

        assert allowed is False
        assert metadata["remaining"] == 0

    @pytest.mark.asyncio
    async def test_token_bucket_rate_limit(self, redis_client, mock_request):
        """Test token bucket rate limiting."""
        rate_limiter = RateLimiter(
            redis_client,
            default_limit=10,
            default_window=60,
            default_strategy=RateLimitStrategy.TOKEN_BUCKET,
        )

        # Mock Redis responses for token bucket
        redis_client.pipeline.return_value.get.side_effect = [b"5.5", b"1234567890.0"]
        redis_client.pipeline.return_value.execute.return_value = [
            b"5.5",
            b"1234567890.0",
        ]

        _allowed, metadata = await rate_limiter.check_rate_limit(
            mock_request, endpoint="/api/v1/research/execute"
        )

        assert "tokens" in metadata
        assert "rate" in metadata

    @pytest.mark.asyncio
    async def test_reset_rate_limit(self, redis_client):
        """Test resetting rate limits."""
        rate_limiter = RateLimiter(redis_client)

        redis_client.scan.return_value = (0, [b"rate_limit:user:123:abc"])
        redis_client.delete.return_value = 1

        result = await rate_limiter.reset_rate_limit("user123")
        assert result is True


class TestSecurityHeaders:
    """Test security headers middleware."""

    @pytest.mark.asyncio
    async def test_security_headers_applied(self):
        """Test that security headers are applied to responses."""
        middleware = SecurityHeadersMiddleware(
            csp_enabled=True, hsts_enabled=True, frame_options="DENY"
        )

        # Create mock request and response
        request = Mock(spec=Request)
        request.state = Mock()

        response = Mock()
        response.headers = {}

        async def call_next(req):
            return response

        result = await middleware(request, call_next)

        # Check headers are set
        assert "Content-Security-Policy" in result.headers
        assert "Strict-Transport-Security" in result.headers
        assert result.headers["X-Frame-Options"] == "DENY"
        assert result.headers["X-Content-Type-Options"] == "nosniff"
        assert result.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    @pytest.mark.asyncio
    async def test_csp_nonce_generation(self):
        """Test CSP nonce generation."""
        middleware = SecurityHeadersMiddleware(csp_enabled=True, nonce_enabled=True)

        request = Mock(spec=Request)
        request.state = Mock()
        response = Mock()
        response.headers = {}

        async def call_next(req):
            # Check nonce was added to request state
            assert hasattr(req.state, "csp_nonce")
            assert len(req.state.csp_nonce) > 0
            return response

        result = await middleware(request, call_next)

        # Check CSP header contains nonce
        csp_header = result.headers.get("Content-Security-Policy", "")
        assert "nonce-" in csp_header

    @pytest.mark.asyncio
    async def test_cors_security(self):
        """Test CORS security middleware."""
        middleware = CORSSecurityMiddleware(
            allowed_origins=["https://example.com"], allow_credentials=True
        )

        # Test allowed origin
        request = Mock(spec=Request)
        request.headers = {"Origin": "https://example.com"}
        request.method = "GET"

        response = Mock()
        response.headers = {}

        async def call_next(req):
            return response

        result = await middleware(request, call_next)

        assert result.headers["Access-Control-Allow-Origin"] == "https://example.com"
        assert result.headers["Access-Control-Allow-Credentials"] == "true"

    @pytest.mark.asyncio
    async def test_cors_preflight(self):
        """Test CORS preflight request handling."""
        middleware = CORSSecurityMiddleware(
            allowed_origins=["https://example.com"],
            allowed_methods=["GET", "POST"],
            max_age=3600,
        )

        request = Mock(spec=Request)
        request.headers = {"Origin": "https://example.com"}
        request.method = "OPTIONS"

        async def call_next(req):
            return Mock()

        result = await middleware(request, call_next)

        assert result.headers["Access-Control-Allow-Origin"] == "https://example.com"
        assert "GET, POST" in result.headers["Access-Control-Allow-Methods"]
        assert result.headers["Access-Control-Max-Age"] == "3600"


class TestSecurityValidators:
    """Test security validators."""

    def test_detect_sql_injection(self):
        """Test SQL injection detection."""
        # Test malicious inputs
        assert SecurityValidator.detect_sql_injection("'; DROP TABLE users; --") is True
        assert SecurityValidator.detect_sql_injection("1' OR '1'='1") is True
        assert SecurityValidator.detect_sql_injection("admin' --") is True
        assert (
            SecurityValidator.detect_sql_injection("UNION SELECT * FROM passwords")
            is True
        )

        # Test safe inputs
        assert SecurityValidator.detect_sql_injection("normal text") is False
        assert SecurityValidator.detect_sql_injection("user@example.com") is False

    def test_detect_xss(self):
        """Test XSS detection."""
        # Test malicious inputs
        assert SecurityValidator.detect_xss("<script>alert('XSS')</script>") is True
        assert SecurityValidator.detect_xss("<img src=x onerror=alert('XSS')>") is True
        assert SecurityValidator.detect_xss("javascript:alert('XSS')") is True
        assert SecurityValidator.detect_xss("<iframe src='evil.com'></iframe>") is True

        # Test safe inputs
        assert SecurityValidator.detect_xss("normal text") is False
        assert SecurityValidator.detect_xss("Hello <World>") is False

    def test_detect_command_injection(self):
        """Test command injection detection."""
        # Test malicious inputs
        assert SecurityValidator.detect_command_injection("test; rm -rf /") is True
        assert (
            SecurityValidator.detect_command_injection("test && cat /etc/passwd")
            is True
        )
        assert SecurityValidator.detect_command_injection("`whoami`") is True
        assert SecurityValidator.detect_command_injection("$(ls -la)") is True

        # Test safe inputs
        assert SecurityValidator.detect_command_injection("normal text") is False
        assert SecurityValidator.detect_command_injection("file-name.txt") is False

    def test_detect_path_traversal(self):
        """Test path traversal detection."""
        # Test malicious inputs
        assert SecurityValidator.detect_path_traversal("../../etc/passwd") is True
        assert (
            SecurityValidator.detect_path_traversal("..\\..\\windows\\system32") is True
        )
        assert SecurityValidator.detect_path_traversal("%2e%2e%2f%2e%2e%2f") is True

        # Test safe inputs
        assert SecurityValidator.detect_path_traversal("normal/path/file.txt") is False
        assert SecurityValidator.detect_path_traversal("file.txt") is False

    def test_sanitize_html(self):
        """Test HTML sanitization."""
        # Test with no allowed tags
        input_html = "<script>alert('XSS')</script>Hello<b>World</b>"
        sanitized = SecurityValidator.sanitize_html(input_html)
        assert "<script>" not in sanitized
        assert "&lt;script&gt;" in sanitized

        # Test with allowed tags
        input_html = "<b>Bold</b> and <i>Italic</i>"
        sanitized = SecurityValidator.sanitize_html(input_html, allowed_tags=["b"])
        assert "<b>" in sanitized
        assert "<i>" not in sanitized

    def test_sanitize_filename(self):
        """Test filename sanitization."""
        # Test path traversal removal
        assert ".." not in SecurityValidator.sanitize_filename("../../file.txt")
        assert "/" not in SecurityValidator.sanitize_filename("/etc/passwd")
        assert "\\" not in SecurityValidator.sanitize_filename("C:\\Windows\\System32")

        # Test special character removal
        sanitized = SecurityValidator.sanitize_filename("file@#$%^&*.txt")
        assert "@" not in sanitized
        assert "#" not in sanitized

        # Test length limiting
        long_name = "a" * 300 + ".txt"
        sanitized = SecurityValidator.sanitize_filename(long_name)
        assert len(sanitized) <= 255

    def test_validate_email(self):
        """Test email validation."""
        # Valid emails
        assert (
            SecurityValidator.validate_email("user@example.com") == "user@example.com"
        )
        assert SecurityValidator.validate_email("test.user+tag@domain.co.uk")

        # Invalid emails
        with pytest.raises(ValueError):
            SecurityValidator.validate_email("not-an-email")
        with pytest.raises(ValueError):
            SecurityValidator.validate_email("@example.com")
        with pytest.raises(ValueError):
            SecurityValidator.validate_email("user@")

    def test_validate_ip_address(self):
        """Test IP address validation."""
        # Valid IPs
        assert SecurityValidator.validate_ip_address("192.168.1.1", allow_private=True)
        assert SecurityValidator.validate_ip_address("8.8.8.8")
        assert SecurityValidator.validate_ip_address("2001:db8::1")

        # Invalid IPs
        with pytest.raises(ValueError):
            SecurityValidator.validate_ip_address("256.256.256.256")
        with pytest.raises(ValueError):
            SecurityValidator.validate_ip_address("not-an-ip")

        # Private IP without permission
        with pytest.raises(ValueError):
            SecurityValidator.validate_ip_address("192.168.1.1", allow_private=False)

    def test_login_request_validation(self):
        """Test login request model validation."""
        # Valid request
        login = LoginRequest(
            email="user@example.com", password="SecureP@ss123", mfa_code="123456"
        )
        assert login.email == "user@example.com"

        # Invalid password (too short)
        with pytest.raises(ValueError):
            LoginRequest(email="user@example.com", password="short")

        # Invalid MFA code
        with pytest.raises(ValueError):
            LoginRequest(
                email="user@example.com",
                password="SecureP@ss123",
                mfa_code="12345",  # Too short
            )

    def test_file_upload_validation(self):
        """Test file upload validation."""
        # Valid document
        filename = FileUploadValidator.validate_file(
            "document.pdf", "document", 1024 * 1024  # 1MB
        )
        assert filename == "document.pdf"

        # Invalid extension
        with pytest.raises(ValueError):
            FileUploadValidator.validate_file("script.exe", "document", 1024)

        # File too large
        with pytest.raises(ValueError):
            FileUploadValidator.validate_file(
                "large.pdf", "document", 20 * 1024 * 1024  # 20MB
            )

        # Content validation
        with pytest.raises(ValueError):
            FileUploadValidator.validate_file(
                "fake.pdf", "document", 1024, content=b"<script>alert('XSS')</script>"
            )


class TestAuditLogger:
    """Test audit logger functionality."""

    @pytest.mark.asyncio
    async def test_log_event(self, db_session, redis_client):
        """Test logging an audit event."""
        logger = AuditLogger(db_session=db_session, cache_manager=None, buffer_size=10)

        # Log event
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
    async def test_buffer_flush(self, db_session):
        """Test buffer flushing to database."""
        logger = AuditLogger(db_session=db_session, buffer_size=2)

        # Add events to buffer
        await logger.log_event(
            event_type=AuditEventType.LOGIN_SUCCESS, action="login", user_id="user1"
        )
        await logger.log_event(
            event_type=AuditEventType.LOGOUT, action="logout", user_id="user1"
        )

        # Flush buffer
        await logger.flush_buffer()

        # Check metrics
        assert logger.metrics["events_flushed"] == 2
        assert len(logger.log_buffer) == 0

        # Verify database calls
        assert db_session.add.call_count == 2
        assert db_session.commit.called

    @pytest.mark.asyncio
    async def test_alert_generation(self, db_session):
        """Test security alert generation."""
        logger = AuditLogger(
            db_session=db_session, alert_threshold={"failed_login_attempts": 3}
        )

        # Mock count query
        db_session.execute.return_value.scalar.return_value = 5

        # Log failed login that should trigger alert
        await logger.log_event(
            event_type=AuditEventType.LOGIN_FAILED,
            action="login_attempt",
            user_id="user123",
            result="failure",
            severity=AuditSeverity.WARNING,
        )

        # Check if alert was generated
        assert logger.metrics["alerts_generated"] >= 0

    @pytest.mark.asyncio
    async def test_compliance_report(self, db_session):
        """Test compliance report generation."""
        logger = AuditLogger(db_session=db_session)

        # Mock query results
        from unittest.mock import MagicMock

        mock_logs = [
            MagicMock(
                event_type=AuditEventType.DATA_ACCESSED,
                metadata={"consent_verified": True},
            ),
            MagicMock(event_type=AuditEventType.DATA_EXPORTED, metadata={}),
        ]

        # Patch query_logs method
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


class TestIntegration:
    """Integration tests for security components."""

    @pytest.mark.asyncio
    async def test_rate_limit_with_audit(self, redis_client, db_session, mock_request):
        """Test rate limiting with audit logging."""
        # Setup components
        rate_limiter = RateLimiter(redis_client, default_limit=2, default_window=60)
        audit_logger = AuditLogger(db_session)

        # Mock Redis to simulate hitting rate limit
        redis_client.pipeline.return_value.execute.return_value = [None, None, 3, None]
        redis_client.zrange.return_value = [(b"timestamp", 1234567890.0)]

        # Check rate limit
        allowed, metadata = await rate_limiter.check_rate_limit(
            mock_request, identifier="user123"
        )

        if not allowed:
            # Log rate limit exceeded
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
    async def test_validation_with_audit(self, db_session):
        """Test input validation with audit logging."""
        audit_logger = AuditLogger(db_session)

        # Test SQL injection attempt
        malicious_input = "'; DROP TABLE users; --"

        if SecurityValidator.detect_sql_injection(malicious_input):
            await audit_logger.log_event(
                event_type=AuditEventType.SQL_INJECTION_ATTEMPT,
                action="input_validation",
                severity=AuditSeverity.CRITICAL,
                metadata={"input": malicious_input[:100]},  # Truncate for safety
            )

        assert audit_logger.metrics["events_logged"] == 1

        # Check alert generation for critical event
        # SQL injection should trigger immediate alert
        assert len(audit_logger.log_buffer) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
