"""
Audit log database model.

Provides comprehensive security audit trail for all authentication
and authorization events in the system.
"""

from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.orm import relationship

from src.models.db.base import BaseModel


class AuditEventType(str, Enum):
    """Audit event types."""

    # Authentication events
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    PASSWORD_CHANGE = "password_change"
    PASSWORD_RESET_REQUEST = "password_reset_request"
    PASSWORD_RESET_COMPLETE = "password_reset_complete"

    # Account events
    ACCOUNT_CREATED = "account_created"
    ACCOUNT_ACTIVATED = "account_activated"
    ACCOUNT_DEACTIVATED = "account_deactivated"
    ACCOUNT_DELETED = "account_deleted"
    ACCOUNT_LOCKED = "account_locked"
    ACCOUNT_UNLOCKED = "account_unlocked"

    # Email events
    EMAIL_VERIFIED = "email_verified"
    EMAIL_CHANGED = "email_changed"

    # MFA events
    MFA_ENABLED = "mfa_enabled"
    MFA_DISABLED = "mfa_disabled"
    MFA_VERIFIED = "mfa_verified"
    MFA_FAILED = "mfa_failed"
    MFA_BACKUP_USED = "mfa_backup_used"

    # OAuth events
    OAUTH_CONNECTED = "oauth_connected"
    OAUTH_DISCONNECTED = "oauth_disconnected"
    OAUTH_LOGIN = "oauth_login"

    # Session events
    SESSION_CREATED = "session_created"
    SESSION_REFRESHED = "session_refreshed"
    SESSION_REVOKED = "session_revoked"
    SESSION_EXPIRED = "session_expired"

    # API key events
    API_KEY_CREATED = "api_key_created"
    API_KEY_REVOKED = "api_key_revoked"
    API_KEY_USED = "api_key_used"

    # Permission events
    PERMISSION_GRANTED = "permission_granted"
    PERMISSION_REVOKED = "permission_revoked"
    ROLE_ASSIGNED = "role_assigned"
    ROLE_REMOVED = "role_removed"

    # Security events
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    INVALID_TOKEN = "invalid_token"
    UNAUTHORIZED_ACCESS = "unauthorized_access"

    # Data access events
    DATA_ACCESSED = "data_accessed"
    DATA_MODIFIED = "data_modified"
    DATA_DELETED = "data_deleted"
    DATA_EXPORTED = "data_exported"


class AuditSeverity(str, Enum):
    """Audit event severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuditLog(BaseModel):
    """
    Audit log model.

    Comprehensive audit trail for security events, authentication,
    and data access tracking.
    """

    __tablename__ = "audit_logs"

    # Event information
    event_type = Column(
        ENUM(AuditEventType, name="audit_event_type"),
        nullable=False,
        index=True,
        comment="Type of audit event",
    )

    severity = Column(
        ENUM(AuditSeverity, name="audit_severity"),
        nullable=False,
        default=AuditSeverity.INFO,
        index=True,
        comment="Event severity level",
    )

    event_category = Column(
        String(50), nullable=True, index=True, comment="Event category for grouping"
    )

    # User information (nullable for system events)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    username = Column(
        String(100), nullable=True, comment="Username at time of event (denormalized)"
    )

    email = Column(
        String(255), nullable=True, comment="Email at time of event (denormalized)"
    )

    # Actor information (who performed the action if different from user)
    actor_id = Column(
        UUID(as_uuid=True),
        nullable=True,
        comment="ID of user who performed action (for admin actions)",
    )

    actor_username = Column(String(100), nullable=True, comment="Username of actor")

    # Target resource
    resource_type = Column(
        String(50), nullable=True, index=True, comment="Type of resource affected"
    )

    resource_id = Column(
        String(255), nullable=True, index=True, comment="ID of resource affected"
    )

    resource_name = Column(
        String(255), nullable=True, comment="Name/description of resource"
    )

    # Event details
    action = Column(String(100), nullable=False, comment="Action performed")

    description = Column(Text, nullable=True, comment="Detailed event description")

    result = Column(
        String(50),
        nullable=True,
        comment="Result of action (success, failure, partial)",
    )

    error_message = Column(
        Text, nullable=True, comment="Error message if action failed"
    )

    # Request information
    ip_address = Column(
        String(45), nullable=True, index=True, comment="Client IP address"
    )

    user_agent = Column(String(500), nullable=True, comment="User agent string")

    request_id = Column(
        String(100), nullable=True, index=True, comment="Request correlation ID"
    )

    session_id = Column(String(255), nullable=True, comment="Session ID if applicable")

    # Location information
    country = Column(String(100), nullable=True, comment="Country from IP geolocation")

    city = Column(String(100), nullable=True, comment="City from IP geolocation")

    # Additional context
    event_metadata = Column(JSON, nullable=True, comment="Additional event metadata")

    # Security flags
    is_suspicious = Column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
        comment="Flag for suspicious activity",
    )

    requires_review = Column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
        comment="Flag for manual review required",
    )

    reviewed_at = Column(
        DateTime(timezone=True), nullable=True, comment="When event was reviewed"
    )

    reviewed_by = Column(String(255), nullable=True, comment="Who reviewed the event")

    # Relationships
    user = relationship("User", back_populates="audit_logs", foreign_keys=[user_id])

    # Indexes
    __table_args__ = (
        Index("idx_audit_log_user_event", "user_id", "event_type", "created_at"),
        Index("idx_audit_log_resource", "resource_type", "resource_id"),
        Index("idx_audit_log_suspicious", "is_suspicious", "requires_review"),
        Index("idx_audit_log_timestamp", "created_at"),
        Index("idx_audit_log_ip", "ip_address", "created_at"),
    )

    @classmethod
    def log_event(
        cls,
        event_type: AuditEventType,
        action: str,
        user_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        result: str = "success",
        severity: AuditSeverity = AuditSeverity.INFO,
        ip_address: str | None = None,
        metadata: dict | None = None,
        **kwargs,
    ) -> "AuditLog":
        """
        Create an audit log entry.

        Args:
            event_type: Type of event
            action: Action performed
            user_id: User ID if applicable
            resource_type: Type of resource affected
            resource_id: ID of resource affected
            result: Result of action
            severity: Event severity
            ip_address: Client IP address
            metadata: Additional metadata
            **kwargs: Additional fields

        Returns:
            AuditLog instance
        """
        # Determine event category from event type
        event_category = cls._get_event_category(event_type)

        # Check if suspicious
        is_suspicious = cls._is_suspicious_event(event_type, result, metadata)

        return cls(
            event_type=event_type,
            event_category=event_category,
            action=action,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
            severity=severity,
            ip_address=ip_address,
            event_metadata=metadata,
            is_suspicious=is_suspicious,
            requires_review=is_suspicious
            or severity in [AuditSeverity.ERROR, AuditSeverity.CRITICAL],
            **kwargs,
        )

    @staticmethod
    def _get_event_category(event_type: AuditEventType) -> str:
        """Get event category from event type."""
        if "login" in event_type.value.lower() or "logout" in event_type.value.lower():
            return "authentication"
        elif "password" in event_type.value.lower():
            return "password"
        elif "account" in event_type.value.lower():
            return "account"
        elif "mfa" in event_type.value.lower():
            return "mfa"
        elif "oauth" in event_type.value.lower():
            return "oauth"
        elif "session" in event_type.value.lower():
            return "session"
        elif "api_key" in event_type.value.lower():
            return "api_key"
        elif (
            "permission" in event_type.value.lower()
            or "role" in event_type.value.lower()
        ):
            return "authorization"
        elif "data" in event_type.value.lower():
            return "data_access"
        else:
            return "security"

    @staticmethod
    def _is_suspicious_event(
        event_type: AuditEventType, result: str, metadata: dict | None
    ) -> bool:
        """Determine if event is suspicious."""
        # Failed login attempts
        if event_type == AuditEventType.LOGIN_FAILED and result == "failure":
            if metadata and metadata.get("attempt_count", 0) > 3:
                return True

        # Suspicious activity events are always suspicious
        if event_type in [
            AuditEventType.SUSPICIOUS_ACTIVITY,
            AuditEventType.UNAUTHORIZED_ACCESS,
            AuditEventType.RATE_LIMIT_EXCEEDED,
        ]:
            return True

        # Multiple MFA failures
        if event_type == AuditEventType.MFA_FAILED and result == "failure":
            if metadata and metadata.get("attempt_count", 0) > 2:
                return True

        return False

    @classmethod
    def get_user_events(
        cls,
        user_id: str,
        event_types: list[AuditEventType] | None = None,
        limit: int = 100,
        session=None,
    ) -> list["AuditLog"]:
        """
        Get audit events for a user.

        Args:
            user_id: User ID
            event_types: Filter by event types
            limit: Maximum number of events
            session: Database session

        Returns:
            List of AuditLog instances
        """
        if not session:
            return []

        query = session.query(cls).filter(cls.user_id == user_id)

        if event_types:
            query = query.filter(cls.event_type.in_(event_types))

        return query.order_by(cls.created_at.desc()).limit(limit).all()

    @classmethod
    def get_suspicious_events(
        cls, unreviewed_only: bool = True, limit: int = 100, session=None
    ) -> list["AuditLog"]:
        """
        Get suspicious events requiring review.

        Args:
            unreviewed_only: Only get unreviewed events
            limit: Maximum number of events
            session: Database session

        Returns:
            List of AuditLog instances
        """
        if not session:
            return []

        query = session.query(cls).filter(cls.is_suspicious == True)

        if unreviewed_only:
            query = query.filter(cls.reviewed_at.is_(None))

        return query.order_by(cls.created_at.desc()).limit(limit).all()

    def mark_reviewed(self, reviewer: str) -> None:
        """
        Mark event as reviewed.

        Args:
            reviewer: Username of reviewer
        """
        self.reviewed_at = datetime.utcnow()
        self.reviewed_by = reviewer
        self.requires_review = False

    def to_dict(self) -> dict:
        """
        Convert to dictionary.

        Returns:
            Dictionary representation
        """
        return {
            "id": str(self.id),
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "event_category": self.event_category,
            "user_id": str(self.user_id) if self.user_id else None,
            "username": self.username,
            "email": self.email,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "action": self.action,
            "description": self.description,
            "result": self.result,
            "error_message": self.error_message,
            "ip_address": self.ip_address,
            "country": self.country,
            "city": self.city,
            "is_suspicious": self.is_suspicious,
            "requires_review": self.requires_review,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "reviewed_by": self.reviewed_by,
            "created_at": self.created_at.isoformat(),
            "metadata": self.event_metadata,
        }

    def __repr__(self) -> str:
        """String representation."""
        return f"<AuditLog(id={self.id}, event={self.event_type.value}, user={self.user_id}, result={self.result})>"
