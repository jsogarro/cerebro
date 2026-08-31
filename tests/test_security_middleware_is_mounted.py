"""Runtime regressions for the security controls mounted on the API app."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from src.models.db.audit_log import AuditEventType, AuditLog
from src.models.db.security_alert import SecurityAlert
from src.security.audit_logger import AuditLogger


class _RecordingSession:
    """Async-session fake used to observe rows created by a real request."""

    def __init__(self, rows: list[AuditLog | SecurityAlert]) -> None:
        self.rows = rows

    async def __aenter__(self) -> _RecordingSession:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def add(self, row: AuditLog | SecurityAlert) -> None:
        self.rows.append(row)

    async def commit(self) -> None:
        return None


class _RecordingSessionFactory:
    """Callable session factory with in-memory committed rows."""

    def __init__(self) -> None:
        self.rows: list[AuditLog | SecurityAlert] = []

    def __call__(self) -> _RecordingSession:
        return _RecordingSession(self.rows)


@pytest.mark.asyncio
async def test_real_app_request_has_security_headers_and_persists_audit_row() -> None:
    """The app entrypoint must reach both controls on an auth failure."""
    from src.api.main import app

    session_factory = _RecordingSessionFactory()
    audit_logger = AuditLogger(session_factory=session_factory, buffer_size=100)
    missing = object()
    previous = getattr(app.state, "audit_logger", missing)
    app.state.audit_logger = audit_logger

    try:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Request-ID": "request-123"},
        ) as client:
            response = await client.get("/this-route-does-not-exist")
    finally:
        if previous is missing:
            delattr(app.state, "audit_logger")
        else:
            app.state.audit_logger = previous

    assert response.status_code == 401
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]

    audit_rows = [row for row in session_factory.rows if isinstance(row, AuditLog)]
    alert_rows = [row for row in session_factory.rows if isinstance(row, SecurityAlert)]
    assert len(audit_rows) == 2
    assert len(alert_rows) == 1
    admission, outcome = audit_rows
    assert admission.event_type is AuditEventType.DATA_ACCESSED
    assert admission.action == "GET /this-route-does-not-exist"
    assert admission.result == "admitted"
    assert admission.request_id == "request-123"
    assert admission.event_metadata == {
        "method": "GET",
        "path": "/this-route-does-not-exist",
        "phase": "admission",
    }
    assert outcome.event_type is AuditEventType.UNAUTHORIZED_ACCESS
    assert outcome.action == "GET /this-route-does-not-exist"
    assert outcome.result == "failure"
    assert outcome.request_id == "request-123"
    assert outcome.event_metadata == {
        "method": "GET",
        "path": "/this-route-does-not-exist",
        "phase": "outcome",
        "status_code": 401,
    }


@pytest.mark.asyncio
async def test_real_app_request_rejects_unavailable_audit_store() -> None:
    """A configured logger without a store cannot claim the request was audited."""
    from src.api.main import app

    missing_store_logger = AuditLogger(buffer_size=100)
    missing = object()
    previous = getattr(app.state, "audit_logger", missing)
    app.state.audit_logger = missing_store_logger

    try:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get("/this-route-does-not-exist")
    finally:
        if previous is missing:
            delattr(app.state, "audit_logger")
        else:
            app.state.audit_logger = previous

    assert response.status_code == 503
    assert response.headers["X-Audit-Status"] == "unavailable"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.json()["error"]["code"] == "AUDIT_PERSISTENCE_UNAVAILABLE"
