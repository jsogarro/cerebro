"""
User session database model.

Tracks active user sessions with device information for security
monitoring and session management.
"""

import secrets
import uuid
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


class UserSession(BaseModel):
    """
    User session model.

    Tracks active sessions including device information,
    location, and activity for security monitoring.
    """

    __tablename__ = "user_sessions"

    # Foreign key to user
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Session identifier
    session_token: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )

    refresh_token: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
        index=True,
    )

    # Session metadata
    session_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="web",
    )

    # Device information
    device_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    device_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    device_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )

    os_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    os_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    browser_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    browser_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Location information
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False)

    country: Mapped[str | None] = mapped_column(String(100), nullable=True)

    region: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    city: Mapped[str | None] = mapped_column(String(100), nullable=True)

    latitude: Mapped[str | None] = mapped_column(String(20), nullable=True)

    longitude: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )

    # Activity tracking
    last_activity: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
    )

    last_ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    request_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    # Session security
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
    )

    is_suspicious: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    mfa_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # Session lifecycle
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    revoke_reason: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    # Additional metadata
    session_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # Relationships
    user = relationship("User", back_populates="sessions")

    # Indexes
    __table_args__ = (
        Index("idx_user_session_active", "user_id", "is_active", "expires_at"),
        Index("idx_user_session_activity", "last_activity", "is_active"),
        Index("idx_user_session_device", "device_id", "user_id"),
    )

    @classmethod
    def create_session(
        cls,
        user_id: str,
        ip_address: str,
        session_type: str = "web",
        duration_hours: int = 24,
        device_info: dict[str, Any] | None = None,
        location_info: dict[str, Any] | None = None,
        mfa_verified: bool = False,
    ) -> "UserSession":
        """
        Create a new user session.

        Args:
            user_id: User ID
            ip_address: Client IP address
            session_type: Type of session (web, api, mobile, cli)
            duration_hours: Session duration in hours
            device_info: Device information dictionary
            location_info: Location information dictionary
            mfa_verified: Whether MFA was verified

        Returns:
            UserSession instance
        """
        session_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(hours=duration_hours)

        session = cls(
            user_id=user_id,
            session_token=session_token,
            refresh_token=refresh_token,
            session_type=session_type,
            ip_address=ip_address,
            last_ip_address=ip_address,
            expires_at=expires_at,
            mfa_verified=mfa_verified,
        )

        # Add device information
        if device_info:
            session.device_id = device_info.get("device_id")
            session.device_name = device_info.get("device_name")
            session.device_type = device_info.get("device_type")
            session.os_name = device_info.get("os_name")
            session.os_version = device_info.get("os_version")
            session.browser_name = device_info.get("browser_name")
            session.browser_version = device_info.get("browser_version")
            session.user_agent = device_info.get("user_agent")

        # Add location information
        if location_info:
            session.country = location_info.get("country")
            session.region = location_info.get("region")
            session.city = location_info.get("city")
            session.latitude = location_info.get("latitude")
            session.longitude = location_info.get("longitude")

        return session

    def update_activity(
        self, ip_address: str | None = None, extend_duration: bool = True
    ) -> None:
        """
        Update session activity.

        Args:
            ip_address: Current IP address
            extend_duration: Whether to extend session duration
        """
        self.last_activity = datetime.now(UTC)
        self.request_count += 1

        if ip_address:
            self.last_ip_address = ip_address

            # Check for IP address change
            if ip_address != self.ip_address:
                self.is_suspicious = True

        # Extend session if requested and not expired
        if extend_duration and not self.is_expired:
            # Extend by original duration
            self.expires_at = datetime.now(UTC) + timedelta(hours=24)

    def revoke(self, reason: str | None = None) -> None:
        """
        Revoke the session.

        Args:
            reason: Reason for revocation
        """
        self.is_active = False
        self.revoked_at = datetime.now(UTC)
        self.revoke_reason = reason

    def refresh(self, duration_hours: int = 24) -> str:
        """
        Refresh the session with a new token.

        Args:
            duration_hours: New session duration in hours

        Returns:
            New session token
        """
        new_token = secrets.token_urlsafe(32)
        self.session_token = new_token
        self.refresh_token = secrets.token_urlsafe(32)
        self.expires_at = datetime.now(UTC) + timedelta(hours=duration_hours)
        self.last_activity = datetime.now(UTC)

        return new_token

    def mark_suspicious(self, reason: str | None = None) -> None:
        """
        Mark session as suspicious.

        Args:
            reason: Reason for marking suspicious
        """
        self.is_suspicious = True
        if reason and self.session_metadata:
            self.session_metadata["suspicious_reason"] = reason
        elif reason:
            self.session_metadata = {"suspicious_reason": reason}

    @property
    def is_expired(self) -> bool:
        """Check if session has expired."""
        return bool(datetime.now(UTC) > self.expires_at)

    @property
    def is_valid(self) -> bool:
        """Check if session is valid (active and not expired)."""
        return bool(self.is_active and not self.is_expired and not self.revoked_at)

    @property
    def duration(self) -> timedelta:
        """Get session duration."""
        return timedelta(seconds=(self.last_activity - self.created_at).total_seconds())

    @property
    def idle_time(self) -> timedelta:
        """Get time since last activity."""
        return timedelta(seconds=(datetime.now(UTC) - self.last_activity).total_seconds())

    @classmethod
    def get_active_sessions(cls, user_id: str, session: Any = None) -> list["UserSession"]:
        """
        Get all active sessions for a user.

        Args:
            user_id: User ID
            session: Database session

        Returns:
            List of active UserSession instances
        """
        if not session:
            return []

        results: list[UserSession] = (
            session.query(cls)
            .filter(
                cls.user_id == user_id,
                cls.is_active,
                cls.expires_at > datetime.now(UTC),
                cls.revoked_at.is_(None),
            )
            .all()
        )
        return results

    @classmethod
    def revoke_all_sessions(
        cls, user_id: str, reason: str = "Manual revocation", session: Any = None
    ) -> int:
        """
        Revoke all sessions for a user.

        Args:
            user_id: User ID
            reason: Revocation reason
            session: Database session

        Returns:
            Number of sessions revoked
        """
        if not session:
            return 0

        active_sessions = cls.get_active_sessions(user_id, session)

        for user_session in active_sessions:
            user_session.revoke(reason)

        session.commit()
        return len(active_sessions)

    def to_dict(self, include_tokens: bool = False) -> dict[str, Any]:
        """
        Convert to dictionary.

        Args:
            include_tokens: Include session tokens

        Returns:
            Dictionary representation
        """
        data = {
            "id": str(self.id),
            "user_id": str(self.user_id),
            "session_type": self.session_type,
            "device_name": self.device_name,
            "device_type": self.device_type,
            "os_name": self.os_name,
            "browser_name": self.browser_name,
            "ip_address": self.ip_address,
            "country": self.country,
            "city": self.city,
            "last_activity": self.last_activity.isoformat(),
            "request_count": self.request_count,
            "is_active": self.is_active,
            "is_suspicious": self.is_suspicious,
            "mfa_verified": self.mfa_verified,
            "expires_at": self.expires_at.isoformat(),
            "created_at": self.created_at.isoformat(),
            "is_valid": self.is_valid,
            "duration_minutes": int(self.duration.total_seconds() / 60),
            "idle_minutes": int(self.idle_time.total_seconds() / 60),
        }

        if include_tokens:
            data["session_token"] = self.session_token
            data["refresh_token"] = self.refresh_token

        return data

    def __repr__(self) -> str:
        """String representation."""
        return f"<UserSession(id={self.id}, user_id={self.user_id}, type={self.session_type}, active={self.is_active})>"
