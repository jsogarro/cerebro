"""Audit rows attribute authenticated requests to the token subject."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_jwt_service
from src.models.db.audit_log import AuditLog
from src.security.audit_logger import AuditLogger

ORG = "11111111-1111-1111-1111-111111111111"


class _Session:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def add(self, row: Any) -> None:
        self.rows.append(row)

    async def commit(self) -> None:
        return None


class _SessionFactory:
    def __init__(self) -> None:
        self.rows: list[Any] = []

    def __call__(self) -> _Session:
        return _Session(self.rows)


def _jwt_service_for(subject: str) -> Any:
    class _JWTService:
        async def validate_token(self, _token: str) -> TokenPayload:
            now = datetime.now(UTC)
            return TokenPayload(
                sub=subject,
                email="actor@example.com",
                organization_id=ORG,
                jti="jti",
                iat=now,
                exp=now + timedelta(minutes=5),
            )

    return _JWTService


async def _audited_rows(subject: str) -> list[AuditLog]:
    from src.api.main import app

    factory = _SessionFactory()
    missing = object()
    previous = getattr(app.state, "audit_logger", missing)
    app.state.audit_logger = AuditLogger(session_factory=factory, buffer_size=100)
    app.dependency_overrides[get_jwt_service] = _jwt_service_for(subject)
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(
                "/api/v1/audit-actor-probe",
                headers={"Authorization": "Bearer token"},
            )
    finally:
        app.dependency_overrides.pop(get_jwt_service, None)
        if previous is missing:
            delattr(app.state, "audit_logger")
        else:
            app.state.audit_logger = previous
    assert response.status_code == 404  # authenticated, then no such route
    return [row for row in factory.rows if isinstance(row, AuditLog)]


@pytest.mark.asyncio
async def test_authenticated_request_audit_row_carries_the_subject() -> None:
    subject = str(uuid.uuid4())

    rows = await _audited_rows(subject)

    outcome = rows[-1]
    assert outcome.event_metadata["phase"] == "outcome"
    assert str(outcome.user_id) == subject
    assert outcome.event_metadata["organization_id"] == ORG


@pytest.mark.asyncio
async def test_non_uuid_subject_does_not_break_audit_persistence() -> None:
    """The column is a UUID; an unparsable subject must not poison the flush."""
    rows = await _audited_rows("not-a-uuid")

    assert rows[-1].user_id is None
