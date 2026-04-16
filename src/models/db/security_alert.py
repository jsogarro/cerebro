"""
Security alert database model.

Manages security alerts and notifications for suspicious activities,
policy violations, and security incidents.
"""

from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID as PyUUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.db.base import BaseModel


class AlertType(str, Enum):
    """Security alert types."""

    # Authentication alerts
    SUSPICIOUS_LOGIN = "suspicious_login"
    FAILED_LOGIN_ATTEMPTS = "failed_login_attempts"
    NEW_DEVICE_LOGIN = "new_device_login"
    NEW_LOCATION_LOGIN = "new_location_login"
    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET = "password_reset"

    # Account alerts
    ACCOUNT_LOCKED = "account_locked"
    ACCOUNT_UNLOCKED = "account_unlocked"
    ACCOUNT_DEACTIVATED = "account_deactivated"
    PRIVILEGE_ESCALATION = "privilege_escalation"

    # MFA alerts
    MFA_DISABLED = "mfa_disabled"
    MFA_METHOD_CHANGED = "mfa_method_changed"
    MFA_BYPASS_ATTEMPT = "mfa_bypass_attempt"

    # Session alerts
    CONCURRENT_SESSIONS = "concurrent_sessions"
    SESSION_HIJACKING = "session_hijacking"
    UNUSUAL_ACTIVITY = "unusual_activity"

    # API alerts
    API_RATE_LIMIT = "api_rate_limit"
    API_KEY_COMPROMISED = "api_key_compromised"
    UNAUTHORIZED_API_ACCESS = "unauthorized_api_access"

    # Data alerts
    BULK_DATA_ACCESS = "bulk_data_access"
    SENSITIVE_DATA_ACCESS = "sensitive_data_access"
    DATA_EXFILTRATION = "data_exfiltration"

    # System alerts
    BRUTE_FORCE_ATTACK = "brute_force_attack"
    SQL_INJECTION_ATTEMPT = "sql_injection_attempt"
    XSS_ATTEMPT = "xss_attempt"
    SYSTEM_BREACH = "system_breach"


class AlertSeverity(str, Enum):
    """Alert severity levels."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    """Alert status."""

    NEW = "new"
    ACKNOWLEDGED = "acknowledged"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    FALSE_POSITIVE = "false_positive"
    ESCALATED = "escalated"


class SecurityAlert(BaseModel):
    """
    Security alert model.

    Tracks security incidents, suspicious activities, and
    policy violations requiring attention.
    """

    __tablename__ = "security_alerts"

    # Alert identification
    alert_type: Mapped[AlertType] = mapped_column(
        ENUM(AlertType, name="alert_type"),
        nullable=False,
        index=True,
        comment="Type of security alert",
    )

    severity: Mapped[AlertSeverity] = mapped_column(
        ENUM(AlertSeverity, name="alert_severity"),
        nullable=False,
        index=True,
        comment="Alert severity level",
    )

    status: Mapped[AlertStatus] = mapped_column(
        ENUM(AlertStatus, name="alert_status"),
        nullable=False,
        default=AlertStatus.NEW,
        index=True,
        comment="Current alert status",
    )

    # Affected user (nullable for system-wide alerts)
    user_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    username: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="Username at time of alert (denormalized)"
    )

    email: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Email at time of alert (denormalized)"
    )

    # Alert details
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="Alert title")

    description: Mapped[str] = mapped_column(Text, nullable=False, comment="Detailed alert description")

    # Threat indicators
    ip_address: Mapped[str | None] = mapped_column(
        String(45), nullable=True, index=True, comment="Source IP address"
    )

    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="User agent string")

    request_path: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="Request path/endpoint")

    request_method: Mapped[str | None] = mapped_column(String(10), nullable=True, comment="HTTP request method")

    # Location information
    country: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="Country from IP geolocation")

    city: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="City from IP geolocation")

    is_known_location: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether location is known for user",
    )

    # Risk assessment
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="Calculated risk score (0-100)")

    confidence_score: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="Alert confidence score (0-100)"
    )

    is_automated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Whether alert was automatically generated",
    )

    # Related information
    related_alerts: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True, comment="IDs of related alerts")

    affected_resources: Mapped[list[Any] | None] = mapped_column(
        JSON, nullable=True, comment="List of affected resources"
    )

    evidence: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True, comment="Supporting evidence/logs")

    # Actions taken
    actions_taken: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True, comment="List of actions taken")

    auto_remediated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether automatically remediated",
    )

    remediation_steps: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Recommended remediation steps"
    )

    # Response tracking
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="When alert was acknowledged"
    )

    acknowledged_by: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Who acknowledged the alert"
    )

    investigated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="When investigation started"
    )

    investigated_by: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Who investigated the alert"
    )

    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="When alert was resolved"
    )

    resolved_by: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="Who resolved the alert")

    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True, comment="Resolution notes")

    # Notification tracking
    notifications_sent: Mapped[list[Any] | None] = mapped_column(
        JSON, nullable=True, default=[], comment="List of sent notifications"
    )

    email_sent: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether email notification was sent",
    )

    sms_sent: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether SMS notification was sent",
    )

    # Escalation
    escalated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
        comment="Whether alert was escalated",
    )

    escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="When alert was escalated"
    )

    escalated_to: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Who/where alert was escalated to"
    )

    # Alert metadata
    alert_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, comment="Additional alert metadata")

    # Relationships
    user = relationship("User", back_populates="security_alerts")

    # Indexes
    __table_args__ = (
        Index("idx_security_alert_user_type", "user_id", "alert_type", "created_at"),
        Index("idx_security_alert_status_severity", "status", "severity"),
        Index("idx_security_alert_ip", "ip_address", "created_at"),
        Index("idx_security_alert_escalated", "escalated", "status"),
    )

    @classmethod
    def create_alert(
        cls,
        alert_type: AlertType,
        title: str,
        description: str,
        severity: AlertSeverity = AlertSeverity.MEDIUM,
        user_id: str | None = None,
        ip_address: str | None = None,
        evidence: dict[str, Any] | None = None,
        auto_remediate: bool = False,
        **kwargs: Any,
    ) -> "SecurityAlert":
        """
        Create a security alert.

        Args:
            alert_type: Type of alert
            title: Alert title
            description: Alert description
            severity: Alert severity
            user_id: Affected user ID
            ip_address: Source IP address
            evidence: Supporting evidence
            auto_remediate: Whether to auto-remediate
            **kwargs: Additional fields

        Returns:
            SecurityAlert instance
        """
        # Calculate risk score based on type and severity
        risk_score = cls._calculate_risk_score(alert_type, severity)

        # Determine if escalation is needed
        needs_escalation = severity in [
            AlertSeverity.HIGH,
            AlertSeverity.CRITICAL,
        ] or alert_type in [
            AlertType.SYSTEM_BREACH,
            AlertType.DATA_EXFILTRATION,
            AlertType.SESSION_HIJACKING,
        ]

        alert = cls(
            alert_type=alert_type,
            title=title,
            description=description,
            severity=severity,
            user_id=user_id,
            ip_address=ip_address,
            evidence=evidence,
            risk_score=risk_score,
            escalated=needs_escalation,
            **kwargs,
        )

        # Auto-remediate if requested
        if auto_remediate:
            alert._auto_remediate()

        return alert

    @staticmethod
    def _calculate_risk_score(alert_type: AlertType, severity: AlertSeverity) -> int:
        """Calculate risk score based on alert type and severity."""
        # Base scores by severity
        severity_scores = {
            AlertSeverity.LOW: 25,
            AlertSeverity.MEDIUM: 50,
            AlertSeverity.HIGH: 75,
            AlertSeverity.CRITICAL: 100,
        }

        # Type multipliers
        type_multipliers = {
            AlertType.SYSTEM_BREACH: 1.5,
            AlertType.DATA_EXFILTRATION: 1.4,
            AlertType.SESSION_HIJACKING: 1.3,
            AlertType.SQL_INJECTION_ATTEMPT: 1.2,
            AlertType.BRUTE_FORCE_ATTACK: 1.2,
            AlertType.API_KEY_COMPROMISED: 1.1,
        }

        base_score = severity_scores.get(severity, 50)
        multiplier = type_multipliers.get(alert_type, 1.0)

        return min(int(base_score * multiplier), 100)

    def _auto_remediate(self) -> None:
        """Perform automatic remediation based on alert type."""
        actions = []

        if self.alert_type == AlertType.FAILED_LOGIN_ATTEMPTS:
            actions.append("Account temporarily locked")
            self.auto_remediated = True
        elif self.alert_type == AlertType.SUSPICIOUS_LOGIN:
            actions.append("Session terminated")
            actions.append("Password reset email sent")
            self.auto_remediated = True
        elif self.alert_type == AlertType.API_RATE_LIMIT:
            actions.append("API access temporarily blocked")
            self.auto_remediated = True

        if actions:
            self.actions_taken = actions

    def acknowledge(self, acknowledged_by: str) -> None:
        """
        Acknowledge the alert.

        Args:
            acknowledged_by: Who is acknowledging
        """
        self.status = AlertStatus.ACKNOWLEDGED
        self.acknowledged_at = datetime.utcnow()
        self.acknowledged_by = acknowledged_by

    def investigate(self, investigated_by: str) -> None:
        """
        Mark alert as under investigation.

        Args:
            investigated_by: Who is investigating
        """
        self.status = AlertStatus.INVESTIGATING
        self.investigated_at = datetime.utcnow()
        self.investigated_by = investigated_by

        # Auto-acknowledge if not already
        if not self.acknowledged_at:
            self.acknowledged_at = datetime.utcnow()
            self.acknowledged_by = investigated_by

    def resolve(
        self, resolved_by: str, resolution_notes: str, is_false_positive: bool = False
    ) -> None:
        """
        Resolve the alert.

        Args:
            resolved_by: Who is resolving
            resolution_notes: Resolution notes
            is_false_positive: Whether this was a false positive
        """
        self.status = (
            AlertStatus.FALSE_POSITIVE if is_false_positive else AlertStatus.RESOLVED
        )
        self.resolved_at = datetime.utcnow()
        self.resolved_by = resolved_by
        self.resolution_notes = resolution_notes

    def escalate(self, escalated_to: str) -> None:
        """
        Escalate the alert.

        Args:
            escalated_to: Who/where to escalate to
        """
        self.status = AlertStatus.ESCALATED
        self.escalated = True
        self.escalated_at = datetime.utcnow()
        self.escalated_to = escalated_to

    def add_evidence(self, evidence: dict[str, Any]) -> None:
        """
        Add evidence to the alert.

        Args:
            evidence: Evidence to add
        """
        if self.evidence:
            if isinstance(self.evidence, list):
                self.evidence.append(evidence)
            else:
                self.evidence = [self.evidence, evidence]
        else:
            self.evidence = [evidence]

    def send_notification(self, channel: str, recipient: str) -> None:
        """
        Record notification sent.

        Args:
            channel: Notification channel (email, sms, etc.)
            recipient: Notification recipient
        """
        notification = {
            "channel": channel,
            "recipient": recipient,
            "sent_at": datetime.utcnow().isoformat(),
        }

        if self.notifications_sent:
            self.notifications_sent.append(notification)
        else:
            self.notifications_sent = [notification]

        if channel == "email":
            self.email_sent = True
        elif channel == "sms":
            self.sms_sent = True

    @property
    def is_active(self) -> bool:
        """Check if alert is still active."""
        return self.status in [
            AlertStatus.NEW,
            AlertStatus.ACKNOWLEDGED,
            AlertStatus.INVESTIGATING,
        ]

    @property
    def response_time(self) -> timedelta | None:
        """Get time to first response."""
        if not self.acknowledged_at:
            return None
        if self.created_at is None:
            return None
        return self.acknowledged_at - self.created_at

    @property
    def resolution_time(self) -> timedelta | None:
        """Get time to resolution."""
        if not self.resolved_at:
            return None
        if self.created_at is None:
            return None
        return self.resolved_at - self.created_at

    @classmethod
    def get_active_alerts(
        cls,
        user_id: str | None = None,
        severity: AlertSeverity | None = None,
        limit: int = 100,
        session: Any = None,
    ) -> list["SecurityAlert"]:
        """
        Get active alerts.

        Args:
            user_id: Filter by user
            severity: Filter by severity
            limit: Maximum number of alerts
            session: Database session

        Returns:
            List of SecurityAlert instances
        """
        if not session:
            return []

        query = session.query(cls).filter(
            cls.status.in_(
                [AlertStatus.NEW, AlertStatus.ACKNOWLEDGED, AlertStatus.INVESTIGATING]
            )
        )

        if user_id:
            query = query.filter(cls.user_id == user_id)

        if severity:
            query = query.filter(cls.severity == severity)

        result: list[SecurityAlert] = (
            query.order_by(cls.severity.desc(), cls.created_at.desc())
            .limit(limit)
            .all()
        )
        return result

    @classmethod
    def count_by_type(
        cls, start_date: datetime, end_date: datetime, session: Any = None
    ) -> dict[str, Any]:
        """
        Count alerts by type in date range.

        Args:
            start_date: Start date
            end_date: End date
            session: Database session

        Returns:
            Dictionary of counts by alert type
        """
        if not session:
            return {}

        from sqlalchemy import func

        results = (
            session.query(cls.alert_type, func.count(cls.id))
            .filter(cls.created_at >= start_date, cls.created_at <= end_date)
            .group_by(cls.alert_type)
            .all()
        )

        return {alert_type.value: count for alert_type, count in results}

    def to_dict(self) -> dict[str, Any]:
        """
        Convert to dictionary.

        Returns:
            Dictionary representation
        """
        return {
            "id": str(self.id),
            "alert_type": self.alert_type.value,
            "severity": self.severity.value,
            "status": self.status.value,
            "title": self.title,
            "description": self.description,
            "user_id": str(self.user_id) if self.user_id else None,
            "username": self.username,
            "ip_address": self.ip_address,
            "country": self.country,
            "city": self.city,
            "risk_score": self.risk_score,
            "confidence_score": self.confidence_score,
            "is_automated": self.is_automated,
            "auto_remediated": self.auto_remediated,
            "actions_taken": self.actions_taken,
            "escalated": self.escalated,
            "escalated_at": (
                self.escalated_at.isoformat() if self.escalated_at else None
            ),
            "acknowledged_at": (
                self.acknowledged_at.isoformat() if self.acknowledged_at else None
            ),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "is_active": self.is_active,
            "response_time_minutes": (
                int(self.response_time.total_seconds() / 60)
                if self.response_time
                else None
            ),
            "resolution_time_minutes": (
                int(self.resolution_time.total_seconds() / 60)
                if self.resolution_time
                else None
            ),
            "created_at": self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        """String representation."""
        return f"<SecurityAlert(id={self.id}, type={self.alert_type.value}, severity={self.severity.value}, status={self.status.value})>"
