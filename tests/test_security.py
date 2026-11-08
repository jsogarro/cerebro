"""
Test suite for security components.

Tests security headers and login request validation.
"""

from typing import Any
from unittest.mock import Mock

import pytest
from fastapi import Request

from src.security.headers import CORSSecurityMiddleware, SecurityHeadersMiddleware


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


class TestLoginRequestPasswordPolicy:
    """Production login request validation."""

    def test_production_login_request_password_policy(self, mocker: Any) -> None:
        """Production ``LoginRequest`` enforces full password complexity.

        Same DNS-skip rationale as ``test_validate_email``: ``EmailStr``
        delegates to ``email_validator.validate_email``, which would
        otherwise refuse the RFC-2606 ``example.com`` test domain.
        """
        import email_validator

        from src.auth.models import LoginRequest

        original_validate = email_validator.validate_email

        def _format_only(email: str, **_: Any) -> Any:
            return original_validate(email, check_deliverability=False)

        mocker.patch.object(email_validator, "validate_email", side_effect=_format_only)

        # Valid request satisfying all four complexity requirements.
        login = LoginRequest(email="user@example.com", password="SecureP@ssw0rd!")
        assert login.email == "user@example.com"

        # Too short (under 12 chars).
        with pytest.raises(ValueError):
            LoginRequest(email="user@example.com", password="Short1!")

        # Missing uppercase.
        with pytest.raises(ValueError):
            LoginRequest(email="user@example.com", password="securep@ssw0rd!")

        # Missing lowercase.
        with pytest.raises(ValueError):
            LoginRequest(email="user@example.com", password="SECUREP@SSW0RD!")

        # Missing digit.
        with pytest.raises(ValueError):
            LoginRequest(email="user@example.com", password="SecurePassword!")

        # Missing special character.
        with pytest.raises(ValueError):
            LoginRequest(email="user@example.com", password="SecurePassw0rd1")
