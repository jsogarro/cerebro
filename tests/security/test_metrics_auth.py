"""``/metrics`` is readable only with the operational metrics credential."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.auth.jwt_service import JWTService
from src.auth.models import PAT_PREFIX
from src.core.config import settings
from src.middleware.auth_middleware import METRICS_AUDIENCE, get_jwt_service
from tests.security.test_route_authentication_boundary import _override_audit_logger

REPO_ROOT = Path(__file__).resolve().parents[2]


class _EmptyRedis:
    """Revocation store holding nothing, so only the claims decide."""

    async def exists(self, key: str) -> int:
        return 0

    async def get(self, key: str) -> None:
        return None

    async def setex(self, *args: object) -> bool:
        return True


@pytest.fixture(scope="module")
def jwt_service() -> JWTService:
    return JWTService(redis_client=_EmptyRedis())  # type: ignore[arg-type]


@pytest.fixture
def client(jwt_service: JWTService) -> Iterator[TestClient]:
    from src.api.main import app

    app.dependency_overrides[get_jwt_service] = lambda: jwt_service
    try:
        with _override_audit_logger(app):
            yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_jwt_service, None)


async def _token(service: JWTService, **kwargs: object) -> str:
    pair = await service.generate_token_pair(
        user_id="svc",
        email="svc@example.com",
        **kwargs,  # type: ignore[arg-type]
    )
    return pair.access_token


def _get(client: TestClient, token: str | None) -> int:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get("/metrics/", headers=headers).status_code


def test_anonymous_is_denied(client: TestClient) -> None:
    assert _get(client, None) == 401


@pytest.mark.asyncio
async def test_ordinary_tenant_token_is_denied(
    client: TestClient, jwt_service: JWTService
) -> None:
    token = await _token(
        jwt_service, permissions=["metrics:read"], organization_id="org-1"
    )
    assert _get(client, token) == 401


@pytest.mark.asyncio
async def test_wrong_audience_token_is_denied(
    client: TestClient, jwt_service: JWTService
) -> None:
    token = await _token(
        jwt_service,
        permissions=["metrics:read"],
        additional_claims={"aud": "someone-else"},
    )
    assert _get(client, token) == 401


@pytest.mark.asyncio
async def test_right_audience_without_permission_is_denied(
    client: TestClient, jwt_service: JWTService
) -> None:
    token = await _token(jwt_service, additional_claims={"aud": METRICS_AUDIENCE})
    assert _get(client, token) == 403


@pytest.mark.asyncio
async def test_operational_credential_reads_metrics(
    client: TestClient, jwt_service: JWTService
) -> None:
    token = await _token(
        jwt_service,
        permissions=["metrics:read"],
        additional_claims={"aud": METRICS_AUDIENCE},
    )
    response = client.get("/metrics/", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert "python_info" in response.text


@pytest.mark.asyncio
async def test_metrics_credential_is_not_an_api_credential(
    client: TestClient, jwt_service: JWTService
) -> None:
    token = await _token(
        jwt_service,
        permissions=["metrics:read"],
        additional_claims={"aud": METRICS_AUDIENCE},
    )
    response = client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


@pytest.mark.parametrize(("flag", "mounted"), [("false", False), ("true", True)])
def test_enable_metrics_controls_the_mount(flag: str, mounted: bool) -> None:
    code = (
        "from src.api.main import app;"
        "print('MOUNTED' if any(getattr(r, 'path', '') == '/metrics' for r in app.routes) else 'ABSENT')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env={**os.environ, "ENABLE_METRICS": flag, "PYTHONPATH": str(REPO_ROOT)},
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == (
        "MOUNTED" if mounted else "ABSENT"
    )


class _Shifted(datetime):
    offset = timedelta(0)

    @classmethod
    def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
        return datetime.now(tz) + cls.offset


@pytest.fixture
def shift_clock(monkeypatch: pytest.MonkeyPatch) -> Any:
    def shift(delta: timedelta) -> None:
        # Patched only once a token exists: jose's encoder isinstance-checks it.
        monkeypatch.setattr("jose.jwt.datetime", _Shifted)
        _Shifted.offset = delta

    yield shift
    _Shifted.offset = timedelta(0)


@pytest.mark.asyncio
async def test_minted_metrics_token_outlives_access_tokens_until_its_own_exp(
    client: TestClient,
    jwt_service: JWTService,
    shift_clock: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "METRICS_TOKEN_EXPIRE_DAYS", 30)
    token = jwt_service.generate_metrics_token()

    shift_clock(timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES + 1))
    assert _get(client, token) == 200

    shift_clock(timedelta(days=29))
    assert _get(client, token) == 200

    shift_clock(timedelta(days=31))
    assert _get(client, token) == 401


@pytest.mark.asyncio
async def test_minted_metrics_token_is_refused_on_the_api(
    client: TestClient, jwt_service: JWTService
) -> None:
    token = jwt_service.generate_metrics_token()
    response = client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


def test_a_personal_access_token_is_denied(client: TestClient) -> None:
    assert _get(client, f"{PAT_PREFIX}anything") == 401
