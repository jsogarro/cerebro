"""
Security headers middleware.

Implements comprehensive security headers to protect against common
web vulnerabilities (XSS, clickjacking, MIME sniffing, etc.).
"""

import base64
import secrets
from typing import Any

from fastapi import Request
from fastapi.responses import Response


class SecurityHeadersMiddleware:
    """
    Middleware to add security headers to all responses.

    Implements OWASP security header recommendations including CSP,
    HSTS, X-Frame-Options, and more.
    """

    def __init__(
        self,
        csp_enabled: bool = True,
        hsts_enabled: bool = True,
        frame_options: str = "DENY",
        content_type_options: str = "nosniff",
        xss_protection: str = "1; mode=block",
        referrer_policy: str = "strict-origin-when-cross-origin",
        permissions_policy: dict[str, list[str]] | None = None,
        custom_headers: dict[str, str] | None = None,
        nonce_enabled: bool = True,
        report_uri: str | None = None,
    ):
        """
        Initialize security headers middleware.

        Args:
            csp_enabled: Enable Content Security Policy
            hsts_enabled: Enable HTTP Strict Transport Security
            frame_options: X-Frame-Options value (DENY, SAMEORIGIN)
            content_type_options: X-Content-Type-Options value
            xss_protection: X-XSS-Protection value
            referrer_policy: Referrer-Policy value
            permissions_policy: Permissions Policy configuration
            custom_headers: Additional custom headers
            nonce_enabled: Enable CSP nonce for inline scripts
            report_uri: CSP violation report URI
        """
        self.csp_enabled = csp_enabled
        self.hsts_enabled = hsts_enabled
        self.frame_options = frame_options
        self.content_type_options = content_type_options
        self.xss_protection = xss_protection
        self.referrer_policy = referrer_policy
        self.permissions_policy = (
            permissions_policy or self._default_permissions_policy()
        )
        self.custom_headers = custom_headers or {}
        self.nonce_enabled = nonce_enabled
        self.report_uri = report_uri

    def _default_permissions_policy(self) -> dict[str, list[str]]:
        """Get default permissions policy."""
        return {
            "accelerometer": [],
            "ambient-light-sensor": [],
            "autoplay": ["self"],
            "battery": [],
            "camera": [],
            "display-capture": [],
            "document-domain": [],
            "encrypted-media": ["self"],
            "fullscreen": ["self"],
            "geolocation": [],
            "gyroscope": [],
            "interest-cohort": [],  # FLoC opt-out
            "magnetometer": [],
            "microphone": [],
            "midi": [],
            "payment": [],
            "picture-in-picture": ["self"],
            "publickey-credentials-get": ["self"],
            "screen-wake-lock": [],
            "sync-xhr": ["self"],
            "usb": [],
            "vibrate": [],
            "xr-spatial-tracking": [],
        }

    def _generate_csp_header(self, nonce: str | None = None) -> str:
        """
        Generate Content Security Policy header.

        Args:
            nonce: CSP nonce for inline scripts

        Returns:
            CSP header value
        """
        directives = []

        # Default source
        directives.append("default-src 'self'")

        # Script source - with nonce if enabled
        if nonce:
            directives.append(f"script-src 'self' 'nonce-{nonce}' 'strict-dynamic'")
        else:
            directives.append("script-src 'self'")

        # Style source - allow inline styles with nonce
        if nonce:
            directives.append(f"style-src 'self' 'nonce-{nonce}'")
        else:
            directives.append("style-src 'self' 'unsafe-inline'")

        # Image source
        directives.append("img-src 'self' data: https:")

        # Font source
        directives.append("font-src 'self' data:")

        # Connect source (for API calls)
        directives.append("connect-src 'self' https:")

        # Media source
        directives.append("media-src 'self'")

        # Object source (plugins)
        directives.append("object-src 'none'")

        # Frame ancestors (clickjacking protection)
        directives.append("frame-ancestors 'none'")

        # Base URI
        directives.append("base-uri 'self'")

        # Form action
        directives.append("form-action 'self'")

        # Upgrade insecure requests
        directives.append("upgrade-insecure-requests")

        # Block all mixed content
        directives.append("block-all-mixed-content")

        # Report URI if configured
        if self.report_uri:
            directives.append(f"report-uri {self.report_uri}")
            directives.append("report-to csp-endpoint")

        return "; ".join(directives)

    def _generate_permissions_policy(self) -> str:
        """
        Generate Permissions Policy header.

        Returns:
            Permissions Policy header value
        """
        policies = []

        for feature, allowlist in self.permissions_policy.items():
            if not allowlist:
                policies.append(f"{feature}=()")
            elif allowlist == ["*"]:
                policies.append(f"{feature}=*")
            else:
                allowed = " ".join(
                    f'"{item}"' if item != "self" else item for item in allowlist
                )
                policies.append(f"{feature}=({allowed})")

        return ", ".join(policies)

    def _generate_hsts_header(self) -> str:
        """
        Generate HTTP Strict Transport Security header.

        Returns:
            HSTS header value
        """
        # Max age of 1 year (31536000 seconds)
        # Include subdomains and preload
        return "max-age=31536000; includeSubDomains; preload"

    def _generate_report_to_header(self) -> str | None:
        """
        Generate Report-To header for CSP reporting.

        Returns:
            Report-To header value or None
        """
        if not self.report_uri:
            return None

        import json

        report_to = {
            "group": "csp-endpoint",
            "max_age": 86400,
            "endpoints": [{"url": self.report_uri}],
        }

        return json.dumps(report_to)

    async def __call__(self, request: Request, call_next: Any) -> Response:
        """Apply security headers to response."""
        # Generate CSP nonce if enabled
        nonce = None
        if self.csp_enabled and self.nonce_enabled:
            nonce = base64.b64encode(secrets.token_bytes(16)).decode("utf-8")
            # Store nonce in request state for use in templates
            request.state.csp_nonce = nonce

        # Process request
        response: Response = await call_next(request)

        # Add security headers

        # Content Security Policy
        if self.csp_enabled:
            csp_header = self._generate_csp_header(nonce)
            response.headers["Content-Security-Policy"] = csp_header
            # Also add report-only version for testing
            response.headers["Content-Security-Policy-Report-Only"] = csp_header

        # HTTP Strict Transport Security
        if self.hsts_enabled:
            response.headers["Strict-Transport-Security"] = self._generate_hsts_header()

        # X-Frame-Options (clickjacking protection)
        response.headers["X-Frame-Options"] = self.frame_options

        # X-Content-Type-Options (MIME sniffing protection)
        response.headers["X-Content-Type-Options"] = self.content_type_options

        # X-XSS-Protection (XSS filter)
        response.headers["X-XSS-Protection"] = self.xss_protection

        # Referrer Policy
        response.headers["Referrer-Policy"] = self.referrer_policy

        # Permissions Policy (formerly Feature Policy)
        response.headers["Permissions-Policy"] = self._generate_permissions_policy()

        # Report-To header for CSP reporting
        if self.report_uri:
            report_to = self._generate_report_to_header()
            if report_to:
                response.headers["Report-To"] = report_to

        # Additional security headers
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"

        # Remove potentially dangerous headers
        headers_to_remove = [
            "Server",
            "X-Powered-By",
            "X-AspNet-Version",
            "X-AspNetMvc-Version",
        ]

        for header in headers_to_remove:
            if header in response.headers:
                del response.headers[header]

        # Add custom headers
        for header, value in self.custom_headers.items():
            response.headers[header] = value

        return response


class CORSSecurityMiddleware:
    """
    Secure CORS middleware with strict origin validation.

    Implements secure Cross-Origin Resource Sharing with origin
    validation, credentials support, and preflight caching.
    """

    def __init__(
        self,
        allowed_origins: list[str] | None = None,
        allowed_methods: list[str] | None = None,
        allowed_headers: list[str] | None = None,
        exposed_headers: list[str] | None = None,
        allow_credentials: bool = False,
        max_age: int = 3600,
        origin_validator: Any | None = None,
    ):
        """
        Initialize CORS middleware.

        Args:
            allowed_origins: List of allowed origins
            allowed_methods: List of allowed HTTP methods
            allowed_headers: List of allowed headers
            exposed_headers: List of exposed headers
            allow_credentials: Whether to allow credentials
            max_age: Preflight cache duration
            origin_validator: Custom origin validation function
        """
        self.allowed_origins = allowed_origins or ["https://localhost:3000"]
        self.allowed_methods = allowed_methods or [
            "GET",
            "POST",
            "PUT",
            "DELETE",
            "OPTIONS",
        ]
        self.allowed_headers = allowed_headers or [
            "Content-Type",
            "Authorization",
            "X-Request-ID",
            "X-API-Key",
        ]
        self.exposed_headers = exposed_headers or [
            "X-Request-ID",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
        ]
        self.allow_credentials = allow_credentials
        self.max_age = max_age
        self.origin_validator = origin_validator

    def _is_origin_allowed(self, origin: str) -> bool:
        """
        Check if origin is allowed.

        Args:
            origin: Origin to check

        Returns:
            True if origin is allowed
        """
        # Custom validator takes precedence
        if self.origin_validator:
            return bool(self.origin_validator(origin))

        # Check against allowed origins list
        if "*" in self.allowed_origins:
            return True

        # Direct match
        if origin in self.allowed_origins:
            return True

        # Check for wildcard subdomains
        for allowed in self.allowed_origins:
            if allowed.startswith("*."):
                domain = allowed[2:]
                if origin.endswith(domain):
                    return True

        return False

    async def __call__(self, request: Request, call_next: Any) -> Response:
        """Process CORS headers."""
        origin = request.headers.get("Origin")

        # Handle preflight requests
        if request.method == "OPTIONS":
            preflight_response = Response()

            if origin and self._is_origin_allowed(origin):
                preflight_response.headers["Access-Control-Allow-Origin"] = origin
                preflight_response.headers["Access-Control-Allow-Methods"] = ", ".join(
                    self.allowed_methods
                )
                preflight_response.headers["Access-Control-Allow-Headers"] = ", ".join(
                    self.allowed_headers
                )
                preflight_response.headers["Access-Control-Max-Age"] = str(self.max_age)

                if self.allow_credentials:
                    preflight_response.headers["Access-Control-Allow-Credentials"] = "true"

            return preflight_response

        # Process actual request
        response: Response = await call_next(request)

        # Add CORS headers if origin is allowed
        if origin and self._is_origin_allowed(origin):
            response.headers["Access-Control-Allow-Origin"] = origin

            if self.exposed_headers:
                response.headers["Access-Control-Expose-Headers"] = ", ".join(
                    self.exposed_headers
                )

            if self.allow_credentials:
                response.headers["Access-Control-Allow-Credentials"] = "true"

            # Add Vary header to indicate response varies by origin
            existing_vary = response.headers.get("Vary", "")
            if existing_vary:
                response.headers["Vary"] = f"{existing_vary}, Origin"
            else:
                response.headers["Vary"] = "Origin"

        return response
