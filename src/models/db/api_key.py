"""
API Key database model.

Manages API keys for service accounts and programmatic access.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.db.base import BaseModel


def generate_api_key() -> tuple[str, str]:
    """
    Generate a new API key.

    Returns:
        Tuple of (raw_key, hashed_key)
    """
    # Generate a secure random key
    raw_key = f"gar_{secrets.token_urlsafe(32)}"

    # Hash the key for storage
    hashed_key = hashlib.sha256(raw_key.encode()).hexdigest()

    return raw_key, hashed_key


class APIKey(BaseModel):
    """
    API Key model.

    Stores API keys for programmatic access to the platform.
    Keys are hashed for security and include permission scoping.
    """

    __tablename__ = "api_keys"

    key_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
        comment="SHA256 hash of the API key",
    )

    user_id: Mapped[Any] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="Descriptive name for the key"
    )

    description: Mapped[str | None] = mapped_column(
        String(1000), nullable=True, comment="Detailed description of key purpose"
    )

    permissions: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=list, comment="List of permission strings"
    )

    rate_limit: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Requests per hour limit (null = use user default)",
    )

    allowed_ips: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="List of allowed IP addresses/ranges"
    )

    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    last_used_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="Key expiration time (null = never expires)",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="When the key was revoked"
    )

    revoked_reason: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="Reason for revocation"
    )

    # Relationships
    user = relationship("User", back_populates="api_keys")

    # Indexes
    __table_args__ = (
        Index("idx_apikey_user_active", "user_id", "is_active"),
        Index("idx_apikey_expires", "expires_at", "is_active"),
        Index("idx_apikey_last_used", "last_used_at"),
    )

    @classmethod
    def create_key(
        cls,
        user_id: str,
        name: str,
        permissions: list[str],
        expires_in_days: int | None = None,
        **kwargs: Any,
    ) -> tuple["APIKey", str]:
        """
        Create a new API key.

        Args:
            user_id: User ID
            name: Key name
            permissions: List of permissions
            expires_in_days: Days until expiration (None = never)
            **kwargs: Additional fields

        Returns:
            Tuple of (APIKey instance, raw key)
        """
        raw_key, hashed_key = generate_api_key()

        expires_at = None
        if expires_in_days:
            expires_at = datetime.now(UTC) + timedelta(days=expires_in_days)

        api_key = cls(
            key_hash=hashed_key,
            user_id=user_id,
            name=name,
            permissions=permissions,
            expires_at=expires_at,
            **kwargs,
        )

        return api_key, raw_key

    @classmethod
    def hash_key(cls, raw_key: str) -> str:
        """
        Hash an API key.

        Args:
            raw_key: Raw API key

        Returns:
            Hashed key
        """
        return hashlib.sha256(raw_key.encode()).hexdigest()

    def record_use(self, ip_address: str | None = None) -> None:
        """
        Record API key usage.

        Args:
            ip_address: IP address of the request
        """
        self.last_used_at = datetime.now(UTC)
        self.use_count += 1

        if ip_address:
            self.last_used_ip = ip_address

    def revoke(self, reason: str | None = None) -> None:
        """
        Revoke the API key.

        Args:
            reason: Reason for revocation
        """
        self.is_active = False
        self.revoked_at = datetime.now(UTC)

        if reason:
            self.revoked_reason = reason

    def activate(self) -> None:
        """Reactivate a revoked key."""
        self.is_active = True
        self.revoked_at = None
        self.revoked_reason = None

    def extend_expiration(self, days: int) -> None:
        """
        Extend key expiration.

        Args:
            days: Number of days to extend
        """
        if self.expires_at:
            self.expires_at += timedelta(days=days)
        else:
            self.expires_at = datetime.now(UTC) + timedelta(days=days)

    def has_permission(self, permission: str) -> bool:
        """
        Check if key has a specific permission.

        Args:
            permission: Permission to check

        Returns:
            True if permission is granted
        """
        if not self.permissions:
            return False

        # Check for wildcard permission
        if "*" in self.permissions:
            return True

        # Check specific permission
        return permission in self.permissions

    def is_valid_ip(self, ip_address: str) -> bool:
        """
        Check if IP address is allowed.

        Args:
            ip_address: IP address to check

        Returns:
            True if IP is allowed
        """
        if not self.allowed_ips:
            return True  # No IP restrictions

        # Simple check - in production, use proper IP range checking
        return ip_address in self.allowed_ips

    @property
    def is_expired(self) -> bool:
        """Check if key is expired."""
        if not self.expires_at:
            return False
        return datetime.now(UTC) > self.expires_at

    @property
    def is_valid(self) -> bool:
        """Check if key is valid for use."""
        return self.is_active and not self.is_expired

    @property
    def days_until_expiration(self) -> int | None:
        """Get days until expiration."""
        if not self.expires_at:
            return None

        delta = self.expires_at - datetime.now(UTC)
        return max(0, delta.days)

    def to_dict(self, include_sensitive: bool = False) -> dict[str, Any]:
        """
        Convert to dictionary.

        Args:
            include_sensitive: Include sensitive information

        Returns:
            Dictionary representation
        """
        data = {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "permissions": self.permissions,
            "last_used_at": (
                self.last_used_at.isoformat() if self.last_used_at else None
            ),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_active": self.is_active,
            "is_expired": self.is_expired,
            "is_valid": self.is_valid,
            "days_until_expiration": self.days_until_expiration,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

        if include_sensitive:
            data["user_id"] = str(self.user_id)
            data["use_count"] = self.use_count
            data["last_used_ip"] = self.last_used_ip
            data["rate_limit"] = self.rate_limit
            allowed_ips_value: Any = self.allowed_ips
            data["allowed_ips"] = allowed_ips_value
            data["revoked_at"] = (
                self.revoked_at.isoformat() if self.revoked_at else None
            )
            data["revoked_reason"] = self.revoked_reason

        return data

    def __repr__(self) -> str:
        """String representation."""
        return f"<APIKey(id={self.id}, name='{self.name}', valid={self.is_valid})>"
