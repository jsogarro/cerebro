"""Runtime tests for the fail-closed HTTP and WebSocket auth boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from jose import JWTError
from starlette.routing import Mount, WebSocketRoute
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from src.api.auth.auth_router import get_jwt_service as auth_get_jwt_service
from src.api.auth.auth_router import get_password_service
from src.auth.models import TokenPayload
from src.middleware.auth_middleware import (
    PUBLIC_ROUTE_ALLOWLIST,
    AuthMiddleware,
    get_jwt_service,
)
from src.models.db.session import get_session

EXPECTED_PUBLIC_ROUTES = {
    ("GET", "/health"),
    ("GET", "/live"),
    ("GET", "/ready"),
    ("GET", "/ws/health"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/forgot-password"),
    ("POST", "/api/v1/auth/reset-password"),
    ("GET", "/api/v1/auth/verify-email"),
}

EXPECTED_MOUNTED_WEBSOCKET_ROUTES = {
    "/api/v1/supervisors/coordination/ws",
    "/api/v1/supervisors/{supervisor_type}/ws",
    "/api/v1/talkhier/sessions/{session_id}/live",
    "/api/v1/talkhier/interactive",
    "/api/v1/talkhier/coordination",
    "/ws",
    "/ws/projects/{project_id}",
    "/ws/cli/{project_id}",
    "/ws/runs/{run_id}",
}


def _token_payload() -> TokenPayload:
    """Build a deterministic identity for the boundary tests."""
    now = datetime.now(UTC)
    return TokenPayload(
        sub="user-123",
        email="user@example.com",
        roles=["researcher"],
        permissions=["read:projects"],
        organization_id="org-123",
        jti="jti-123",
        iat=now,
        exp=now + timedelta(minutes=15),
    )


class _StubJWTService:
    """Small JWT service fake that exercises the middleware boundary."""

    def __init__(self, payload: TokenPayload | None = None) -> None:
        self.payload = payload or _token_payload()
        self.calls: list[str] = []
        self.failure: Exception | None = None

    async def validate_token(self, token: str) -> TokenPayload:
        self.calls.append(token)
        if self.failure is not None:
            raise self.failure
        if token != "valid-token":
            raise JWTError("invalid token")
        return self.payload


class _SuccessfulDurableAuditLogger:
    """In-memory durable audit double for clients that skip application lifespan."""

    def __init__(self) -> None:
        self.pending_events: list[dict[str, Any]] = []
        self.persisted_events: list[dict[str, Any]] = []

    async def log_event(self, **event: Any) -> str:
        self.pending_events.append(event)
        return f"test-audit-event-{len(self.pending_events)}"

    async def flush_buffer(self) -> None:
        self.persisted_events.extend(self.pending_events)
        self.pending_events.clear()


@contextmanager
def _override_audit_logger(
    app: FastAPI,
) -> Iterator[_SuccessfulDurableAuditLogger]:
    """Provide durable audit state while a test client bypasses lifespan."""
    audit_logger = _SuccessfulDurableAuditLogger()
    missing = object()
    previous = getattr(app.state, "audit_logger", missing)
    app.state.audit_logger = audit_logger
    try:
        yield audit_logger
    finally:
        if previous is missing:
            delattr(app.state, "audit_logger")
        else:
            app.state.audit_logger = previous


@contextmanager
def _override_jwt_service(
    app: FastAPI, service_factory: Callable[[], Any]
) -> Iterator[None]:
    app.dependency_overrides[get_jwt_service] = service_factory
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_jwt_service, None)


def _production_app() -> FastAPI:
    """Return the real application entry point used by runtime tests."""
    from src.api.main import app

    return app


@contextmanager
def _client(app: FastAPI) -> Iterator[TestClient]:
    """Use the real ASGI stack without requiring external lifespan services."""
    with _override_audit_logger(app):
        client = TestClient(app)
        try:
            yield client
        finally:
            client.close()


async def _fake_session() -> AsyncIterator[None]:
    """Satisfy dependency resolution while public request validation runs."""
    yield None


@contextmanager
def _override_public_dependencies(
    app: FastAPI, service: _StubJWTService
) -> Iterator[None]:
    """Keep public route tests at the auth boundary, before handler effects."""
    app.dependency_overrides[get_session] = _fake_session
    app.dependency_overrides[auth_get_jwt_service] = lambda: service
    app.dependency_overrides[get_password_service] = lambda: object()
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(auth_get_jwt_service, None)
        app.dependency_overrides.pop(get_password_service, None)


def _materialize_route(path: str) -> str:
    """Replace route parameters with values sufficient to reach middleware."""
    return (
        path.replace("{project_id}", str(UUID(int=1)))
        .replace("{run_id}", "run-123")
        .replace("{report_id}", "report-123")
        .replace("{user_id}", "user-123")
        .replace("{device_id}", "device-123")
        .replace("{execution_id}", "execution-123")
        .replace("{supervisor_type}", "research")
        .replace("{agent_type}", "research")
        .replace("{session_id}", "session-123")
    )


def _mounted_http_routes(app: FastAPI) -> set[tuple[str, str]]:
    signatures: set[tuple[str, str]] = set()
    for route in app.routes:
        if isinstance(route, APIRoute):
            signatures.update(
                (method, route.path)
                for method in route.methods
                if method not in {"HEAD", "OPTIONS"}
            )
        elif isinstance(route, Mount):
            signatures.add(("MOUNT", route.path))
    return signatures


def _mounted_websocket_routes(app: FastAPI) -> set[str]:
    return {route.path for route in app.routes if isinstance(route, WebSocketRoute)}


def _assert_websocket_denied(
    client: TestClient,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> None:
    try:
        with client.websocket_connect(path, headers=headers or {}) as websocket:
            message = websocket.receive()
            assert message["type"] == "websocket.close"
            assert message.get("code") in {1008, 1011}
    except WebSocketDisconnect as error:
        assert error.code in {1008, 1011}
    except WebSocketDenialResponse as error:
        assert error.status_code in {401, 403}


def test_public_route_allowlist_is_exact_and_every_entry_is_mounted() -> None:
    app = _production_app()

    assert set(PUBLIC_ROUTE_ALLOWLIST) == EXPECTED_PUBLIC_ROUTES
    mounted_http_routes = _mounted_http_routes(app)
    assert mounted_http_routes >= EXPECTED_PUBLIC_ROUTES
    assert ("GET", "/ws/health") in mounted_http_routes
    assert _mounted_websocket_routes(app).isdisjoint(
        {route for _method, route in PUBLIC_ROUTE_ALLOWLIST}
    )


def test_public_http_routes_reach_the_real_app_without_authentication() -> None:
    app = _production_app()
    service = _StubJWTService()
    requests = [
        ("GET", "/health", None),
        ("GET", "/live", None),
        ("GET", "/ready", None),
        ("GET", "/ws/health", None),
        ("POST", "/api/v1/auth/login", {}),
        ("POST", "/api/v1/auth/register", {}),
        ("POST", "/api/v1/auth/forgot-password", {}),
        ("POST", "/api/v1/auth/reset-password", {}),
        ("GET", "/api/v1/auth/verify-email", None),
    ]

    with (
        _client(app) as client,
        _override_jwt_service(app, lambda: service),
        _override_public_dependencies(app, service),
    ):
        for method, path, payload in requests:
            response = client.request(method, path, json=payload)
            assert response.status_code != 401, (method, path, response.text)

    assert service.calls == []


def test_genuine_cors_preflight_reaches_cors_before_authentication() -> None:
    app = _production_app()
    service = _StubJWTService()

    with _client(app) as client, _override_jwt_service(app, lambda: service):
        response = client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-methods"]
    assert service.calls == []


def test_non_preflight_options_request_remains_protected() -> None:
    app = _production_app()
    service = _StubJWTService()

    with _client(app) as client, _override_jwt_service(app, lambda: service):
        response = client.options(
            "/api/v1/auth/login",
            headers={"Origin": "http://localhost:3000"},
        )

    assert response.status_code == 401
    assert "access-control-allow-origin" not in response.headers
    assert service.calls == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/health/"),
        ("POST", "/health"),
        ("GET", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/login/"),
    ],
)
def test_public_allowlist_matches_method_and_path_exactly(
    method: str, path: str
) -> None:
    app = _production_app()
    service = _StubJWTService()

    with _client(app) as client, _override_jwt_service(app, lambda: service):
        response = client.request(method, path)

    assert response.status_code == 401
    assert service.calls == []


def test_every_mounted_http_route_and_metrics_mount_rejects_anonymous_access() -> None:
    app = _production_app()
    service = _StubJWTService()

    with _client(app) as client, _override_jwt_service(app, lambda: service):
        for method, route in _mounted_http_routes(app):
            if (method, route) in EXPECTED_PUBLIC_ROUTES:
                continue
            path = "/metrics" if method == "MOUNT" else _materialize_route(route)
            response = client.request(method if method != "MOUNT" else "GET", path)
            assert response.status_code == 401, (method, route, response.text)

        for path in ("/docs", "/redoc", "/openapi.json"):
            response = client.get(path)
            assert response.status_code == 401, (path, response.text)

    assert service.calls == []


def test_missing_credentials_are_rejected_before_handler_validation() -> None:
    app = _production_app()
    service = _StubJWTService()

    with _client(app) as client, _override_jwt_service(app, lambda: service):
        response = client.post("/api/v1/query/research", json={})

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Authentication required"
    assert service.calls == []


@pytest.mark.parametrize(
    ("authorization", "expected_calls"),
    [("Bearer not-a-token", ["not-a-token"]), ("Basic valid-token", [])],
)
def test_invalid_or_non_bearer_credentials_are_not_authentication(
    authorization: str,
    expected_calls: list[str],
) -> None:
    app = _production_app()
    service = _StubJWTService()

    with _client(app) as client, _override_jwt_service(app, lambda: service):
        response = client.get(
            "/api/v1/query/routing/strategies",
            headers={"Authorization": authorization},
        )

    assert response.status_code == 401
    assert service.calls == expected_calls


def test_auth_store_failure_returns_service_unavailable() -> None:
    app = _production_app()
    service = _StubJWTService()
    service.failure = ConnectionError("redis unavailable")

    with _client(app) as client, _override_jwt_service(app, lambda: service):
        response = client.get(
            "/api/v1/query/routing/strategies",
            headers={"Authorization": "Bearer valid-token"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Authentication service unavailable"


def test_valid_token_populates_downstream_request_identity() -> None:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    service = _StubJWTService()

    @app.get("/protected")
    async def protected(request: Request) -> dict[str, Any]:
        return {
            "user": request.state.user,
            "user_id": request.state.user_id,
            "organization_id": request.state.organization_id,
            "subject": request.state.token_payload.sub,
        }

    with _client(app) as client, _override_jwt_service(app, lambda: service):
        response = client.get(
            "/protected",
            headers={"Authorization": "Bearer valid-token"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "user": "user-123",
        "user_id": "user-123",
        "organization_id": "org-123",
        "subject": "user-123",
    }


@pytest.mark.parametrize(
    "path",
    [
        "/ws",
        "/ws/projects/00000000-0000-0000-0000-000000000001",
        "/ws/cli/00000000-0000-0000-0000-000000000001",
        "/ws/runs/run-123",
        "/api/v1/supervisors/coordination/ws",
        "/api/v1/supervisors/research/ws",
        "/api/v1/talkhier/sessions/session-123/live",
        "/api/v1/talkhier/interactive",
        "/api/v1/talkhier/coordination",
    ],
)
def test_every_mounted_websocket_route_rejects_anonymous_handshakes(path: str) -> None:
    app = _production_app()
    service = _StubJWTService()

    with _client(app) as client, _override_jwt_service(app, lambda: service):
        _assert_websocket_denied(client, path)

    assert service.calls == []


def test_websocket_auth_uses_query_token_and_not_authorization_header() -> None:
    app = _production_app()
    service = _StubJWTService()

    with _client(app) as client, _override_jwt_service(app, lambda: service):
        _assert_websocket_denied(
            client,
            "/ws",
            headers={"Authorization": "Bearer valid-token"},
        )

    assert service.calls == []


def test_websocket_auth_service_failure_closes_connection() -> None:
    app = _production_app()
    service = _StubJWTService()
    service.failure = ConnectionError("redis unavailable")

    with _client(app) as client, _override_jwt_service(app, lambda: service):
        _assert_websocket_denied(client, "/ws?token=valid-token")

    assert service.calls == ["valid-token"]


def test_invalid_websocket_token_is_denied_before_endpoint_execution() -> None:
    app = _production_app()
    service = _StubJWTService()

    with _client(app) as client, _override_jwt_service(app, lambda: service):
        _assert_websocket_denied(
            client, "/api/v1/supervisors/coordination/ws?token=not-a-token"
        )

    assert service.calls == ["not-a-token"]


def test_runtime_websocket_inventory_has_no_public_entries() -> None:
    app = _production_app()
    mounted_websockets = _mounted_websocket_routes(app)

    assert mounted_websockets == EXPECTED_MOUNTED_WEBSOCKET_ROUTES
