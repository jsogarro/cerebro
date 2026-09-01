"""Regression tests for session-factory-backed audit persistence."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from src.models.db.audit_log import AuditEventType, AuditLog, AuditSeverity
from src.models.db.security_alert import SecurityAlert
from src.security.audit_logger import AuditLogger, AuditPersistenceError


class _RecordingSession:
    """Small async-session fake that records committed ORM entities."""

    def __init__(self, rows: list[AuditLog]) -> None:
        self.rows = rows
        self.commit_count = 0

    async def __aenter__(self) -> _RecordingSession:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def add(self, row: AuditLog) -> None:
        self.rows.append(row)

    async def commit(self) -> None:
        self.commit_count += 1


class _RecordingSessionFactory:
    """Callable async-session factory used by the audit logger tests."""

    def __init__(self) -> None:
        self.rows: list[AuditLog] = []
        self.sessions: list[_RecordingSession] = []

    def __call__(self) -> _RecordingSession:
        session = _RecordingSession(self.rows)
        self.sessions.append(session)
        return session


def _bind_postgresql_json(column: Any, value: Any) -> Any:
    """Apply the mapped column's real PostgreSQL JSON bind processor."""
    processor = column.type.bind_processor(postgresql.dialect())
    assert processor is not None
    return json.loads(processor(value))


class _PostgresJsonSession:
    """Session fake that exercises SQLAlchemy's PostgreSQL JSON bind path."""

    def __init__(
        self,
        rows: list[AuditLog | SecurityAlert],
        bound_alert_evidence: list[Any],
        bound_event_metadata: list[Any],
    ) -> None:
        self.rows = rows
        self.bound_alert_evidence = bound_alert_evidence
        self.bound_event_metadata = bound_event_metadata
        self.pending: list[AuditLog | SecurityAlert] = []

    async def __aenter__(self) -> _PostgresJsonSession:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def add(self, row: AuditLog | SecurityAlert) -> None:
        self.rows.append(row)
        self.pending.append(row)

    async def commit(self) -> None:
        """Run the same JSON bind conversion a PostgreSQL driver receives."""
        for row in self.pending:
            if isinstance(row, SecurityAlert):
                self.bound_alert_evidence.append(
                    _bind_postgresql_json(
                        SecurityAlert.__table__.columns["evidence"],
                        row.evidence,
                    )
                )
            elif isinstance(row, AuditLog):
                self.bound_event_metadata.append(
                    _bind_postgresql_json(
                        AuditLog.__table__.columns["metadata"],
                        row.event_metadata,
                    )
                )


class _PostgresJsonSessionFactory:
    """Factory for observing JSON payloads at the SQLAlchemy bind boundary."""

    def __init__(self) -> None:
        self.rows: list[AuditLog | SecurityAlert] = []
        self.bound_alert_evidence: list[Any] = []
        self.bound_event_metadata: list[Any] = []

    def __call__(self) -> _PostgresJsonSession:
        return _PostgresJsonSession(
            self.rows,
            self.bound_alert_evidence,
            self.bound_event_metadata,
        )


@pytest.mark.asyncio
async def test_audit_logger_flushes_through_its_injected_session_factory() -> None:
    """A flush opens a fresh session and commits an observable audit row."""
    session_factory = _RecordingSessionFactory()
    logger = AuditLogger(session_factory=session_factory, buffer_size=100)

    await logger.log_event(
        event_type=AuditEventType.DATA_ACCESSED,
        action="GET /api/v1/example",
        metadata={"organization_id": "tenant-123"},
    )
    await logger.flush_buffer()

    assert len(session_factory.sessions) == 1
    assert len(session_factory.rows) == 1
    assert session_factory.rows[0].event_type is AuditEventType.DATA_ACCESSED
    assert session_factory.rows[0].action == "GET /api/v1/example"
    assert session_factory.rows[0].event_metadata == {"organization_id": "tenant-123"}
    assert session_factory.sessions[0].commit_count == 1
    assert logger.metrics["events_flushed"] == 1


@pytest.mark.asyncio
async def test_unauthorized_event_persists_json_safe_alert_and_event_metadata() -> None:
    """An unauthorized event survives PostgreSQL JSON binding without data loss."""
    session_factory = _PostgresJsonSessionFactory()
    logger = AuditLogger(session_factory=session_factory, buffer_size=100)
    observed_at = datetime(2026, 8, 31, 12, 34, 56, 123456, tzinfo=UTC)
    request_id = UUID("550e8400-e29b-41d4-a716-446655440000")
    metadata = {
        "organization_id": "tenant-123",
        "context": {
            "request_id": request_id,
            "observed_at": observed_at,
        },
        "related_events": (
            AuditEventType.DATA_ACCESSED,
            AuditEventType.UNAUTHORIZED_ACCESS,
        ),
    }

    await logger.log_event(
        event_type=AuditEventType.UNAUTHORIZED_ACCESS,
        action="GET /api/v1/agents",
        metadata=metadata,
        severity=AuditSeverity.CRITICAL,
    )
    await logger.flush_buffer()

    expected_metadata = {
        "organization_id": "tenant-123",
        "context": {
            "request_id": str(request_id),
            "observed_at": observed_at.isoformat(),
        },
        "related_events": ["data_accessed", "unauthorized_access"],
    }

    assert session_factory.bound_event_metadata == [expected_metadata]
    assert len(session_factory.bound_alert_evidence) == 1
    alert_log = session_factory.bound_alert_evidence[0]["audit_log"]
    assert alert_log["event_type"] == "unauthorized_access"
    assert alert_log["severity"] == "critical"
    assert alert_log["action"] == "GET /api/v1/agents"
    assert alert_log["metadata"] == expected_metadata
    assert datetime.fromisoformat(alert_log["created_at"]).tzinfo is not None
    alerts = [row for row in session_factory.rows if isinstance(row, SecurityAlert)]
    assert len(alerts) == 1
    assert alerts[0].description == "Security event: unauthorized_api_access"
    assert logger.metrics["alerts_generated"] == 1
    assert logger.metrics["events_flushed"] == 1


@pytest.mark.asyncio
async def test_audit_logger_without_a_store_exposes_persistence_failure() -> None:
    """A missing database cannot be mistaken for a successful flush."""
    logger = AuditLogger(buffer_size=100)
    await logger.log_event(
        event_type=AuditEventType.DATA_ACCESSED,
        action="GET /api/v1/example",
    )

    with pytest.raises(AuditPersistenceError, match="session factory"):
        await logger.flush_buffer()

    assert logger.metrics["events_flushed"] == 0
    assert logger.metrics["errors"] == 1
