"""
Request validators for security.

Provides input validation, sanitization, and security checks for
incoming requests to prevent injection attacks and data corruption.
"""

import html
import ipaddress
import re
import urllib.parse
from typing import Any

import email_validator
from pydantic import BaseModel, EmailStr, Field, field_validator


class SecurityValidator:
    """
    Core security validator for input sanitization and validation.

    Prevents common injection attacks including SQL injection, XSS,
    command injection, and path traversal.
    """

    # Patterns for detecting malicious input
    SQL_INJECTION_PATTERNS = [
        r"(\b(SELECT|INSERT|UPDATE|DELETE|DROP|UNION|CREATE|ALTER|EXEC|EXECUTE|SCRIPT|TRUNCATE)\b)",
        r"(--|\||;|\/\*|\*\/|@@|xp_|sp_|0x)",
        r"(\bOR\b\s*\d+\s*=\s*\d+)",
        r"(\bAND\b\s*\d+\s*=\s*\d+)",
        r"(\'|\"|`|´|'|'|\"|\")",  # noqa: RUF001
        r"(\bWHERE\b.*=.*)",
    ]

    XSS_PATTERNS = [
        r"(<script[^>]*>.*?</script>)",
        r"(<iframe[^>]*>.*?</iframe>)",
        r"(javascript:|vbscript:|data:text/html)",
        r"(on\w+\s*=)",  # Event handlers
        r"(<img[^>]*src[^>]*>)",
        r"(<object[^>]*>)",
        r"(<embed[^>]*>)",
        r"(<applet[^>]*>)",
        r"(alert\s*\(|confirm\s*\(|prompt\s*\()",
        r"(document\.|window\.|eval\s*\()",
    ]

    COMMAND_INJECTION_PATTERNS = [
        r"([;&|`$])",
        r"(\.\./|\.\.\\)",  # Path traversal
        r"(/etc/passwd|/etc/shadow|/windows/system32)",
        r"(cmd\.exe|powershell|bash|sh\s)",
        r"(\||\||&&|;|`|\$\(|\${)",
        r"(nc\s|netcat\s|wget\s|curl\s|telnet\s)",
    ]

    PATH_TRAVERSAL_PATTERNS = [
        r"(\.\./|\.\./)",
        r"(\.\.\\|\.\.\\\\)",
        r"(%2e%2e%2f|%2e%2e/|\.%2e/|%2e%2e%5c)",
        r"(\/\.\.\/|\\\.\.\\)",
        r"(\.\.;|\.\.%00|\.\.%01)",
    ]

    LDAP_INJECTION_PATTERNS = [
        r"(\*|\(|\)|\\|/|NUL)",
        r"(\|\(|\)\|)",
        r"(&|\||!)",
    ]

    XML_INJECTION_PATTERNS = [
        r"(<!DOCTYPE[^>]*>)",
        r"(<!ENTITY[^>]*>)",
        r"(<!\[CDATA\[)",
        r"(SYSTEM\s+[\"']file:)",
    ]

    @classmethod
    def detect_sql_injection(cls, value: str) -> bool:
        """
        Detect potential SQL injection attempts.

        Args:
            value: Input string to check

        Returns:
            True if SQL injection pattern detected
        """
        if not value:
            return False

        value_upper = value.upper()
        for pattern in cls.SQL_INJECTION_PATTERNS:
            if re.search(pattern, value_upper, re.IGNORECASE):
                return True
        return False

    @classmethod
    def detect_xss(cls, value: str) -> bool:
        """
        Detect potential XSS attempts.

        Args:
            value: Input string to check

        Returns:
            True if XSS pattern detected
        """
        if not value:
            return False

        value_lower = value.lower()
        for pattern in cls.XSS_PATTERNS:
            if re.search(pattern, value_lower, re.IGNORECASE):
                return True
        return False

    @classmethod
    def detect_command_injection(cls, value: str) -> bool:
        """
        Detect potential command injection attempts.

        Args:
            value: Input string to check

        Returns:
            True if command injection pattern detected
        """
        if not value:
            return False

        for pattern in cls.COMMAND_INJECTION_PATTERNS:
            if re.search(pattern, value, re.IGNORECASE):
                return True
        return False

    @classmethod
    def detect_path_traversal(cls, value: str) -> bool:
        """
        Detect potential path traversal attempts.

        Args:
            value: Input string to check

        Returns:
            True if path traversal pattern detected
        """
        if not value:
            return False

        for pattern in cls.PATH_TRAVERSAL_PATTERNS:
            if re.search(pattern, value, re.IGNORECASE):
                return True
        return False

    @classmethod
    def sanitize_html(cls, value: str, allowed_tags: list[str] | None = None) -> str:
        """
        Sanitize HTML input to prevent XSS.

        Args:
            value: HTML string to sanitize
            allowed_tags: List of allowed HTML tags

        Returns:
            Sanitized HTML string
        """
        if not value:
            return value

        # Basic HTML escaping
        value = html.escape(value)

        # If allowed tags specified, unescape them
        if allowed_tags:
            for tag in allowed_tags:
                # Very basic - in production use a proper HTML sanitizer
                value = value.replace(f"&lt;{tag}&gt;", f"<{tag}>")
                value = value.replace(f"&lt;/{tag}&gt;", f"</{tag}>")

        return value

    @classmethod
    def sanitize_filename(cls, filename: str) -> str:
        """
        Sanitize filename to prevent path traversal.

        Args:
            filename: Filename to sanitize

        Returns:
            Sanitized filename
        """
        if not filename:
            return filename

        # Remove path components
        filename = filename.replace("..", "")
        filename = filename.replace("/", "")
        filename = filename.replace("\\", "")
        filename = filename.replace("\x00", "")

        # Remove special characters
        filename = re.sub(r"[^a-zA-Z0-9._-]", "", filename)

        # Limit length
        max_length = 255
        if len(filename) > max_length:
            name, ext = filename.rsplit(".", 1) if "." in filename else (filename, "")
            filename = (
                name[: max_length - len(ext) - 1] + "." + ext
                if ext
                else name[:max_length]
            )

        return filename

    @classmethod
    def sanitize_url(cls, url: str, allowed_schemes: list[str] | None = None) -> str:
        """
        Sanitize URL to prevent open redirect and XSS.

        Args:
            url: URL to sanitize
            allowed_schemes: Allowed URL schemes

        Returns:
            Sanitized URL
        """
        if not url:
            return url

        allowed_schemes = allowed_schemes or ["http", "https"]

        # Parse URL
        parsed = urllib.parse.urlparse(url)

        # Check scheme
        if parsed.scheme and parsed.scheme not in allowed_schemes:
            raise ValueError(f"Invalid URL scheme: {parsed.scheme}")

        # Prevent javascript: and data: URLs
        if parsed.scheme in ["javascript", "data", "vbscript"]:
            raise ValueError(f"Dangerous URL scheme: {parsed.scheme}")

        # Rebuild URL with only safe components
        safe_url = urllib.parse.urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                "",  # Remove fragment to prevent DOM XSS
            )
        )

        return safe_url

    @classmethod
    def validate_email(cls, email: str) -> str:
        """
        Validate and normalize email address.

        Args:
            email: Email address to validate

        Returns:
            Normalized email address
        """
        try:
            # Validate email format
            validated = email_validator.validate_email(email)
            return validated.email
        except email_validator.EmailNotValidError as e:
            raise ValueError(f"Invalid email address: {e!s}") from e

    @classmethod
    def validate_ip_address(cls, ip: str, allow_private: bool = False) -> str:
        """
        Validate IP address.

        Args:
            ip: IP address to validate
            allow_private: Whether to allow private IP addresses

        Returns:
            Validated IP address
        """
        try:
            ip_obj = ipaddress.ip_address(ip)

            if not allow_private and ip_obj.is_private:
                raise ValueError("Private IP addresses not allowed")

            if ip_obj.is_multicast:
                raise ValueError("Multicast IP addresses not allowed")

            if ip_obj.is_reserved:
                raise ValueError("Reserved IP addresses not allowed")

            return str(ip_obj)
        except ValueError as e:
            raise ValueError(f"Invalid IP address: {e!s}") from e

    @classmethod
    def validate_input(
        cls,
        value: Any,
        input_type: str = "text",
        max_length: int | None = None,
        allow_html: bool = False,
        custom_patterns: list[str] | None = None,
    ) -> Any:
        """
        Comprehensive input validation.

        Args:
            value: Input value to validate
            input_type: Type of input (text, email, url, filename, etc.)
            max_length: Maximum allowed length
            allow_html: Whether to allow HTML content
            custom_patterns: Custom regex patterns to check against

        Returns:
            Validated and sanitized input

        Raises:
            ValueError: If validation fails
        """
        if value is None:
            return value

        # Convert to string for validation
        str_value = str(value)

        # Check length
        if max_length and len(str_value) > max_length:
            raise ValueError(f"Input exceeds maximum length of {max_length}")

        # Check for injection attacks
        if cls.detect_sql_injection(str_value):
            raise ValueError("Potential SQL injection detected")

        if not allow_html and cls.detect_xss(str_value):
            raise ValueError("Potential XSS attack detected")

        if cls.detect_command_injection(str_value):
            raise ValueError("Potential command injection detected")

        if input_type == "filename" and cls.detect_path_traversal(str_value):
            raise ValueError("Potential path traversal detected")

        # Check custom patterns
        if custom_patterns:
            for pattern in custom_patterns:
                if re.search(pattern, str_value):
                    raise ValueError("Input matches forbidden pattern")

        # Type-specific validation and sanitization
        if input_type == "email":
            return cls.validate_email(str_value)
        elif input_type == "url":
            return cls.sanitize_url(str_value)
        elif input_type == "filename":
            return cls.sanitize_filename(str_value)
        elif input_type == "html":
            return cls.sanitize_html(str_value)
        elif input_type == "ip":
            return cls.validate_ip_address(str_value)
        else:
            # Default text sanitization
            if not allow_html:
                str_value = html.escape(str_value)
            return str_value


# Pydantic models for request validation


from typing import Annotated  # noqa: E402

SecureStringField = Annotated[str, "SecureString"]


def validate_secure_string(v: str) -> str:
    """Validate secure string field with injection prevention."""
    if not isinstance(v, str):
        raise TypeError("string required")

    # Check for injection attempts
    if SecurityValidator.detect_sql_injection(v):
        raise ValueError("Potential SQL injection detected")

    if SecurityValidator.detect_xss(v):
        raise ValueError("Potential XSS detected")

    if SecurityValidator.detect_command_injection(v):
        raise ValueError("Potential command injection detected")

    return v


class LoginRequest(BaseModel):
    """Secure login request model."""

    email: EmailStr = Field(..., description="User email address")
    password: Annotated[str, Field(min_length=8, max_length=128)] = Field(
        ..., description="User password"
    )
    mfa_code: Annotated[str, Field(pattern=r"^\d{6}$")] | None = Field(None, description="MFA code")
    remember_me: bool = Field(False, description="Remember session")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return SecurityValidator.validate_email(v)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        # Basic password validation - expand as needed
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain lowercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain digit")
        return v


class RegisterRequest(BaseModel):
    """Secure registration request model."""

    email: EmailStr = Field(..., description="Email address")
    username: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{3,32}$")] = Field(
        ..., description="Username"
    )
    password: Annotated[str, Field(min_length=8, max_length=128)] = Field(..., description="Password")
    full_name: SecureStringField | None = Field(None, max_length=255)
    organization: SecureStringField | None = Field(None, max_length=255)
    terms_accepted: bool = Field(..., description="Terms acceptance")

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        # Check for reserved usernames
        reserved = ["admin", "root", "system", "api", "test"]
        if v.lower() in reserved:
            raise ValueError("Username is reserved")
        return v


class ResearchProjectRequest(BaseModel):
    """Secure research project request model."""

    title: SecureStringField = Field(..., max_length=500)
    description: SecureStringField | None = Field(None, max_length=5000)
    query: SecureStringField = Field(..., max_length=1000)
    domains: list[SecureStringField] = Field(..., max_length=10)

    @field_validator("domains")
    @classmethod
    def validate_domains(cls, v: list[str]) -> list[str]:
        # Ensure unique domains
        if len(v) != len(set(v)):
            raise ValueError("Duplicate domains not allowed")
        return v


class FileUploadValidator:
    """Validator for file uploads."""

    ALLOWED_EXTENSIONS = {
        "document": [".pdf", ".doc", ".docx", ".txt", ".md", ".rtf"],
        "image": [".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp"],
        "data": [".csv", ".json", ".xml", ".xlsx", ".xls"],
        "archive": [".zip", ".tar", ".gz", ".7z"],
    }

    MAX_FILE_SIZES = {
        "document": 10 * 1024 * 1024,  # 10MB
        "image": 5 * 1024 * 1024,  # 5MB
        "data": 50 * 1024 * 1024,  # 50MB
        "archive": 100 * 1024 * 1024,  # 100MB
    }

    @classmethod
    def validate_file(
        cls,
        filename: str,
        file_type: str,
        file_size: int,
        content: bytes | None = None,
    ) -> str:
        """
        Validate uploaded file.

        Args:
            filename: Original filename
            file_type: Type category (document, image, etc.)
            file_size: File size in bytes
            content: File content for deep inspection

        Returns:
            Sanitized filename

        Raises:
            ValueError: If validation fails
        """
        # Sanitize filename
        safe_filename = SecurityValidator.sanitize_filename(filename)

        # Check extension
        ext = (
            "." + safe_filename.rsplit(".", 1)[1].lower()
            if "." in safe_filename
            else ""
        )
        allowed_exts = cls.ALLOWED_EXTENSIONS.get(file_type, [])

        if ext not in allowed_exts:
            raise ValueError(f"File type {ext} not allowed")

        # Check file size
        max_size = cls.MAX_FILE_SIZES.get(file_type, 10 * 1024 * 1024)
        if file_size > max_size:
            raise ValueError(f"File size exceeds maximum of {max_size} bytes")

        # Deep content inspection if provided
        if content:
            # Check for malicious content patterns
            if b"<script" in content.lower():
                raise ValueError("Potential malicious script detected")

            # Check magic bytes for file type verification
            # This is a basic check - expand based on requirements
            if ext == ".pdf" and not content.startswith(b"%PDF"):
                raise ValueError("Invalid PDF file")
            elif ext in [".jpg", ".jpeg"] and not content.startswith(b"\xff\xd8\xff"):
                raise ValueError("Invalid JPEG file")
            elif ext == ".png" and not content.startswith(b"\x89PNG"):
                raise ValueError("Invalid PNG file")

        return safe_filename
