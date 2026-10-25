"""Unauthenticated rate limiting keys on the client, not on caller-chosen headers."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import httpx
import pytest
from httpx import ASGITransport

from src.api.middleware.rate_limiter import InMemoryRateLimitStore, RateLimitMiddleware

LOGIN = "/api/v1/auth/login"
LIMIT = 3


def _mounted_rate_limiter(app: object) -> RateLimitMiddleware:
    layer = getattr(app, "middleware_stack", None)
    while layer is not None and not isinstance(layer, RateLimitMiddleware):
        layer = getattr(layer, "app", None)
    assert layer is not None, "RateLimitMiddleware is not mounted on the app"
    return layer


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
    try:
        yield mounted
    finally:
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
