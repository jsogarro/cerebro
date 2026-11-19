"""Routes authenticate on their own, with ``AuthMiddleware`` out of the picture.

The supervisor, TalkHier and MASR routers are mounted bare (no middleware) so
a 401 here can only come from the route's own auth dependency.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from src.api.routes import masr_api, supervisor_api, talkhier_api
from src.api.services.supervisor_coordination_service import (
    SupervisorCoordinationService,
    get_application_supervisor_coordination_service,
)
from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_jwt_service

_ROUTES = [
    (method, re.sub(r"\{[^}]+\}", "research", route.path))
    for module in (supervisor_api, talkhier_api, masr_api)
    for route in module.router.routes
    if isinstance(route, APIRoute)
    for method in sorted(route.methods)
]


class _JWT:
    async def validate_token(self, token: str) -> TokenPayload:
        now = datetime.now(UTC)
        return TokenPayload(
            sub="user-1",
            email="u@example.com",
            organization_id="11111111-1111-1111-1111-111111111111",
            jti="jti",
            iat=now,
            exp=now + timedelta(minutes=5),
        )


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    for module in (supervisor_api, talkhier_api, masr_api):
        app.include_router(module.router)
    app.dependency_overrides[get_jwt_service] = _JWT
    app.dependency_overrides[get_application_supervisor_coordination_service] = (
        SupervisorCoordinationService
    )
    yield TestClient(app)


@pytest.mark.parametrize(("method", "path"), _ROUTES)
def test_anonymous_caller_gets_401(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path, json={})

    assert response.status_code == 401, response.text


def test_authenticated_caller_is_admitted(client: TestClient) -> None:
    response = client.get("/api/v1/supervisors", headers={"Authorization": "Bearer t"})

    assert response.status_code == 200, response.text
