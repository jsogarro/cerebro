"""
Authentication data models.

Pydantic models for authentication requests and responses.
"""

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


class TokenPayload(BaseModel):
    """JWT token payload structure."""

    sub: str = Field(..., description="Subject (user ID)")
    email: str | None = Field(None, description="User email")
    roles: list[str] = Field(default_factory=list, description="User roles")
    permissions: list[str] = Field(default_factory=list, description="User permissions")
    jti: str = Field(..., description="JWT ID (unique token identifier)")
    iat: datetime = Field(..., description="Issued at timestamp")
    exp: datetime = Field(..., description="Expiration timestamp")
    device_id: str | None = Field(None, description="Device fingerprint")

    def has_role(self, role: str) -> bool:
        """Check if user has specific role."""
        return role in self.roles

    def has_permission(self, permission: str) -> bool:
        """Check if user has specific permission."""
        return permission in self.permissions

    def has_any_role(self, roles: list[str]) -> bool:
        """Check if user has any of the specified roles."""
        return any(role in self.roles for role in roles)

    def has_any_permission(self, permissions: list[str]) -> bool:
        """Check if user has any of the specified permissions."""
        return any(perm in self.permissions for perm in permissions)

    def is_expired(self) -> bool:
        """Check if token is expired."""
        return datetime.utcnow() > self.exp

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class TokenPair(BaseModel):
    """Access and refresh token pair."""

    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(..., description="JWT refresh token")
    token_type: str = Field(default="Bearer", description="Token type")
    expires_in: int = Field(..., description="Access token expiry in seconds")

    class Config:
        schema_extra = {
            "example": {
                "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
                "refresh_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "Bearer",
                "expires_in": 900,
            }
        }


class LoginRequest(BaseModel):
    """Login request with email and password."""

    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=8, description="User password")
    device_id: str | None = Field(None, description="Device fingerprint")
    remember_me: bool = Field(default=False, description="Extended session duration")

    class Config:
        schema_extra = {
            "example": {
                "email": "user@example.com",
                "password": "SecurePassword123!",
                "device_id": "device-fingerprint-123",
                "remember_me": False,
            }
        }


class RegisterRequest(BaseModel):
    """User registration request."""

    email: EmailStr = Field(..., description="Email address")
    username: str = Field(..., min_length=3, max_length=50, description="Username")
    password: str = Field(
        ..., min_length=12, description="Password (min 12 characters)"
    )
    confirm_password: str = Field(..., description="Password confirmation")
    full_name: str | None = Field(None, max_length=255, description="Full name")
    organization: str | None = Field(
        None, max_length=255, description="Organization"
    )
    accept_terms: bool = Field(..., description="Accept terms of service")

    @field_validator("username")
    def validate_username(cls, v: str) -> str:
        """Validate username format."""
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError(
                "Username must contain only letters, numbers, underscores, and hyphens"
            )
        return v.lower()

    @field_validator("password")
    def validate_password_strength(cls, v: str) -> str:
        """Validate password strength."""
        if len(v) < 12:
            raise ValueError("Password must be at least 12 characters long")

        # Check for required character types
        has_upper = any(c.isupper() for c in v)
        has_lower = any(c.islower() for c in v)
        has_digit = any(c.isdigit() for c in v)
        has_special = any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in v)

        if not (has_upper and has_lower and has_digit and has_special):
            raise ValueError(
                "Password must contain uppercase, lowercase, digit, and special character"
            )

        return v

    @field_validator("confirm_password")
    @classmethod
    def validate_password_match(cls, v: str, info: Any) -> str:
        """Validate password confirmation matches."""
        password = info.data.get("password")
        if password and v != password:
            raise ValueError("Passwords do not match")
        return v

    @field_validator("accept_terms")
    def validate_terms_accepted(cls, v: bool) -> bool:
        """Validate terms acceptance."""
        if not v:
            raise ValueError("You must accept the terms of service")
        return v

    class Config:
        schema_extra = {
            "example": {
                "email": "newuser@example.com",
                "username": "newuser",
                "password": "SecurePass123!@#",
                "confirm_password": "SecurePass123!@#",
                "full_name": "John Doe",
                "organization": "Research Institute",
                "accept_terms": True,
            }
        }


class RefreshRequest(BaseModel):
    """Token refresh request."""

    refresh_token: str = Field(..., description="Valid refresh token")
    device_id: str | None = Field(None, description="Device fingerprint")

    class Config:
        schema_extra = {
            "example": {
                "refresh_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
                "device_id": "device-fingerprint-123",
            }
        }


class PasswordResetRequest(BaseModel):
    """Password reset request."""

    email: EmailStr = Field(..., description="Email address for password reset")

    class Config:
        schema_extra = {"example": {"email": "user@example.com"}}


class PasswordResetConfirm(BaseModel):
    """Password reset confirmation."""

    token: str = Field(..., description="Password reset token")
    new_password: str = Field(..., min_length=12, description="New password")
    confirm_password: str = Field(..., description="Confirm new password")

    @field_validator("new_password")
    def validate_password_strength(cls, v: str) -> str:
        """Validate password strength."""
        if len(v) < 12:
            raise ValueError("Password must be at least 12 characters long")

        has_upper = any(c.isupper() for c in v)
        has_lower = any(c.islower() for c in v)
        has_digit = any(c.isdigit() for c in v)
        has_special = any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in v)

        if not (has_upper and has_lower and has_digit and has_special):
            raise ValueError(
                "Password must contain uppercase, lowercase, digit, and special character"
            )

        return v

    @field_validator("confirm_password")
    @classmethod
    def validate_password_match(cls, v: str, info: Any) -> str:
        """Validate password confirmation matches."""
        new_password = info.data.get("new_password")
        if new_password and v != new_password:
            raise ValueError("Passwords do not match")
        return v

    class Config:
        schema_extra = {
            "example": {
                "token": "reset-token-abc123",
                "new_password": "NewSecurePass123!@#",
                "confirm_password": "NewSecurePass123!@#",
            }
        }


class ChangePasswordRequest(BaseModel):
    """Change password request for authenticated users."""

    current_password: str = Field(..., description="Current password")
    new_password: str = Field(..., min_length=12, description="New password")
    confirm_password: str = Field(..., description="Confirm new password")

    @field_validator("new_password")
    def validate_password_strength(cls, v: str) -> str:
        """Validate password strength."""
        if len(v) < 12:
            raise ValueError("Password must be at least 12 characters long")

        has_upper = any(c.isupper() for c in v)
        has_lower = any(c.islower() for c in v)
        has_digit = any(c.isdigit() for c in v)
        has_special = any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in v)

        if not (has_upper and has_lower and has_digit and has_special):
            raise ValueError(
                "Password must contain uppercase, lowercase, digit, and special character"
            )

        return v

    @field_validator("confirm_password")
    @classmethod
    def validate_password_match(cls, v: str, info: Any) -> str:
        """Validate password confirmation matches."""
        new_password = info.data.get("new_password")
        if new_password and v != new_password:
            raise ValueError("Passwords do not match")
        return v

    @field_validator("new_password")
    @classmethod
    def validate_different_password(cls, v: str, info: Any) -> str:
        """Validate new password is different from current."""
        current_password = info.data.get("current_password")
        if current_password and v == current_password:
            raise ValueError("New password must be different from current password")
        return v


class EmailVerificationRequest(BaseModel):
    """Email verification request."""

    token: str = Field(..., description="Email verification token")

    class Config:
        schema_extra = {"example": {"token": "verification-token-xyz789"}}


class UserResponse(BaseModel):
    """User response model."""

    id: UUID = Field(..., description="User ID")
    email: str = Field(..., description="Email address")
    username: str = Field(..., description="Username")
    full_name: str | None = Field(None, description="Full name")
    organization: str | None = Field(None, description="Organization")
    role: str | None = Field(None, description="User role")
    is_active: bool = Field(..., description="Account active status")
    is_verified: bool = Field(..., description="Email verification status")
    is_superuser: bool = Field(..., description="Superuser status")
    created_at: datetime = Field(..., description="Account creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    last_login: datetime | None = Field(None, description="Last login timestamp")

    class Config:
        from_attributes = True
        json_encoders = {datetime: lambda v: v.isoformat(), UUID: lambda v: str(v)}


class AuthResponse(BaseModel):
    """Authentication response with user and tokens."""

    user: UserResponse = Field(..., description="User information")
    tokens: TokenPair = Field(..., description="Authentication tokens")

    class Config:
        schema_extra = {
            "example": {
                "user": {
                    "id": "123e4567-e89b-12d3-a456-426614174000",
                    "email": "user@example.com",
                    "username": "johndoe",
                    "full_name": "John Doe",
                    "organization": "Research Institute",
                    "role": "researcher",
                    "is_active": True,
                    "is_verified": True,
                    "is_superuser": False,
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                    "last_login": "2024-01-15T10:30:00Z",
                },
                "tokens": {
                    "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "refresh_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "token_type": "Bearer",
                    "expires_in": 900,
                },
            }
        }


class SessionInfo(BaseModel):
    """User session information."""

    device_id: str | None = Field(None, description="Device identifier")
    created_at: str = Field(..., description="Session creation timestamp")
    last_activity: str | None = Field(None, description="Last activity timestamp")
    ip_address: str | None = Field(None, description="IP address")
    user_agent: str | None = Field(None, description="User agent string")

    class Config:
        schema_extra = {
            "example": {
                "device_id": "device-123",
                "created_at": "2024-01-15T10:00:00Z",
                "last_activity": "2024-01-15T11:30:00Z",
                "ip_address": "192.168.1.1",
                "user_agent": "Mozilla/5.0...",
            }
        }
