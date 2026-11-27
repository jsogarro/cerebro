"""Global audit-event and security-alert reads are superuser-only (decision Q12)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from src.api.routes import admin_security
from src.middleware.auth_middleware import get_current_active_user
from src.models.db.session import get_session

ROUTES = ["/admin/audit-events", "/admin/alerts"]


class _Row:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def to_dict(self) -> dict[str, Any]:
        return self.payload


class _Db:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        rows = [_Row({"id": str(uuid.uuid4()), "at": datetime.now(UTC).isoformat()})]
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))


@pytest.fixture
def app() -> Iterator[FastAPI]:
    app = FastAPI()
    app.include_router(admin_security.router)
    app.dependency_overrides[get_session] = _Db
    yield app


def _as(app: FastAPI, *, superuser: bool) -> httpx.AsyncClient:
    user = SimpleNamespace(id=uuid.uuid4(), is_superuser=superuser, is_active=True)
    app.dependency_overrides[get_current_active_user] = lambda: user
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize("path", ROUTES)
async def test_non_superuser_gets_403(app: FastAPI, path: str) -> None:
    async with _as(app, superuser=False) as client:
        assert (await client.get(path)).status_code == 403


@pytest.mark.parametrize("path", ROUTES)
async def test_superuser_reads_globally(app: FastAPI, path: str) -> None:
    async with _as(app, superuser=True) as client:
        response = await client.get(path)
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1


@pytest.mark.parametrize("path", ROUTES)
async def test_limit_is_bounded(app: FastAPI, path: str) -> None:
    async with _as(app, superuser=True) as client:
        assert (await client.get(path, params={"limit": 100000})).status_code == 422


class _AckDb:
    def __init__(self, alert: Any | None) -> None:
        self.alert = alert
        self.commits = 0

    async def get(self, model: Any, key: Any) -> Any:
        return self.alert if self.alert is not None and key == self.alert.id else None

    async def commit(self) -> None:
        self.commits += 1


def _alert() -> Any:
    from src.models.db.security_alert import (
        AlertSeverity,
        AlertStatus,
        AlertType,
        SecurityAlert,
    )

    return SecurityAlert(
        id=uuid.uuid4(),
        alert_type=AlertType.FAILED_LOGIN_ATTEMPTS,
        severity=AlertSeverity.HIGH,
        status=AlertStatus.NEW,
        title="t",
        description="d",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


async def test_non_superuser_cannot_acknowledge(app: FastAPI) -> None:
    alert = _alert()
    app.dependency_overrides[get_session] = lambda: _AckDb(alert)
    async with _as(app, superuser=False) as client:
        response = await client.post(f"/admin/alerts/{alert.id}/acknowledge")
    assert response.status_code == 403
    assert alert.acknowledged_by is None


async def test_superuser_acknowledge_records_who(app: FastAPI) -> None:
    alert = _alert()
    app.dependency_overrides[get_session] = lambda: _AckDb(alert)
    async with _as(app, superuser=True) as client:
        response = await client.post(f"/admin/alerts/{alert.id}/acknowledge")
        who = app.dependency_overrides[get_current_active_user]().id
    assert response.status_code == 200
    assert response.json()["status"] == "acknowledged"
    assert alert.acknowledged_by == str(who)
    assert alert.acknowledged_at is not None


async def test_acknowledging_an_unknown_alert_is_404(app: FastAPI) -> None:
    app.dependency_overrides[get_session] = lambda: _AckDb(None)
    async with _as(app, superuser=True) as client:
        response = await client.post(f"/admin/alerts/{uuid.uuid4()}/acknowledge")
    assert response.status_code == 404


async def test_acknowledge_is_committed_before_the_response_is_sent(
    app: FastAPI,
) -> None:
    # The session dependency commits only after the response has gone out, so a
    # client reading straight after the 200 could still see the old row.
    alert = _alert()
    db = _AckDb(alert)
    app.dependency_overrides[get_session] = lambda: db
    async with _as(app, superuser=True) as client:
        response = await client.post(f"/admin/alerts/{alert.id}/acknowledge")
    assert response.status_code == 200
    assert db.commits == 1
