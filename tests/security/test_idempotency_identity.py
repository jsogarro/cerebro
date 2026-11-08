"""Idempotency keys are scoped to the caller and reserved before execution."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport

from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_jwt_service

PATH = "/api/v1/query/idempotency-probe"
ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"
IDENTITIES = {"token-a": ("user-a", ORG_A), "token-b": ("user-b", ORG_B)}


class _JWTService:
    async def validate_token(self, token: str) -> TokenPayload:
        user_id, org_id = IDENTITIES[token]
        now = datetime.now(UTC)
        return TokenPayload(
            sub=user_id,
            email=f"{user_id}@example.com",
            organization_id=org_id,
            jti=f"jti-{user_id}",
            iat=now,
            exp=now + timedelta(minutes=5),
        )


@pytest_asyncio.fixture
async def probe() -> AsyncIterator[dict[str, object]]:
    """Mount a counting route on the real app, behind its real middleware."""
    from src.api.main import app

    state: dict[str, object] = {
        "calls": 0,
        "gate": asyncio.Event(),
        "fail": False,
    }

    @app.post(PATH)
    async def _probe() -> dict[str, int]:
        state["calls"] = int(state["calls"]) + 1  # type: ignore[call-overload]
        await state["gate"].wait()  # type: ignore[attr-defined]
        if state["fail"]:
            raise HTTPException(status_code=500, detail="boom")
        return {"call": int(state["calls"])}  # type: ignore[call-overload]

    route = app.router.routes[-1]
    app.dependency_overrides[get_jwt_service] = _JWTService
    state["gate"].set()  # type: ignore[attr-defined]
    try:
        yield state
    finally:
        app.router.routes.remove(route)
        app.dependency_overrides.pop(get_jwt_service, None)


def _client() -> httpx.AsyncClient:
    from src.api.main import app

    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _headers(token: str, key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": key}


@pytest.mark.asyncio
async def test_other_caller_does_not_receive_cached_response(
    probe: dict[str, object],
) -> None:
    key = str(uuid.uuid4())
    async with _client() as client:
        first = await client.post(PATH, json={"q": 1}, headers=_headers("token-a", key))
        second = await client.post(
            PATH, json={"q": 1}, headers=_headers("token-b", key)
        )
        replay = await client.post(
            PATH, json={"q": 1}, headers=_headers("token-a", key)
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert probe["calls"] == 2  # B executed its own request
    assert second.json() == {"call": 2}
    assert replay.json() == first.json()  # A's own replay is still served
    assert probe["calls"] == 2


@pytest.mark.asyncio
async def test_concurrent_duplicate_is_rejected_while_first_is_in_flight(
    probe: dict[str, object],
) -> None:
    key = str(uuid.uuid4())
    gate: asyncio.Event = probe["gate"]  # type: ignore[assignment]
    gate.clear()
    async with _client() as client:
        first = asyncio.create_task(
            client.post(PATH, json={"q": 1}, headers=_headers("token-a", key))
        )
        while probe["calls"] == 0:
            await asyncio.sleep(0.01)
        try:
            duplicate = await asyncio.wait_for(
                client.post(PATH, json={"q": 1}, headers=_headers("token-a", key)),
                timeout=2,
            )
        finally:
            gate.set()
        first_response = await first

    assert duplicate.status_code == 409
    assert first_response.status_code == 200
    assert probe["calls"] == 1


@pytest.mark.asyncio
async def test_reservation_is_released_when_handler_fails(
    probe: dict[str, object],
) -> None:
    key = str(uuid.uuid4())
    probe["fail"] = True
    async with _client() as client:
        failed = await client.post(
            PATH, json={"q": 1}, headers=_headers("token-a", key)
        )
        probe["fail"] = False
        retry = await client.post(PATH, json={"q": 1}, headers=_headers("token-a", key))

    assert failed.status_code == 500
    assert retry.status_code == 200
    assert probe["calls"] == 2
