"""Rejected requests must not amplify durable audit writes (issue #156)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import structlog
from fastapi import FastAPI
from httpx import ASGITransport

from src.api.middleware.rate_limiter import RateLimitMiddleware
from src.middleware.auth_middleware import AuthMiddleware
from src.security.audit_logger import AuditLogger
from src.security.audit_middleware import AuditTrailMiddleware


class _Session:
    def __init__(self, store: _Store) -> None:
        self.store = store
        self.pending: list[Any] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def add(self, row: Any) -> None:
        self.pending.append(row)

    async def commit(self) -> None:
        if self.store.fail:
            raise RuntimeError("audit store unavailable")
        self.store.commits += 1
        self.store.rows.extend(self.pending)


class _Store:
    def __init__(self, *, fail: bool = False) -> None:
        self.rows: list[Any] = []
        self.commits = 0
        self.fail = fail

    def __call__(self) -> _Session:
        return _Session(self)


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _mini_app(store: _Store) -> tuple[FastAPI, AuditLogger]:
    app = FastAPI()
    app.middleware("http")(AuditTrailMiddleware())
    logger = AuditLogger(session_factory=store, buffer_size=100)
    app.state.audit_logger = logger

    @app.get("/read")
    async def read() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/v1/auth/verify-email")
    async def verify_email() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/mutate")
    async def mutate() -> dict[str, bool]:
        return {"ok": True}

    return app, logger


def _audit_middleware_position(app: FastAPI) -> int:
    return next(
        i
        for i, m in enumerate(app.user_middleware)
        if isinstance(m.kwargs.get("dispatch"), AuditTrailMiddleware)
    )


def test_audit_is_registered_inside_auth_and_rate_limiting() -> None:
    """Outermost-first: auth and rate limiting reject before audit admits."""
    from src.api.main import app

    classes = [m.cls for m in app.user_middleware]
    audit = _audit_middleware_position(app)
    assert audit > classes.index(AuthMiddleware)
    assert audit > classes.index(RateLimitMiddleware)


@pytest.mark.asyncio
@pytest.mark.parametrize("store_fails", [False, True])
async def test_real_app_rejection_writes_nothing_and_is_not_503(
    store_fails: bool,
) -> None:
    from src.api.main import app

    store = _Store(fail=store_fails)
    previous = getattr(app.state, "audit_logger", None)
    app.state.audit_logger = AuditLogger(session_factory=store, buffer_size=100)
    try:
        async with _client(app) as client:
            response = await client.get("/this-route-does-not-exist")
    finally:
        app.state.audit_logger = previous

    assert response.status_code == 401
    assert store.commits == 0
    assert store.rows == []
    assert response.headers["X-Frame-Options"] == "DENY"


@pytest.mark.asyncio
async def test_rejection_is_recorded_cheaply_in_the_log() -> None:
    from src.api.main import app

    app.state.audit_logger = AuditLogger(session_factory=_Store(), buffer_size=100)
    with structlog.testing.capture_logs() as logs:
        async with _client(app) as client:
            await client.get("/this-route-does-not-exist")

    rejected = [e for e in logs if e["event"] == "request_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["status_code"] == 401
    assert rejected[0]["path"] == "/this-route-does-not-exist"


@pytest.mark.asyncio
async def test_read_costs_one_durable_write() -> None:
    store = _Store()
    app, _ = _mini_app(store)
    async with _client(app) as client:
        response = await client.get("/read")

    assert response.status_code == 200
    assert response.headers["X-Audit-Status"] == "persisted"
    assert store.commits == 1
    assert [r.event_metadata["phase"] for r in store.rows] == ["outcome"]


@pytest.mark.asyncio
async def test_mutation_keeps_durable_admission_and_outcome() -> None:
    store = _Store()
    app, _ = _mini_app(store)
    async with _client(app) as client:
        response = await client.post("/mutate")

    assert response.status_code == 200
    assert store.commits == 2
    assert [r.event_metadata["phase"] for r in store.rows] == ["admission", "outcome"]


@pytest.mark.asyncio
async def test_mutation_outcome_failure_does_not_mask_the_completed_response() -> None:
    store = _Store()
    app, _ = _mini_app(store)
    effects: list[str] = []

    @app.put("/effect")
    async def effect() -> dict[str, bool]:
        effects.append("ran")
        store.fail = True  # the store goes down after admission was written
        return {"ok": True}

    async with _client(app) as client:
        response = await client.put("/effect")

    assert effects == ["ran"]
    assert response.status_code == 200
    assert response.headers["X-Audit-Status"] == "unavailable"


@pytest.mark.asyncio
async def test_read_fails_open_when_the_audit_store_is_down() -> None:
    app, _ = _mini_app(_Store(fail=True))
    async with _client(app) as client:
        response = await client.get("/read")

    assert response.status_code == 200
    assert response.headers["X-Audit-Status"] == "unavailable"


@pytest.mark.asyncio
async def test_mutation_still_fails_closed_when_the_audit_store_is_down() -> None:
    app, _ = _mini_app(_Store(fail=True))
    async with _client(app) as client:
        response = await client.post("/mutate")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUDIT_PERSISTENCE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_state_changing_get_is_admitted_like_a_mutation() -> None:
    store = _Store(fail=True)
    app, _ = _mini_app(store)
    async with _client(app) as client:
        response = await client.get("/api/v1/auth/verify-email")

    assert response.status_code == 503


def test_every_get_route_that_commits_is_treated_as_state_changing() -> None:
    """A GET handler that commits must be admitted durably like a mutation.

    Static check on the handler body; a commit hidden in a helper is not seen,
    so add the path to ``_MUTATING_GET_PATHS`` when reviewing such a route.
    """
    import inspect

    from src.api.main import app
    from src.security.audit_middleware import _MUTATING_GET_PATHS

    routes = []
    for route in app.routes:
        # FastAPI includes routers lazily; resolve them to concrete routes.
        contexts = getattr(route, "effective_route_contexts", None)
        routes.extend(contexts() if contexts else [route])
    committing = {
        route.path
        for route in routes
        if "GET" in (getattr(route, "methods", None) or ())
        and hasattr(route, "endpoint")
        and any(
            token in inspect.getsource(route.endpoint)
            for token in (".commit(", ".flush(")
        )
    }

    assert committing <= _MUTATING_GET_PATHS, committing - _MUTATING_GET_PATHS
    assert "/api/v1/auth/verify-email" in committing
