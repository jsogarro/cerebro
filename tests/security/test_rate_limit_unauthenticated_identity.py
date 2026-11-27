"""Unauthenticated rate limiting keys on the client, not on caller-chosen headers."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import httpx
import pytest
from fastapi import HTTPException
from httpx import ASGITransport

from src.api.middleware.rate_limiter import InMemoryRateLimitStore, RateLimitMiddleware
from src.models.db.session import get_session

LOGIN = "/api/v1/auth/login"
LIMIT = 3


def _mounted_rate_limiter(app: object) -> RateLimitMiddleware:
    layer = getattr(app, "middleware_stack", None)
    while layer is not None and not isinstance(layer, RateLimitMiddleware):
        layer = getattr(layer, "app", None)
    assert layer is not None, "RateLimitMiddleware is not mounted on the app"
    return layer


async def _no_database() -> None:
    # Reaching the handler is all these tests need; failing fast here keeps
    # a missing database from producing slow 500 tracebacks per request.
    raise HTTPException(status_code=503)


@pytest.fixture
def limiter() -> Iterator[RateLimitMiddleware]:
    """Enable the limiter that the real app mounts, with a fresh counter store."""
    from src.api.main import app

    app.middleware_stack = app.build_middleware_stack()
    mounted = _mounted_rate_limiter(app)
    saved = (mounted.enabled, mounted.requests_per_minute, mounted.store)
    mounted.enabled = True
    mounted.requests_per_minute = LIMIT
    mounted.store = InMemoryRateLimitStore()
    app.dependency_overrides[get_session] = _no_database
    try:
        yield mounted
    finally:
        app.dependency_overrides.pop(get_session, None)
        mounted.enabled, mounted.requests_per_minute, mounted.store = saved


@pytest.mark.asyncio
async def test_rotating_x_api_key_does_not_evade_login_rate_limit(
    limiter: RateLimitMiddleware,
) -> None:
    from src.api.main import app

    statuses = []
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        for _ in range(LIMIT + 2):
            response = await client.post(
                LOGIN, json={}, headers={"X-API-Key": str(uuid.uuid4())}
            )
            statuses.append(response.status_code)

    # Requests under the limit reach the route (whatever it answers without a DB).
    assert 429 not in statuses[:LIMIT]
    assert statuses[LIMIT:] == [429, 429]


@pytest.mark.asyncio
async def test_each_429_increments_the_exposed_counter(
    limiter: RateLimitMiddleware,
) -> None:
    from prometheus_client import REGISTRY

    from src.api.main import app

    def rejected() -> float:
        return REGISTRY.get_sample_value("cerebro_rate_limit_rejections_total") or 0.0

    before = rejected()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        statuses = [
            (await client.post(LOGIN, json={})).status_code for _ in range(LIMIT + 2)
        ]

    assert statuses.count(429) == 2
    assert rejected() - before == 2
