"""
Tests for JWT authentication service.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import redis.asyncio as redis
from jose import JWTError, jwt

from src.auth.jwt_service import JWTService
from src.auth.models import TokenPair, TokenPayload


@pytest.fixture
async def redis_mock():
    """Mock Redis client."""
    mock = AsyncMock(spec=redis.Redis)
    mock.setex = AsyncMock()
    mock.get = AsyncMock()
    mock.delete = AsyncMock()
    mock.exists = AsyncMock(return_value=0)
    mock.scan = AsyncMock(return_value=(0, []))
    return mock


@pytest.fixture
async def jwt_service(redis_mock):
    """Create JWT service instance."""
    service = JWTService(redis_client=redis_mock)
    return service


class TestJWTService:
    """Test JWT service functionality."""

    @pytest.mark.asyncio
    async def test_generate_token_pair(self, jwt_service):
        """Test token pair generation."""
        # Generate tokens
        token_pair = await jwt_service.generate_token_pair(
            user_id="test-user-123",
            email="test@example.com",
            roles=["user", "researcher"],
            permissions=["read:projects", "write:projects"],
            device_id="device-123",
        )

        # Verify token pair structure
        assert isinstance(token_pair, TokenPair)
        assert token_pair.access_token
        assert token_pair.refresh_token
        assert token_pair.token_type == "Bearer"
        assert token_pair.expires_in == jwt_service.access_token_expire_minutes * 60

        # Decode and verify access token
        access_payload = jwt.decode(
            token_pair.access_token,
            jwt_service.public_key,
            algorithms=[jwt_service.algorithm],
        )

        assert access_payload["sub"] == "test-user-123"
        assert access_payload["email"] == "test@example.com"
        assert access_payload["roles"] == ["user", "researcher"]
        assert access_payload["permissions"] == ["read:projects", "write:projects"]
        assert access_payload["device_id"] == "device-123"
        assert access_payload["token_type"] == "access"

        # Decode and verify refresh token
        refresh_payload = jwt.decode(
            token_pair.refresh_token,
            jwt_service.public_key,
            algorithms=[jwt_service.algorithm],
        )

        assert refresh_payload["sub"] == "test-user-123"
        assert refresh_payload["device_id"] == "device-123"
        assert refresh_payload["token_type"] == "refresh"

    @pytest.mark.asyncio
    async def test_validate_token_success(self, jwt_service):
        """Test successful token validation."""
        # Generate token
        token_pair = await jwt_service.generate_token_pair(
            user_id="test-user-123",
            email="test@example.com",
            roles=["user"],
            permissions=["read"],
        )

        # Validate token
        payload = await jwt_service.validate_token(
            token_pair.access_token, token_type="access"
        )

        assert isinstance(payload, TokenPayload)
        assert payload.sub == "test-user-123"
        assert payload.email == "test@example.com"
        assert payload.roles == ["user"]
        assert payload.permissions == ["read"]

    @pytest.mark.asyncio
    async def test_validate_token_wrong_type(self, jwt_service):
        """Test token validation with wrong type."""
        # Generate token
        token_pair = await jwt_service.generate_token_pair(
            user_id="test-user-123", email="test@example.com"
        )

        # Try to validate access token as refresh token
        with pytest.raises(JWTError, match="Invalid token type"):
            await jwt_service.validate_token(
                token_pair.access_token, token_type="refresh"
            )

    @pytest.mark.asyncio
    async def test_validate_token_expired(self, jwt_service):
        """Test validation of expired token."""
        # Create expired token
        now = datetime.now(UTC)
        expired_payload = {
            "sub": "test-user",
            "exp": now - timedelta(minutes=1),
            "iat": now - timedelta(minutes=10),
            "jti": "test-jti",
            "token_type": "access",
        }

        expired_token = jwt.encode(
            expired_payload, jwt_service.private_key, algorithm=jwt_service.algorithm
        )

        # Try to validate expired token
        with pytest.raises(JWTError):
            await jwt_service.validate_token(expired_token)

    @pytest.mark.asyncio
    async def test_validate_token_blacklisted(self, jwt_service, redis_mock):
        """Test validation of blacklisted token."""
        # Generate token
        token_pair = await jwt_service.generate_token_pair(
            user_id="test-user-123", email="test@example.com"
        )

        # Mock token as blacklisted
        redis_mock.exists.return_value = 1

        # Try to validate blacklisted token
        with pytest.raises(JWTError, match="Token has been revoked"):
            await jwt_service.validate_token(token_pair.access_token)

    @pytest.mark.asyncio
    async def test_refresh_tokens(self, jwt_service, redis_mock):
        """Test token refresh."""
        # Generate initial tokens
        initial_pair = await jwt_service.generate_token_pair(
            user_id="test-user-123", email="test@example.com", device_id="device-123"
        )

        # Mock refresh token in Redis
        redis_mock.get.return_value = (
            '{"user_id": "test-user-123", "device_id": "device-123"}'
        )

        # Refresh tokens
        new_pair = await jwt_service.refresh_tokens(
            initial_pair.refresh_token, device_id="device-123"
        )

        assert isinstance(new_pair, TokenPair)
        assert new_pair.access_token != initial_pair.access_token
        assert new_pair.refresh_token != initial_pair.refresh_token

        # Verify old refresh token was deleted
        redis_mock.delete.assert_called()

    @pytest.mark.asyncio
    async def test_refresh_tokens_device_mismatch(self, jwt_service):
        """Test token refresh with device mismatch."""
        # Generate tokens
        token_pair = await jwt_service.generate_token_pair(
            user_id="test-user-123", email="test@example.com", device_id="device-123"
        )

        # Try to refresh with different device
        with pytest.raises(JWTError, match="Device mismatch"):
            await jwt_service.refresh_tokens(
                token_pair.refresh_token, device_id="different-device"
            )

    @pytest.mark.asyncio
    async def test_revoke_token(self, jwt_service, redis_mock):
        """Test token revocation."""
        # Generate token
        token_pair = await jwt_service.generate_token_pair(
            user_id="test-user-123", email="test@example.com"
        )

        # Revoke token
        success = await jwt_service.revoke_token(token_pair.access_token)

        assert success is True
        redis_mock.setex.assert_called()

    @pytest.mark.asyncio
    async def test_revoke_all_user_tokens(self, jwt_service, redis_mock):
        """Test revoking all user tokens."""
        # Mock Redis scan to return some keys
        redis_mock.scan.return_value = (0, [b"refresh:token:123", b"refresh:token:456"])
        redis_mock.get.side_effect = [
            '{"user_id": "test-user-123"}',
            '{"user_id": "different-user"}',
        ]

        # Revoke all tokens for user
        count = await jwt_service.revoke_all_user_tokens("test-user-123")

        assert count == 1
        redis_mock.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_active_sessions(self, jwt_service, redis_mock):
        """Test getting active sessions."""
        # Mock Redis scan
        redis_mock.scan.return_value = (0, [b"refresh:token:123", b"refresh:token:456"])
        redis_mock.get.side_effect = [
            '{"user_id": "test-user-123", "device_id": "device-1", "created_at": "2024-01-01T00:00:00"}',
            '{"user_id": "test-user-123", "device_id": "device-2", "created_at": "2024-01-02T00:00:00"}',
        ]

        # Get sessions
        sessions = await jwt_service.get_active_sessions("test-user-123")

        assert len(sessions) == 2
        assert sessions[0]["device_id"] == "device-1"
        assert sessions[1]["device_id"] == "device-2"

    @pytest.mark.asyncio
    async def test_token_expiration(self, jwt_service):
        """Test token expiration times."""
        # Generate tokens
        token_pair = await jwt_service.generate_token_pair(
            user_id="test-user-123", email="test@example.com"
        )

        # Decode tokens to check expiration
        access_payload = jwt.decode(
            token_pair.access_token,
            jwt_service.public_key,
            algorithms=[jwt_service.algorithm],
            options={"verify_exp": False},
        )

        refresh_payload = jwt.decode(
            token_pair.refresh_token,
            jwt_service.public_key,
            algorithms=[jwt_service.algorithm],
            options={"verify_exp": False},
        )

        # Check expiration times
        now = datetime.now(UTC).timestamp()
        access_exp = access_payload["exp"]
        refresh_exp = refresh_payload["exp"]

        # Access token should expire in ~15 minutes
        assert (
            abs(access_exp - now - (jwt_service.access_token_expire_minutes * 60)) < 5
        )

        # Refresh token should expire in ~7 days
        assert (
            abs(refresh_exp - now - (jwt_service.refresh_token_expire_days * 24 * 3600))
            < 5
        )
