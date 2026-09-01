"""Regression coverage for fail-closed request admission auditing."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from src.models.db.audit_log import AuditEventType
from src.security.audit_logger import AuditLogger
from src.security.audit_middleware import AuditTrailMiddleware


class _FailingSession:
    async def __aenter__(self) -> _FailingSession:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def add(self, _row: Any) -> None:
        return None

    async def commit(self) -> None:
        raise RuntimeError("audit store unavailable")


class _FailingSessionFactory:
    def __call__(self) -> _FailingSession:
        return _FailingSession()


class _RecordingAuditLogger:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def log_event(self, **kwargs: Any) -> None:
        self.events.append(kwargs)

    async def flush_buffer(self) -> None:
        return None


@pytest.mark.asyncio
async def test_login_admission_is_not_recorded_as_login_success() -> None:
    """Only a completed login request can produce a login-success event."""
    app = FastAPI()
    app.middleware("http")(AuditTrailMiddleware())
    audit_logger = _RecordingAuditLogger()
    app.state.audit_logger = audit_logger

    @app.post("/auth/login")
    async def login() -> dict[str, bool]:
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/auth/login")

    assert response.status_code == 200
    assert audit_logger.events[0]["metadata"]["phase"] == "admission"
    assert audit_logger.events[0]["event_type"] == AuditEventType.DATA_ACCESSED
    assert audit_logger.events[0]["event_type"] != AuditEventType.LOGIN_SUCCESS
    assert audit_logger.events[1]["metadata"]["phase"] == "outcome"
    assert audit_logger.events[1]["event_type"] == AuditEventType.LOGIN_SUCCESS


@pytest.mark.asyncio
async def test_failed_admission_audit_blocks_endpoint_side_effect() -> None:
    """A mutation is not admitted when its intent cannot be durably audited."""
    app = FastAPI()
    app.middleware("http")(AuditTrailMiddleware())
    app.state.audit_logger = AuditLogger(
        session_factory=_FailingSessionFactory(),
        buffer_size=100,
    )
    side_effects: list[str] = []

    @app.post("/mutate")
    async def mutate() -> dict[str, bool]:
        side_effects.append("executed")
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/mutate")

    assert response.status_code == 503
    assert response.headers["X-Audit-Status"] == "unavailable"
    assert response.json()["error"]["code"] == "AUDIT_PERSISTENCE_UNAVAILABLE"
    assert side_effects == []
