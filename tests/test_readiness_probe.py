"""``/ready`` is the Kubernetes readiness probe, so it must reflect the dependencies."""

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api.routes import health


class _Session:
    def __init__(self, error: Exception | None) -> None:
        self.error = error

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def execute(self, _statement: object) -> None:
        if self.error:
            raise self.error


class _Redis:
    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self.closed = False

    async def ping(self) -> bool:
        if self.error:
            raise self.error
        return True

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Return ``call(db_error, redis_error)`` hitting /ready on the real app."""
    from src.api.main import app

    redis_clients: list[_Redis] = []

    def call(
        db_error: Exception | None = None, redis_error: Exception | None = None
    ) -> Any:
        monkeypatch.setattr(
            health, "get_session_factory", lambda: lambda: _Session(db_error)
        )

        def from_url(_url: str) -> _Redis:
            client = _Redis(redis_error)
            redis_clients.append(client)
            return client

        monkeypatch.setattr(health, "redis", SimpleNamespace(from_url=from_url))
        # No Authorization header: /ready must stay public.
        response = TestClient(app).get("/ready")
        return response, redis_clients

    yield call


def test_ready_when_both_dependencies_answer(probe: Any) -> None:
    response, clients = probe()
    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok", "redis": "ok"}
    assert clients[0].closed


def test_database_failure_is_503(probe: Any) -> None:
    response, _ = probe(db_error=ConnectionError("db down"))
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "unavailable", "redis": "ok"}


def test_redis_failure_is_503(probe: Any) -> None:
    response, clients = probe(redis_error=ConnectionError("redis down"))
    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "redis": "unavailable"}
    assert clients[0].closed


def test_uninitialised_database_is_503_not_500(
    probe: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    response, _ = probe(db_error=RuntimeError("Database not initialized"))
    assert response.status_code == 503


def test_hung_dependency_times_out(probe: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    monkeypatch.setattr(health, "_PROBE_TIMEOUT_SECONDS", 0.05)

    async def hang() -> None:
        await asyncio.sleep(5)

    monkeypatch.setattr(health, "_check_database", hang)
    response, _ = probe()
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "unavailable"


def test_temporal_is_not_reported(probe: Any) -> None:
    response, _ = probe()
    assert "temporal" not in response.json()["checks"]
