"""Compatibility contracts for the historical audit log schema."""

from src.models.db.audit_log import AuditLog
from src.models.db.security_alert import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    SecurityAlert,
)


def test_event_metadata_uses_the_historical_metadata_column() -> None:
    """Keep the Python event_metadata API backed by audit_logs.metadata."""
    mapped_column = AuditLog.__mapper__.attrs.event_metadata.columns[0]

    assert mapped_column is AuditLog.__table__.columns["metadata"]
    assert mapped_column.name == "metadata"
    assert "event_metadata" not in AuditLog.__table__.columns


def test_alert_metadata_uses_the_historical_metadata_column() -> None:
    """Keep the Python alert_metadata API backed by security_alerts.metadata."""
    mapped_column = SecurityAlert.__mapper__.attrs.alert_metadata.columns[0]

    assert mapped_column is SecurityAlert.__table__.columns["metadata"]
    assert mapped_column.name == "metadata"
    assert "alert_metadata" not in SecurityAlert.__table__.columns


def test_audit_event_type_enum_persists_lowercase_values() -> None:
    """Bind the PostgreSQL enum to the live schema's lowercase values."""
    event_type_enum = AuditLog.__table__.columns["event_type"].type

    assert event_type_enum.enums == [
        "login_success",
        "login_failed",
        "logout",
        "password_change",
        "password_reset_request",
        "password_reset_complete",
        "account_created",
        "account_activated",
        "account_deactivated",
        "account_deleted",
        "account_locked",
        "account_unlocked",
        "email_verified",
        "email_changed",
        "mfa_enabled",
        "mfa_disabled",
        "mfa_verified",
        "mfa_failed",
        "mfa_backup_used",
        "oauth_connected",
        "oauth_disconnected",
        "oauth_login",
        "session_created",
        "session_refreshed",
        "session_revoked",
        "session_expired",
        "api_key_created",
        "api_key_revoked",
        "api_key_used",
        "permission_granted",
        "permission_revoked",
        "role_assigned",
        "role_removed",
        "suspicious_activity",
        "rate_limit_exceeded",
        "invalid_token",
        "unauthorized_access",
        "system_breach",
        "data_exfiltration",
        "sql_injection_attempt",
        "xss_attempt",
        "data_accessed",
        "data_modified",
        "data_deleted",
        "data_exported",
    ]


def test_audit_severity_enum_persists_lowercase_values() -> None:
    """Bind severity values to the live PostgreSQL enum labels."""
    severity_enum = AuditLog.__table__.columns["severity"].type

    assert severity_enum.enums == ["info", "warning", "error", "critical"]


def test_security_alert_type_enum_persists_lowercase_values() -> None:
    """Bind alert types to the live PostgreSQL enum labels."""
    alert_type_enum = SecurityAlert.__table__.columns["alert_type"].type

    assert alert_type_enum.enums == [member.value for member in AlertType]


def test_security_alert_severity_enum_persists_lowercase_values() -> None:
    """Bind alert severities to the live PostgreSQL enum labels."""
    severity_enum = SecurityAlert.__table__.columns["severity"].type

    assert severity_enum.enums == [member.value for member in AlertSeverity]


def test_security_alert_status_enum_persists_lowercase_values() -> None:
    """Bind alert statuses to the live PostgreSQL enum labels."""
    status_enum = SecurityAlert.__table__.columns["status"].type

    assert status_enum.enums == [member.value for member in AlertStatus]
