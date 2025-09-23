"""
API Key repository for authentication and authorization.

Manages API keys for service accounts and programmatic access.
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update

from src.models.db.api_key import APIKey, generate_api_key
from src.repositories.base import BaseRepository


class APIKeyRepository(BaseRepository[APIKey]):
    """
    Repository for API key operations.

    Manages API key creation, validation, and usage tracking.
    """

    def __init__(self, session):
        """Initialize API key repository."""
        super().__init__(APIKey, session)

    async def get_by_key_hash(self, key_hash: str) -> APIKey | None:
        """
        Find API key by hash.

        Args:
            key_hash: SHA256 hash of the API key

        Returns:
            API key or None
        """
        query = select(APIKey).where(
            and_(APIKey.key_hash == key_hash, APIKey.deleted_at.is_(None))
        )

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_user(
        self, user_id: UUID, active_only: bool = True, include_expired: bool = False
    ) -> list[APIKey]:
        """
        Get API keys for a user.

        Args:
            user_id: User ID
            active_only: Only return active keys
            include_expired: Include expired keys

        Returns:
            List of API keys
        """
        query = self.build_query().where(APIKey.user_id == user_id)

        if active_only:
            query = query.where(APIKey.is_active == True)

        if not include_expired:
            query = query.where(
                or_(APIKey.expires_at.is_(None), APIKey.expires_at > datetime.utcnow())
            )

        query = query.order_by(APIKey.created_at.desc())

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def create_key(
        self,
        user_id: UUID,
        name: str,
        permissions: list[str],
        expires_in_days: int | None = None,
        description: str | None = None,
        rate_limit: int | None = None,
        allowed_ips: list[str] | None = None,
    ) -> tuple[APIKey, str]:
        """
        Create a new API key.

        Args:
            user_id: User ID
            name: Key name
            permissions: List of permissions
            expires_in_days: Days until expiration
            description: Key description
            rate_limit: Custom rate limit
            allowed_ips: IP restrictions

        Returns:
            Tuple of (APIKey, raw_key)
        """
        # Generate key and hash
        raw_key, key_hash = generate_api_key()

        # Calculate expiration
        expires_at = None
        if expires_in_days:
            expires_at = datetime.utcnow() + timedelta(days=expires_in_days)

        # Create API key
        api_key = await self.create(
            key_hash=key_hash,
            user_id=user_id,
            name=name,
            description=description,
            permissions=permissions,
            rate_limit=rate_limit,
            allowed_ips=allowed_ips,
            expires_at=expires_at,
            is_active=True,
        )

        return api_key, raw_key

    async def validate_key(
        self,
        raw_key: str,
        required_permission: str | None = None,
        ip_address: str | None = None,
    ) -> APIKey | None:
        """
        Validate an API key.

        Args:
            raw_key: Raw API key string
            required_permission: Permission to check
            ip_address: Request IP address

        Returns:
            Valid API key or None
        """
        # Hash the raw key
        key_hash = APIKey.hash_key(raw_key)

        # Find the key
        api_key = await self.get_by_key_hash(key_hash)

        if not api_key:
            return None

        # Check if key is valid
        if not api_key.is_valid:
            return None

        # Check permission if required
        if required_permission and not api_key.has_permission(required_permission):
            return None

        # Check IP restrictions
        if ip_address and not api_key.is_valid_ip(ip_address):
            return None

        # Record usage
        await self.record_usage(api_key.id, ip_address)

        return api_key

    async def record_usage(
        self, key_id: UUID, ip_address: str | None = None
    ) -> None:
        """
        Record API key usage.

        Args:
            key_id: API key ID
            ip_address: Request IP address
        """
        api_key = await self.get(key_id)

        if api_key:
            api_key.record_use(ip_address)
            await self.session.flush()

    async def revoke_key(
        self, key_id: UUID, reason: str | None = None
    ) -> APIKey | None:
        """
        Revoke an API key.

        Args:
            key_id: API key ID
            reason: Revocation reason

        Returns:
            Revoked key or None
        """
        api_key = await self.get(key_id)

        if api_key:
            api_key.revoke(reason)
            await self.session.flush()
            await self.session.refresh(api_key)

        return api_key

    async def cleanup_expired(self) -> int:
        """
        Remove expired API keys.

        Returns:
            Number of keys removed
        """
        # Soft delete expired keys
        stmt = (
            update(APIKey)
            .where(
                and_(
                    APIKey.expires_at.isnot(None),
                    APIKey.expires_at < datetime.utcnow(),
                    APIKey.deleted_at.is_(None),
                )
            )
            .values(deleted_at=datetime.utcnow(), is_active=False)
        )

        result = await self.session.execute(stmt)
        await self.session.flush()

        return result.rowcount

    async def get_usage_statistics(
        self, key_id: UUID, days: int = 30
    ) -> dict[str, Any]:
        """
        Get usage statistics for an API key.

        Args:
            key_id: API key ID
            days: Number of days to analyze

        Returns:
            Usage statistics
        """
        api_key = await self.get(key_id)

        if not api_key:
            return {}

        since = datetime.utcnow() - timedelta(days=days)

        # Calculate usage rate
        usage_rate = 0
        if api_key.last_used_at and api_key.last_used_at >= since:
            days_active = (datetime.utcnow() - since).days or 1
            usage_rate = api_key.use_count / days_active

        return {
            "key_id": str(key_id),
            "key_name": api_key.name,
            "total_uses": api_key.use_count,
            "last_used_at": (
                api_key.last_used_at.isoformat() if api_key.last_used_at else None
            ),
            "last_used_ip": api_key.last_used_ip,
            "created_at": api_key.created_at.isoformat(),
            "expires_at": (
                api_key.expires_at.isoformat() if api_key.expires_at else None
            ),
            "is_expired": api_key.is_expired,
            "is_active": api_key.is_active,
            "days_until_expiration": api_key.days_until_expiration,
            "usage_rate_per_day": usage_rate,
            "permissions": api_key.permissions,
            "rate_limit": api_key.rate_limit,
        }

    async def extend_expiration(self, key_id: UUID, days: int) -> APIKey | None:
        """
        Extend API key expiration.

        Args:
            key_id: API key ID
            days: Days to extend

        Returns:
            Updated key or None
        """
        api_key = await self.get(key_id)

        if api_key:
            api_key.extend_expiration(days)
            await self.session.flush()
            await self.session.refresh(api_key)

        return api_key

    async def update_permissions(
        self, key_id: UUID, permissions: list[str]
    ) -> APIKey | None:
        """
        Update API key permissions.

        Args:
            key_id: API key ID
            permissions: New permissions list

        Returns:
            Updated key or None
        """
        api_key = await self.get(key_id)

        if api_key:
            api_key.permissions = permissions
            api_key.updated_at = datetime.utcnow()
            await self.session.flush()
            await self.session.refresh(api_key)

        return api_key

    async def get_statistics(self) -> dict[str, Any]:
        """
        Get overall API key statistics.

        Returns:
            Statistics dictionary
        """
        # Total keys
        total_query = select(func.count(APIKey.id)).where(APIKey.deleted_at.is_(None))
        result = await self.session.execute(total_query)
        total = result.scalar() or 0

        # Active keys
        active_query = select(func.count(APIKey.id)).where(
            and_(APIKey.deleted_at.is_(None), APIKey.is_active == True)
        )
        result = await self.session.execute(active_query)
        active = result.scalar() or 0

        # Expired keys
        expired_query = select(func.count(APIKey.id)).where(
            and_(
                APIKey.deleted_at.is_(None),
                APIKey.expires_at.isnot(None),
                APIKey.expires_at < datetime.utcnow(),
            )
        )
        result = await self.session.execute(expired_query)
        expired = result.scalar() or 0

        # Recently used (last 7 days)
        recent_cutoff = datetime.utcnow() - timedelta(days=7)
        recent_query = select(func.count(APIKey.id)).where(
            and_(
                APIKey.deleted_at.is_(None),
                APIKey.last_used_at.isnot(None),
                APIKey.last_used_at >= recent_cutoff,
            )
        )
        result = await self.session.execute(recent_query)
        recently_used = result.scalar() or 0

        # Keys by user
        user_query = (
            select(APIKey.user_id, func.count(APIKey.id).label("count"))
            .where(APIKey.deleted_at.is_(None))
            .group_by(APIKey.user_id)
        )

        result = await self.session.execute(user_query)
        keys_per_user = [row.count for row in result]
        avg_keys_per_user = (
            sum(keys_per_user) / len(keys_per_user) if keys_per_user else 0
        )

        return {
            "total_keys": total,
            "active_keys": active,
            "inactive_keys": total - active,
            "expired_keys": expired,
            "recently_used_keys": recently_used,
            "average_keys_per_user": avg_keys_per_user,
            "usage_rate": recently_used / active * 100 if active > 0 else 0,
        }


__all__ = ["APIKeyRepository"]
