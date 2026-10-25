"""One JWTService + Redis client per process, shared by every auth path."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.auth import jwt_service as jwt_module
from src.middleware import auth_middleware


class _FakeRedis:
    """Permissive Redis stand-in: every command succeeds and reads miss."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    def __getattr__(self, name: str) -> Any:
        async def _command(*_args: Any, **_kwargs: Any) -> int | None:
            return 0 if name == "exists" else None

        return _command


@pytest.fixture
def constructions(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Count JWTService constructions and Redis clients built via from_url."""
    seen: dict[str, Any] = {"service": 0, "redis": []}
    original_init = jwt_module.JWTService.__init__

    def counting_init(self: Any, *args: Any, **kwargs: Any) -> None:
        seen["service"] += 1
        original_init(self, *args, **kwargs)

    async def fake_from_url(*_args: Any, **_kwargs: Any) -> _FakeRedis:
        client = _FakeRedis()
        seen["redis"].append(client)
        return client

    monkeypatch.setattr(jwt_module.JWTService, "__init__", counting_init)
    monkeypatch.setattr(auth_middleware.redis, "from_url", fake_from_url)
    monkeypatch.setattr(auth_middleware, "_shared_jwt_service", None, raising=False)
    monkeypatch.setattr(auth_middleware.settings, "JWT_PRIVATE_KEY_PATH", None)
    monkeypatch.setattr(auth_middleware.settings, "JWT_PUBLIC_KEY_PATH", None)
    return seen


async def _mint_token() -> str:
    service = await auth_middleware.get_jwt_service()
    pair = await service.generate_token_pair(user_id="user-1", email="u@example.com")
    return pair.access_token


def test_authenticated_requests_share_one_service_and_client(
    constructions: dict[str, Any],
) -> None:
    import asyncio

    token = asyncio.run(_mint_token())
    headers = {"Authorization": f"Bearer {token}"}
    client = TestClient(app)  # no lifespan: exercises the lazy fallback
    if True:
        for _ in range(2):
            response = client.get("/api/v1/no-such-route", headers=headers)
            # 404 = the middleware accepted the token; 401 would be rejected.
            assert response.status_code == 404
    assert constructions["service"] == 1
    assert len(constructions["redis"]) == 1


def test_shutdown_closes_the_shared_redis_client(
    constructions: dict[str, Any],
) -> None:
    with TestClient(app) as client:
        client.get("/api/v1/no-such-route")  # anonymous, 401, no service needed
        import asyncio

        asyncio.run(_mint_token())
    # lifespan exit closed the client built during the run
    assert constructions["redis"]
    assert all(c.closed for c in constructions["redis"])
