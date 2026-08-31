"""Runtime regressions for the security controls mounted on the API app."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from src.models.db.audit_log import AuditEventType, AuditLog
from src.security.audit_logger import AuditLogger


class _RecordingSession:
    """Async-session fake used to observe rows created by a real request."""

    def __init__(self, rows: list[AuditLog]) -> None:
        self.rows = rows

    async def __aenter__(self) -> _RecordingSession:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def add(self, row: AuditLog) -> None:
        self.rows.append(row)

    async def commit(self) -> None:
        return None


class _RecordingSessionFactory:
    """Callable session factory with in-memory committed rows."""

    def __init__(self) -> None:
        self.rows: list[AuditLog] = []

    def __call__(self) -> _RecordingSession:
        return _RecordingSession(self.rows)


@pytest.mark.asyncio
async def test_real_app_request_has_security_headers_and_persists_audit_row() -> None:
    """The app entrypoint must reach both controls on an HTTP error response."""
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

    assert response.status_code == 404
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]

    assert len(session_factory.rows) == 1
    row = session_factory.rows[0]
    assert row.event_type is AuditEventType.DATA_ACCESSED
    assert row.action == "GET /this-route-does-not-exist"
    assert row.request_id == "request-123"
    assert row.event_metadata == {
        "method": "GET",
        "path": "/this-route-does-not-exist",
        "status_code": 404,
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
