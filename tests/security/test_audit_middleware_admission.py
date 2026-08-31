"""Regression coverage for fail-closed request admission auditing."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

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
