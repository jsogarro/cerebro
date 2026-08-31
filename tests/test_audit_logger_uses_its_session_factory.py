"""Regression tests for session-factory-backed audit persistence."""

from __future__ import annotations

from typing import Any

import pytest

from src.models.db.audit_log import AuditEventType, AuditLog
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
