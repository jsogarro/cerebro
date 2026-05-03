"""
Test suite for security components.

Tests rate limiting, security headers, validators, and authentication features.
"""

from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
import redis.asyncio as redis
from fastapi import Request

from src.security.headers import CORSSecurityMiddleware, SecurityHeadersMiddleware
from src.security.rate_limiter import RateLimiter, RateLimitStrategy
from src.security.validators import (
    FileUploadValidator,
    LoginRequest,
    SecurityValidator,
)


@pytest.fixture
async def redis_client() -> Any:
    """Create a mock Redis client."""
    client = AsyncMock(spec=redis.Redis)
    pipeline = Mock()
    pipeline.execute = AsyncMock()
    client.pipeline = Mock(return_value=pipeline)
    client.zrange = AsyncMock(return_value=[])
    client.scan = AsyncMock(return_value=(0, []))
    client.delete = AsyncMock(return_value=0)
    return client


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


class TestRateLimiter:
    """Test rate limiter functionality."""

    @pytest.mark.asyncio
    async def test_sliding_window_rate_limit(
        self, redis_client: Any, mock_request: Request
    ) -> None:
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
    async def test_rate_limit_exceeded(
        self, redis_client: Any, mock_request: Request
    ) -> None:
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
    async def test_token_bucket_rate_limit(
        self, redis_client: Any, mock_request: Request
    ) -> None:
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
    async def test_reset_rate_limit(self, redis_client: Any) -> None:
        """Test resetting rate limits."""
        rate_limiter = RateLimiter(redis_client)

        redis_client.scan.return_value = (0, [b"rate_limit:user:123:abc"])
        redis_client.delete.return_value = 1

        result = await rate_limiter.reset_rate_limit("user123")
        assert result is True


class TestSecurityHeaders:
    """Test security headers middleware."""

    @pytest.mark.asyncio
    async def test_security_headers_applied(self) -> None:
        """Test that security headers are applied to responses."""
        middleware = SecurityHeadersMiddleware(
            csp_enabled=True, hsts_enabled=True, frame_options="DENY"
        )

        # Create mock request and response
        request = Mock(spec=Request)
        request.state = Mock()

        response = Mock()
        response.headers = {}

        async def call_next(req: Request) -> Mock:
            return response

        result = await middleware(request, call_next)

        # Check headers are set
        assert "Content-Security-Policy" in result.headers
        assert "Strict-Transport-Security" in result.headers
        assert result.headers["X-Frame-Options"] == "DENY"
        assert result.headers["X-Content-Type-Options"] == "nosniff"
        assert result.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    @pytest.mark.asyncio
    async def test_csp_nonce_generation(self) -> None:
        """Test CSP nonce generation."""
        middleware = SecurityHeadersMiddleware(csp_enabled=True, nonce_enabled=True)

        request = Mock(spec=Request)
        request.state = Mock()
        response = Mock()
        response.headers = {}

        async def call_next(req: Request) -> Mock:
            # Check nonce was added to request state
            assert hasattr(req.state, "csp_nonce")
            assert len(req.state.csp_nonce) > 0
            return response

        result = await middleware(request, call_next)

        # Check CSP header contains nonce
        csp_header = result.headers.get("Content-Security-Policy", "")
        assert "nonce-" in csp_header

    @pytest.mark.asyncio
    async def test_cors_security(self) -> None:
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

        async def call_next(req: Request) -> Mock:
            return response

        result = await middleware(request, call_next)

        assert result.headers["Access-Control-Allow-Origin"] == "https://example.com"
        assert result.headers["Access-Control-Allow-Credentials"] == "true"

    @pytest.mark.asyncio
    async def test_cors_preflight(self) -> None:
        """Test CORS preflight request handling."""
        middleware = CORSSecurityMiddleware(
            allowed_origins=["https://example.com"],
            allowed_methods=["GET", "POST"],
            max_age=3600,
        )

        request = Mock(spec=Request)
        request.headers = {"Origin": "https://example.com"}
        request.method = "OPTIONS"

        async def call_next(req: Request) -> Mock:
            return Mock()

        result = await middleware(request, call_next)

        assert result.headers["Access-Control-Allow-Origin"] == "https://example.com"
        assert "GET, POST" in result.headers["Access-Control-Allow-Methods"]
        assert result.headers["Access-Control-Max-Age"] == "3600"


class TestSecurityValidators:
    """Test security validators."""

    def test_detect_sql_injection(self) -> None:
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

    def test_detect_xss(self) -> None:
        """Test XSS detection."""
        # Test malicious inputs
        assert SecurityValidator.detect_xss("<script>alert('XSS')</script>") is True
        assert SecurityValidator.detect_xss("<img src=x onerror=alert('XSS')>") is True
        assert SecurityValidator.detect_xss("javascript:alert('XSS')") is True
        assert SecurityValidator.detect_xss("<iframe src='evil.com'></iframe>") is True

        # Test safe inputs
        assert SecurityValidator.detect_xss("normal text") is False
        assert SecurityValidator.detect_xss("Hello <World>") is False

    def test_detect_command_injection(self) -> None:
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

    def test_detect_path_traversal(self) -> None:
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

    def test_sanitize_html(self) -> None:
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

    def test_sanitize_filename(self) -> None:
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

    def test_validate_email(self, mocker: Any) -> None:
        """Test email validation.

        ``email_validator`` performs DNS deliverability checks by default;
        the RFC-2606 reserved test domains (``example.com``, ``domain.co.uk``)
        deliberately reject MX lookups, which would make this test brittle
        and network-dependent. Patch the underlying validator to skip the
        deliverability step so the format/structure assertions still run.
        """
        import email_validator

        original_validate = email_validator.validate_email

        def _format_only(email: str, **_: Any) -> Any:
            return original_validate(email, check_deliverability=False)

        mocker.patch.object(
            email_validator, "validate_email", side_effect=_format_only
        )

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

    def test_validate_ip_address(self) -> None:
        """Test IP address validation."""
        # Valid IPs
        assert SecurityValidator.validate_ip_address("192.168.1.1", allow_private=True)
        assert SecurityValidator.validate_ip_address("8.8.8.8")
        assert SecurityValidator.validate_ip_address("2606:4700:4700::1111")

        # Invalid IPs
        with pytest.raises(ValueError):
            SecurityValidator.validate_ip_address("256.256.256.256")
        with pytest.raises(ValueError):
            SecurityValidator.validate_ip_address("not-an-ip")

        # Private IP without permission
        with pytest.raises(ValueError):
            SecurityValidator.validate_ip_address("192.168.1.1", allow_private=False)

    def test_login_request_validation(self, mocker: Any) -> None:
        """Test login request model validation.

        Same DNS-skip rationale as ``test_validate_email``: ``LoginRequest``
        delegates email validation to ``email_validator.validate_email``,
        which would otherwise refuse the RFC-2606 ``example.com`` test
        domain and make this test network-dependent.

        NOTE (tech debt): two ``LoginRequest`` classes exist — this one at
        ``src/security/validators.py:405`` and a duplicate at
        ``src/auth/models.py:69``. Consolidate in a follow-up PR; out of
        scope for the stabilization milestone.
        """
        import email_validator

        original_validate = email_validator.validate_email

        def _format_only(email: str, **_: Any) -> Any:
            return original_validate(email, check_deliverability=False)

        mocker.patch.object(
            email_validator, "validate_email", side_effect=_format_only
        )

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

    def test_file_upload_validation(self) -> None:
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
