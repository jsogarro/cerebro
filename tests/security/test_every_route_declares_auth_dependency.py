"""Every mounted HTTP route authenticates itself, not only through middleware.

``AuthMiddleware`` is the first line of defence and the runtime tests in
``test_route_authentication_boundary.py`` prove it rejects anonymous callers.
This guard is the second: each non-public route must also resolve
``get_current_token`` in its own dependency tree, so removing or bypassing the
middleware does not silently open the route.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, APIWebSocketRoute

from src.api.websocket.auth import require_authenticated_websocket
from src.middleware.auth_middleware import PUBLIC_ROUTE_ALLOWLIST, get_current_token


def _resolves(dependant: Dependant, target: object) -> bool:
    return dependant.call is target or any(
        _resolves(sub, target) for sub in dependant.dependencies
    )


def _routes_without_auth(app: FastAPI) -> list[tuple[str, str]]:
    missing: list[tuple[str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if _resolves(route.dependant, get_current_token):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            if (method, route.path) not in PUBLIC_ROUTE_ALLOWLIST:
                missing.append((method, route.path))
    return missing


def test_every_non_public_route_resolves_get_current_token() -> None:
    from src.api.main import app

    api_routes = [r for r in app.routes if isinstance(r, APIRoute)]
    assert len(api_routes) > 50, "route survey found no plausible application"

    assert _routes_without_auth(app) == []


def test_every_websocket_route_resolves_websocket_authentication() -> None:
    from src.api.main import app

    ws_routes = [r for r in app.routes if isinstance(r, APIWebSocketRoute)]
    assert ws_routes, "route survey found no websocket routes"

    assert [
        r.path
        for r in ws_routes
        if not _resolves(r.dependant, require_authenticated_websocket)
    ] == []
