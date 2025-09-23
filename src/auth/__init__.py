"""
Authentication and security module for the Research Platform.

This module provides comprehensive authentication, authorization, and security
features including JWT tokens, OAuth2, rate limiting, and audit logging.
"""

from src.auth.jwt_service import JWTService
from src.auth.models import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    TokenPayload,
)
from src.auth.password_service import PasswordService

__all__ = [
    "JWTService",
    "LoginRequest",
    "PasswordService",
    "RefreshRequest",
    "RegisterRequest",
    "TokenPair",
    "TokenPayload",
]
