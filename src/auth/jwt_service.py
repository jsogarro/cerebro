"""
JWT Token Service for authentication.

Handles token generation, validation, and management using RS256 algorithm.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import redis.asyncio as redis
import structlog
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import JWTError, jwt

from src.auth.models import TokenPair, TokenPayload
from src.core.config import settings
from src.utils.serialization import deserialize_from_cache, serialize_for_cache

logger = structlog.get_logger(__name__)


class JWTServiceUnavailableError(RuntimeError):
    """Raised when token validation cannot reach a required auth store."""


# Keep the original import name available to existing callers while exposing a
# Ruff-compliant canonical exception name.
JWTServiceUnavailable = JWTServiceUnavailableError


class JWTService:
    """
    JWT Token service for secure authentication.

    Features:
    - RS256 algorithm for enhanced security
    - Access and refresh token generation
    - Token blacklisting for logout
    - Token rotation support
    - Device fingerprinting
    """

    def __init__(
        self,
        redis_client: redis.Redis[Any] | None = None,
        private_key_path: str | None = None,
        public_key_path: str | None = None,
    ):
        """
        Initialize JWT service.

        Args:
            redis_client: Redis client for token blacklisting
            private_key_path: Path to RSA private key
            public_key_path: Path to RSA public key
        """
        self.redis_client = redis_client
        self.algorithm = "RS256"
        self._production = settings.ENVIRONMENT.lower() == "production"

        # Token expiration settings
        self.access_token_expire_minutes = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
        self.refresh_token_expire_days = settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS

        # Load or generate RSA keys
        resolved_private_key_path = (
            private_key_path
            if private_key_path is not None
            else settings.JWT_PRIVATE_KEY_PATH
            if self._production
            else None
        )
        resolved_public_key_path = (
            public_key_path
            if public_key_path is not None
            else settings.JWT_PUBLIC_KEY_PATH
            if self._production
            else None
        )
        self.private_key = self._load_or_generate_private_key(resolved_private_key_path)
        self.public_key = self._load_or_generate_public_key(resolved_public_key_path)
        self._validate_key_pair()

        # Token blacklist prefix in Redis
        self.blacklist_prefix = "blacklist:token:"
        self.refresh_token_prefix = "refresh:token:"

    def _load_or_generate_private_key(self, key_path: str | None = None) -> str:
        """Load or generate RSA private key.

        If ``key_path`` is set and exists, load it.
        Otherwise generate a fresh RSA key pair and attempt to persist it
        to ``key_path`` in development and test environments. Production
        requires pre-provisioned key files and fails closed.
        """
        if key_path and os.path.isfile(key_path):
            try:
                with open(key_path, "rb") as f:
                    private_key = serialization.load_pem_private_key(
                        f.read(), password=None, backend=default_backend()
                    )
            except (OSError, TypeError, ValueError) as exc:
                if self._production:
                    raise RuntimeError(
                        "Production JWT private key could not be loaded"
                    ) from exc
                raise
        else:
            if self._production:
                raise RuntimeError(
                    "Production JWT private key file is required and must be readable"
                )

            # Generate new RSA key pair
            private_key = rsa.generate_private_key(
                public_exponent=65537, key_size=2048, backend=default_backend()
            )

            # Best-effort persist if path provided
            if key_path:
                try:
                    os.makedirs(os.path.dirname(key_path), exist_ok=True)
                    with open(key_path, "wb") as f:
                        f.write(
                            private_key.private_bytes(
                                encoding=serialization.Encoding.PEM,
                                format=serialization.PrivateFormat.PKCS8,
                                encryption_algorithm=serialization.NoEncryption(),
                            )
                        )
                except OSError as err:
                    logger.warning(
                        "jwt_private_key_path_not_writable_using_ephemeral",
                        configured_path=key_path,
                        error=str(err),
                        guidance=(
                            "Falling back to ephemeral in-memory key. "
                            "Tokens will not survive process restart. "
                            "Production requires a pre-provisioned read-only key pair."
                        ),
                    )

        return private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()

    def _load_or_generate_public_key(self, key_path: str | None = None) -> str:
        """Load or generate RSA public key."""
        if key_path and os.path.isfile(key_path):
            try:
                with open(key_path, "rb") as f:
                    public_key = serialization.load_pem_public_key(
                        f.read(), backend=default_backend()
                    )
            except (OSError, TypeError, ValueError) as exc:
                if self._production:
                    raise RuntimeError(
                        "Production JWT public key could not be loaded"
                    ) from exc
                raise
        else:
            if self._production:
                raise RuntimeError(
                    "Production JWT public key file is required and must be readable"
                )

            # Extract public key from private key
            private_key_obj = serialization.load_pem_private_key(
                self.private_key.encode(), password=None, backend=default_backend()
            )
            public_key = private_key_obj.public_key()

            # Best-effort persist if path provided. The companion private-key
            # write path already logged a warning if the directory wasn't
            # writable, so we silently skip here to avoid duplicate logging.
            if key_path:
                try:
                    os.makedirs(os.path.dirname(key_path), exist_ok=True)
                    with open(key_path, "wb") as f:
                        f.write(
                            public_key.public_bytes(
                                encoding=serialization.Encoding.PEM,
                                format=serialization.PublicFormat.SubjectPublicKeyInfo,
                            )
                        )
                except OSError:
                    pass

        if isinstance(public_key, bytes):
            return public_key.decode()

        return public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    def _validate_key_pair(self) -> None:
        """Reject a public key that does not match the signing key."""

        try:
            private_key = serialization.load_pem_private_key(
                self.private_key.encode(),
                password=None,
                backend=default_backend(),
            )
            public_key = serialization.load_pem_public_key(
                self.public_key.encode(),
                backend=default_backend(),
            )
            expected_public = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            actual_public = public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            if actual_public != expected_public:
                raise ValueError("JWT public key does not match private key")
        except (TypeError, ValueError) as exc:
            if self._production:
                raise RuntimeError(
                    "Production JWT key pair is invalid or mismatched"
                ) from exc
            raise

    async def generate_token_pair(
        self,
        user_id: str,
        email: str,
        roles: list[str] | None = None,
        permissions: list[str] | None = None,
        device_id: str | None = None,
        organization_id: str | None = None,
        additional_claims: dict[str, Any] | None = None,
    ) -> TokenPair:
        """
        Generate access and refresh token pair.

        Args:
            user_id: User identifier
            email: User email
            roles: User roles
            permissions: User permissions
            device_id: Device fingerprint
            organization_id: Tenant organization identifier
            additional_claims: Additional JWT claims

        Returns:
            TokenPair with access and refresh tokens
        """
        # Generate unique token ID
        jti = str(uuid4())

        # Current time
        now = datetime.now(UTC)

        # Base payload
        base_payload = {
            "sub": user_id,
            "email": email,
            "roles": roles or [],
            "permissions": permissions or [],
            "organization_id": organization_id,
            "jti": jti,
            "iat": now,
            "device_id": device_id,
        }

        # Add additional claims
        if additional_claims:
            base_payload.update(additional_claims)

        # Access token payload
        access_payload = {
            **base_payload,
            "exp": now + timedelta(minutes=self.access_token_expire_minutes),
            "token_type": "access",
        }

        # Refresh token payload
        refresh_payload = {
            "sub": user_id,
            "jti": f"refresh_{jti}",
            "iat": now,
            "exp": now + timedelta(days=self.refresh_token_expire_days),
            "token_type": "refresh",
            "device_id": device_id,
            "organization_id": organization_id,
        }

        # Generate tokens
        access_token = jwt.encode(
            access_payload, self.private_key, algorithm=self.algorithm
        )

        refresh_token = jwt.encode(
            refresh_payload, self.private_key, algorithm=self.algorithm
        )

        # Store refresh token in Redis for validation
        if self.redis_client:
            refresh_key = f"{self.refresh_token_prefix}{refresh_payload['jti']}"
            refresh_data = {
                "user_id": user_id,
                "device_id": device_id,
                "organization_id": organization_id,
                "created_at": now.isoformat(),
            }
            await self.redis_client.setex(
                refresh_key,
                timedelta(days=self.refresh_token_expire_days),
                serialize_for_cache(refresh_data).decode("utf-8"),
            )

        logger.info(
            "Generated token pair", user_id=user_id, jti=jti, device_id=device_id
        )

        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="Bearer",
            expires_in=self.access_token_expire_minutes * 60,
        )

    async def validate_token(
        self,
        token: str,
        token_type: str = "access",
        verify_blacklist: bool = True,
    ) -> TokenPayload:
        """
        Validate and decode JWT token.

        Args:
            token: JWT token to validate
            token_type: Expected token type (access/refresh)
            verify_blacklist: Check if token is blacklisted

        Returns:
            Decoded token payload

        Raises:
            JWTError: If token is invalid
        """
        try:
            # Decode token
            payload = jwt.decode(
                token,
                self.public_key,
                algorithms=[self.algorithm],
                options={"verify_exp": True},
            )

            # Verify token type
            if payload.get("token_type") != token_type:
                raise JWTError(f"Invalid token type. Expected {token_type}")

            jti = payload.get("jti")
            if not jti:
                raise JWTError("Token missing jti")

            # Check blacklist. A token cannot be admitted while the revocation
            # store is unavailable; treating that failure as an invalid token
            # would hide an infrastructure outage and invite fail-open workarounds.
            if verify_blacklist:
                if self.redis_client is None:
                    raise JWTServiceUnavailableError(
                        "Token blacklist store unavailable"
                    )
                try:
                    is_blacklisted = await self._is_token_blacklisted(jti)
                except Exception as exc:
                    logger.error("Token blacklist store unavailable", error=str(exc))
                    raise JWTServiceUnavailableError(
                        "Token blacklist store unavailable"
                    ) from exc
                if is_blacklisted:
                    raise JWTError("Token has been revoked")

            # Create token payload
            return TokenPayload(
                sub=payload["sub"],
                email=payload.get("email"),
                roles=payload.get("roles", []),
                permissions=payload.get("permissions", []),
                organization_id=payload.get("organization_id"),
                jti=payload["jti"],
                iat=datetime.fromtimestamp(payload["iat"], tz=UTC),
                exp=datetime.fromtimestamp(payload["exp"], tz=UTC),
                device_id=payload.get("device_id"),
            )

        except JWTServiceUnavailableError:
            raise
        except JWTError as e:
            logger.warning("Token validation failed", error=str(e))
            raise
        except Exception as e:
            logger.error("Unexpected error during token validation", error=str(e))
            raise JWTError(f"Token validation failed: {e!s}") from e

    async def refresh_tokens(
        self,
        refresh_token: str,
        device_id: str | None = None,
    ) -> TokenPair:
        """
        Refresh token pair using refresh token.

        Args:
            refresh_token: Valid refresh token
            device_id: Device fingerprint for validation

        Returns:
            New token pair

        Raises:
            JWTError: If refresh token is invalid
        """
        # Validate refresh token
        refresh_payload = await self.validate_token(refresh_token, token_type="refresh")

        # Verify device ID if provided
        if device_id and refresh_payload.device_id != device_id:
            raise JWTError("Device mismatch")

        # Check if refresh token exists in Redis
        if self.redis_client:
            refresh_key = f"{self.refresh_token_prefix}{refresh_payload.jti}"
            refresh_data = await self.redis_client.get(refresh_key)

            if not refresh_data:
                raise JWTError("Refresh token not found or expired")

            # Revoke old refresh token
            await self.redis_client.delete(refresh_key)

        # Get user details (would typically fetch from database)
        # For now, we'll use the sub from refresh token
        user_id = refresh_payload.sub

        # Generate new token pair
        # In production, fetch fresh user data from database
        return await self.generate_token_pair(
            user_id=user_id,
            email=refresh_payload.email or "",  # Would fetch from DB
            roles=refresh_payload.roles,
            permissions=refresh_payload.permissions,
            device_id=device_id or refresh_payload.device_id,
            organization_id=refresh_payload.organization_id,
        )

    async def revoke_token(self, token: str) -> bool:
        """
        Revoke a token by adding to blacklist.

        Args:
            token: Token to revoke

        Returns:
            True if successfully revoked
        """
        try:
            # Decode token to get JTI
            payload = jwt.decode(
                token,
                self.public_key,
                algorithms=[self.algorithm],
                options={"verify_exp": False},  # Allow expired tokens
            )

            jti = payload.get("jti")
            if not jti:
                return False

            if self.redis_client is None:
                return False

            # Add to blacklist
            if self.redis_client:
                # Calculate remaining TTL
                exp = payload.get("exp")
                if exp:
                    ttl = max(0, exp - datetime.now(UTC).timestamp())
                    blacklist_key = f"{self.blacklist_prefix}{jti}"
                    await self.redis_client.setex(blacklist_key, int(ttl), "1")

                # If refresh token, also delete from refresh store
                if payload.get("token_type") == "refresh":
                    refresh_key = f"{self.refresh_token_prefix}{jti}"
                    await self.redis_client.delete(refresh_key)

            logger.info("Token revoked", jti=jti)
            return True

        except Exception as e:
            logger.error("Failed to revoke token", error=str(e))
            return False

    async def revoke_all_user_tokens(self, user_id: str) -> int:
        """
        Revoke all tokens for a user.

        Args:
            user_id: User identifier

        Returns:
            Number of tokens revoked
        """
        if not self.redis_client:
            return 0

        count = 0

        # Find all refresh tokens for user
        pattern = f"{self.refresh_token_prefix}*"
        cursor = 0

        while True:
            cursor, keys = await self.redis_client.scan(
                cursor, match=pattern, count=100
            )

            for key in keys:
                data = await self.redis_client.get(key)
                if data:
                    try:
                        token_data = deserialize_from_cache(data)
                        if token_data.get("user_id") == user_id:
                            await self.redis_client.delete(key)
                            count += 1
                    except json.JSONDecodeError:
                        continue

            if cursor == 0:
                break

        logger.info("Revoked all user tokens", user_id=user_id, count=count)
        return count

    async def _is_token_blacklisted(self, jti: str) -> bool:
        """
        Check if token is blacklisted.

        Args:
            jti: Token ID

        Returns:
            True if blacklisted
        """
        if not self.redis_client:
            return False

        blacklist_key = f"{self.blacklist_prefix}{jti}"
        return int(await self.redis_client.exists(blacklist_key)) > 0

    async def get_active_sessions(self, user_id: str) -> list[dict[str, Any]]:
        """
        Get all active sessions for a user.

        Args:
            user_id: User identifier

        Returns:
            List of active sessions
        """
        if not self.redis_client:
            return []

        sessions = []
        pattern = f"{self.refresh_token_prefix}*"
        cursor = 0

        while True:
            cursor, keys = await self.redis_client.scan(
                cursor, match=pattern, count=100
            )

            for key in keys:
                data = await self.redis_client.get(key)
                if data:
                    try:
                        token_data = deserialize_from_cache(data)
                        if token_data.get("user_id") == user_id:
                            sessions.append(
                                {
                                    "device_id": token_data.get("device_id"),
                                    "created_at": token_data.get("created_at"),
                                    "key": (
                                        key.decode() if isinstance(key, bytes) else key
                                    ),
                                }
                            )
                    except json.JSONDecodeError:
                        continue

            if cursor == 0:
                break

        return sessions

    async def cleanup_expired_tokens(self) -> int:
        """
        Clean up expired tokens from blacklist.

        Returns:
            Number of tokens cleaned
        """
        # Redis automatically expires keys with TTL
        # This method is for manual cleanup if needed
        return 0
