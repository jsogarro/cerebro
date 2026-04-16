"""
Authentication-related database models.

Additional tables for authentication, sessions, and security features.
"""

from datetime import datetime

from sqlalchemy import (
    ARRAY,
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.db.base import BaseModel


class PasswordHistory(BaseModel):
    """
    Password history tracking.

    Stores hashed passwords to prevent reuse.
    """

    __tablename__ = "password_history"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    password_hash = Column(
        String(255), nullable=False, comment="Bcrypt hashed password"
    )

    # Relationships
    user = relationship("User", back_populates="password_history")

    # Indexes
    __table_args__ = (
        Index("idx_password_history_user_created", "user_id", "created_at"),
    )


class UserSession(BaseModel):
    """
    User session tracking.

    Stores active user sessions for management and security.
    """

    __tablename__ = "user_sessions"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    token_hash = Column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
        comment="Hashed session token",
    )

    device_info = Column(JSON, nullable=True, comment="Device fingerprint and metadata")

    ip_address = Column(INET, nullable=True, comment="IP address of session")

    user_agent = Column(Text, nullable=True, comment="User agent string")

    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)

    last_activity: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="Last activity timestamp"
    )

    # Relationships
    user = relationship("User", back_populates="sessions")

    # Indexes
    __table_args__ = (
        Index("idx_session_user_expires", "user_id", "expires_at"),
        Index("idx_session_expires", "expires_at"),
    )

    @property
    def is_expired(self) -> bool:
        """Check if session is expired."""
        return bool(datetime.utcnow() > self.expires_at)

    def update_activity(self) -> None:
        """Update last activity timestamp."""
        current_time: datetime = datetime.utcnow()
        self.last_activity = current_time


class AuditLog(BaseModel):
    """
    Audit log for security events.

    Immutable log of all security-relevant actions.
    """

    __tablename__ = "audit_logs"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="User who performed action (null for system)",
    )

    action = Column(
        String(100),
        nullable=False,
        index=True,
        comment="Action performed (login, logout, password_change, etc.)",
    )

    resource_type = Column(
        String(50), nullable=True, index=True, comment="Type of resource affected"
    )

    resource_id = Column(
        UUID(as_uuid=True), nullable=True, index=True, comment="ID of resource affected"
    )

    details = Column(JSON, nullable=True, comment="Additional event details")

    ip_address = Column(INET, nullable=True, comment="IP address of request")

    user_agent = Column(Text, nullable=True, comment="User agent string")

    status = Column(
        String(20), nullable=True, comment="Result status (success, failure, etc.)"
    )

    # Relationships
    user = relationship("User", back_populates="audit_logs")

    # Indexes
    __table_args__ = (
        Index("idx_audit_user_action", "user_id", "action", "created_at"),
        Index("idx_audit_action_created", "action", "created_at"),
        Index("idx_audit_resource", "resource_type", "resource_id"),
    )


class OAuthAccount(BaseModel):
    """
    OAuth provider accounts.

    Links users to external OAuth providers.
    """

    __tablename__ = "oauth_accounts"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    provider = Column(
        String(50), nullable=False, comment="OAuth provider name (google, github, etc.)"
    )

    provider_user_id = Column(
        String(255), nullable=False, comment="User ID from provider"
    )

    access_token = Column(Text, nullable=True, comment="Encrypted access token")

    refresh_token = Column(Text, nullable=True, comment="Encrypted refresh token")

    expires_at = Column(
        DateTime(timezone=True), nullable=True, comment="Token expiration time"
    )

    profile_data = Column(
        JSON, nullable=True, comment="Cached profile data from provider"
    )

    # Relationships
    user = relationship("User", back_populates="oauth_accounts")

    # Constraints and indexes
    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_provider_user"),
        Index("idx_oauth_user_provider", "user_id", "provider"),
    )


class MFASettings(BaseModel):
    """
    Multi-factor authentication settings.

    Stores MFA configuration for users.
    """

    __tablename__ = "mfa_settings"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    totp_secret = Column(String(255), nullable=True, comment="Encrypted TOTP secret")

    backup_codes: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True, comment="Encrypted backup codes"
    )

    sms_number = Column(
        String(20), nullable=True, comment="Phone number for SMS verification"
    )

    is_enabled = Column(
        Boolean, nullable=False, default=False, comment="Whether MFA is enabled"
    )

    preferred_method = Column(
        String(20), nullable=True, comment="Preferred MFA method (totp, sms, etc.)"
    )

    # Relationships
    user = relationship("User", back_populates="mfa_settings", uselist=False)


class LoginAttempt(BaseModel):
    """
    Login attempt tracking.

    Tracks failed login attempts for security monitoring.
    """

    __tablename__ = "login_attempts"

    email = Column(
        String(255), nullable=False, index=True, comment="Email used in attempt"
    )

    ip_address = Column(INET, nullable=True, comment="IP address of attempt")

    user_agent = Column(Text, nullable=True, comment="User agent string")

    success = Column(Boolean, nullable=False, comment="Whether login was successful")

    failure_reason = Column(String(100), nullable=True, comment="Reason for failure")

    # Indexes
    __table_args__ = (
        Index("idx_login_attempt_email_created", "email", "created_at"),
        Index("idx_login_attempt_ip_created", "ip_address", "created_at"),
    )


class SecurityAlert(BaseModel):
    """
    Security alerts and notifications.

    Stores security-related alerts for users.
    """

    __tablename__ = "security_alerts"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    alert_type = Column(
        String(50),
        nullable=False,
        comment="Type of alert (suspicious_login, password_breach, etc.)",
    )

    severity = Column(
        String(20),
        nullable=False,
        comment="Alert severity (low, medium, high, critical)",
    )

    message = Column(Text, nullable=False, comment="Alert message")

    details = Column(JSON, nullable=True, comment="Additional alert details")

    is_read = Column(
        Boolean, nullable=False, default=False, comment="Whether alert has been read"
    )

    resolved_at = Column(
        DateTime(timezone=True), nullable=True, comment="When alert was resolved"
    )

    # Relationships
    user = relationship("User", back_populates="security_alerts")

    # Indexes
    __table_args__ = (
        Index("idx_alert_user_unread", "user_id", "is_read"),
        Index("idx_alert_severity_created", "severity", "created_at"),
    )
