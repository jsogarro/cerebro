"""Every mounted route authenticates itself, not only through middleware.

``AuthMiddleware`` is the first line of defence and the runtime tests in
``test_route_authentication_boundary.py`` prove it rejects anonymous callers.
This guard is the second: each non-public route must also resolve
``get_current_token`` in its own dependency tree, so removing or bypassing the
middleware does not silently open the route.

Newer FastAPI releases keep included routers as ``_IncludedRouter`` wrappers in
``app.routes`` instead of flattening them into ``APIRoute`` entries, so the
survey recurses through both shapes.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, APIWebSocketRoute

from src.api.websocket.auth import require_authenticated_websocket
from src.middleware.auth_middleware import PUBLIC_ROUTE_ALLOWLIST, get_current_token


def _resolves(dependant: Dependant, target: object) -> bool:
    return dependant.call is target or any(
        _resolves(sub, target) for sub in dependant.dependencies
    )


def _join(prefix: str, path: str) -> str:
    return f"{prefix.rstrip('/')}{path}" if prefix else path


def _survey(
    routes: list[Any], prefix: str = "", included: tuple[object, ...] = ()
) -> Iterator[tuple[Any, str, tuple[object, ...]]]:
    """Yield (route, full path, include-level dependency callables)."""
    for route in routes:
        context = getattr(route, "include_context", None)
        nested = getattr(getattr(route, "original_router", None), "routes", None)
        if context is not None and nested is not None:
            yield from _survey(
                nested,
                _join(prefix, context.prefix or ""),
                included + tuple(d.dependency for d in context.dependencies),
            )
        elif isinstance(route, (APIRoute, APIWebSocketRoute)):
            yield route, _join(prefix, route.path), included


def _authenticates(route: Any, included: tuple[object, ...], target: object) -> bool:
    return target in included or _resolves(route.dependant, target)


def _routes_without_auth(app: FastAPI) -> list[tuple[str, str]]:
    missing: list[tuple[str, str]] = []
    for route, path, included in _survey(app.routes):
        if not isinstance(route, APIRoute):
            continue
        if _authenticates(route, included, get_current_token):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            if (method, path) not in PUBLIC_ROUTE_ALLOWLIST:
                missing.append((method, path))
    return missing


def test_every_non_public_route_resolves_get_current_token() -> None:
    from src.api.main import app

    api_routes = [r for r, _, _ in _survey(app.routes) if isinstance(r, APIRoute)]
    assert len(api_routes) > 50, "route survey found no plausible application"

    assert _routes_without_auth(app) == []


def test_every_websocket_route_resolves_websocket_authentication() -> None:
    from src.api.main import app

    ws_routes = [
        (r, inc)
        for r, _, inc in _survey(app.routes)
        if isinstance(r, APIWebSocketRoute)
    ]
    assert ws_routes, "route survey found no websocket routes"

    assert [
        r.path
        for r, inc in ws_routes
        if not _authenticates(r, inc, require_authenticated_websocket)
    ] == []
