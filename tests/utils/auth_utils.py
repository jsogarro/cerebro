"""
Authentication utilities for integration testing.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

import jwt

from src.auth.jwt_service import JWTService
from src.auth.password_service import PasswordService
from src.models.db.user import User


class TestAuthManager:
    """Manage authentication for testing."""

    def __init__(self, secret_key: str = "test-secret-key", algorithm: str = "HS256"):
        self.secret_key = secret_key
        self.algorithm = algorithm
        self.jwt_service = JWTService(
            secret_key=secret_key,
            algorithm=algorithm,
            access_token_expire_minutes=30,
            refresh_token_expire_days=7,
        )
        self.password_service = PasswordService()

    def create_access_token(
        self,
        user_id: str,
        email: str,
        role: str = "researcher",
        scopes: list[str] | None = None,
        expires_delta: timedelta | None = None,
    ) -> str:
        """Create an access token for testing."""
        data = {
            "sub": user_id,
            "email": email,
            "role": role,
            "scopes": scopes or ["read", "write"],
            "type": "access",
        }

        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=30)

        data["exp"] = expire
        data["iat"] = datetime.utcnow()
        data["jti"] = str(uuid.uuid4())  # JWT ID for revocation

        return jwt.encode(data, self.secret_key, algorithm=self.algorithm)

    def create_refresh_token(
        self, user_id: str, expires_delta: timedelta | None = None
    ) -> str:
        """Create a refresh token for testing."""
        data = {"sub": user_id, "type": "refresh"}

        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(days=7)

        data["exp"] = expire
        data["iat"] = datetime.utcnow()
        data["jti"] = str(uuid.uuid4())

        return jwt.encode(data, self.secret_key, algorithm=self.algorithm)

    def create_expired_token(
        self, user_id: str, email: str, role: str = "researcher"
    ) -> str:
        """Create an expired token for testing."""
        return self.create_access_token(
            user_id=user_id, email=email, role=role, expires_delta=timedelta(seconds=-1)
        )

    def create_invalid_signature_token(
        self, user_id: str, email: str, role: str = "researcher"
    ) -> str:
        """Create a token with invalid signature for testing."""
        data = {
            "sub": user_id,
            "email": email,
            "role": role,
            "type": "access",
            "exp": datetime.utcnow() + timedelta(minutes=30),
            "iat": datetime.utcnow(),
            "jti": str(uuid.uuid4()),
        }

        # Use wrong secret key
        return jwt.encode(data, "wrong-secret-key", algorithm=self.algorithm)

    def create_api_key(
        self,
        user_id: str,
        scopes: list[str] | None = None,
        expires_delta: timedelta | None = None,
    ) -> str:
        """Create an API key for testing."""
        import hashlib
        import secrets

        # Generate random API key
        api_key = f"sk_test_{secrets.token_urlsafe(32)}"

        # Store hash for validation
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()

        return api_key

    def decode_token(self, token: str) -> dict[str, Any]:
        """Decode a token for testing verification."""
        try:
            return jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
        except jwt.ExpiredSignatureError:
            return {"error": "Token expired"}
        except jwt.InvalidTokenError as e:
            return {"error": str(e)}

    def hash_password(self, password: str) -> str:
        """Hash a password for testing."""
        return self.password_service.hash_password(password)

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password for testing."""
        return self.password_service.verify_password(plain_password, hashed_password)


class TestAuthScenarios:
    """Generate various authentication scenarios for testing."""

    def __init__(self, auth_manager: TestAuthManager):
        self.auth_manager = auth_manager

    def create_user_tokens(self, user: User) -> dict[str, str]:
        """Create various tokens for a user."""
        return {
            "valid_access": self.auth_manager.create_access_token(
                user.id, user.email, user.role
            ),
            "valid_refresh": self.auth_manager.create_refresh_token(user.id),
            "expired_access": self.auth_manager.create_expired_token(
                user.id, user.email, user.role
            ),
            "invalid_signature": self.auth_manager.create_invalid_signature_token(
                user.id, user.email, user.role
            ),
            "admin_access": self.auth_manager.create_access_token(
                user.id, user.email, "admin"
            ),
            "readonly_access": self.auth_manager.create_access_token(
                user.id, user.email, user.role, scopes=["read"]
            ),
        }

    def create_authorization_headers(
        self, tokens: dict[str, str]
    ) -> dict[str, dict[str, str]]:
        """Create authorization headers for various scenarios."""
        return {
            "valid": {"Authorization": f"Bearer {tokens['valid_access']}"},
            "expired": {"Authorization": f"Bearer {tokens['expired_access']}"},
            "invalid": {"Authorization": f"Bearer {tokens['invalid_signature']}"},
            "malformed": {"Authorization": "Bearer invalid.token.format"},
            "missing": {},
            "wrong_scheme": {"Authorization": f"Basic {tokens['valid_access']}"},
            "admin": {"Authorization": f"Bearer {tokens['admin_access']}"},
            "readonly": {"Authorization": f"Bearer {tokens['readonly_access']}"},
        }

    def create_login_scenarios(self) -> list[dict[str, Any]]:
        """Create various login test scenarios."""
        return [
            {
                "name": "valid_credentials",
                "email": "test@example.com",
                "password": "ValidPass123!",
                "expected_status": 200,
            },
            {
                "name": "invalid_password",
                "email": "test@example.com",
                "password": "WrongPassword",
                "expected_status": 401,
            },
            {
                "name": "non_existent_user",
                "email": "nonexistent@example.com",
                "password": "Password123!",
                "expected_status": 401,
            },
            {
                "name": "invalid_email_format",
                "email": "invalid-email",
                "password": "Password123!",
                "expected_status": 422,
            },
            {
                "name": "empty_password",
                "email": "test@example.com",
                "password": "",
                "expected_status": 422,
            },
            {
                "name": "sql_injection_attempt",
                "email": "test@example.com' OR '1'='1",
                "password": "Password123!",
                "expected_status": 422,
            },
        ]

    def create_registration_scenarios(self) -> list[dict[str, Any]]:
        """Create various registration test scenarios."""
        return [
            {
                "name": "valid_registration",
                "data": {
                    "email": "newuser@example.com",
                    "username": "newuser",
                    "password": "SecurePass123!",
                    "full_name": "New User",
                },
                "expected_status": 201,
            },
            {
                "name": "duplicate_email",
                "data": {
                    "email": "existing@example.com",
                    "username": "newuser2",
                    "password": "SecurePass123!",
                    "full_name": "New User 2",
                },
                "expected_status": 400,
            },
            {
                "name": "weak_password",
                "data": {
                    "email": "weak@example.com",
                    "username": "weakuser",
                    "password": "weak",
                    "full_name": "Weak User",
                },
                "expected_status": 422,
            },
            {
                "name": "missing_required_fields",
                "data": {
                    "email": "incomplete@example.com",
                },
                "expected_status": 422,
            },
            {
                "name": "invalid_email",
                "data": {
                    "email": "not-an-email",
                    "username": "invaliduser",
                    "password": "SecurePass123!",
                    "full_name": "Invalid User",
                },
                "expected_status": 422,
            },
        ]


class MockOAuthProvider:
    """Mock OAuth provider for testing."""

    def __init__(self, provider_name: str = "google"):
        self.provider_name = provider_name
        self.users = {}

    def register_user(
        self, oauth_id: str, email: str, name: str, picture: str | None = None
    ):
        """Register a user with the mock OAuth provider."""
        self.users[oauth_id] = {
            "id": oauth_id,
            "email": email,
            "name": name,
            "picture": picture or f"https://example.com/avatar/{oauth_id}.jpg",
            "email_verified": True,
        }

    def get_user_info(self, oauth_id: str) -> dict[str, Any] | None:
        """Get user info from mock OAuth provider."""
        return self.users.get(oauth_id)

    def generate_oauth_token(self, oauth_id: str) -> str:
        """Generate a mock OAuth token."""
        import secrets

        return f"mock_oauth_token_{self.provider_name}_{oauth_id}_{secrets.token_urlsafe(16)}"

    def validate_token(self, token: str) -> str | None:
        """Validate a mock OAuth token and return user ID."""
        if token.startswith(f"mock_oauth_token_{self.provider_name}_"):
            parts = token.split("_")
            if len(parts) >= 5:
                return parts[4]  # Return OAuth ID
        return None


class TestPermissionChecker:
    """Check permissions for testing."""

    @staticmethod
    def has_permission(
        user_role: str, required_role: str, operation: str = "read"
    ) -> bool:
        """Check if a user role has permission for an operation."""
        role_hierarchy = {
            "admin": 3,
            "researcher": 2,
            "viewer": 1,
        }

        operation_requirements = {
            "read": ["viewer", "researcher", "admin"],
            "write": ["researcher", "admin"],
            "delete": ["admin"],
            "admin": ["admin"],
        }

        allowed_roles = operation_requirements.get(operation, [])
        return user_role in allowed_roles

    @staticmethod
    def check_resource_ownership(
        user_id: str, resource_owner_id: str, user_role: str = "researcher"
    ) -> bool:
        """Check if a user owns a resource or is admin."""
        return user_id == resource_owner_id or user_role == "admin"
