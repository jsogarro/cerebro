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
