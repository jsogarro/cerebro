"""TOTP MFA against real Postgres: enrol, activate, login, replay, refresh, disable."""

import ast
import hashlib
import hmac
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pyotp
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport
from jose import jwt as jose_jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.api.auth import auth_router
from src.api.middleware.rate_limiter import InMemoryRateLimitStore
from src.auth.jwt_service import JWTService
from src.core.config import settings
from src.models.db.audit_log import AuditEventType
from src.models.db.session import get_session
from src.models.db.user import User
from src.security.audit_middleware import AuditTrailMiddleware
from src.security.login_throttle import LoginThrottle

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC = Path(sys.executable).parent / "alembic"
PASSWORD = "CorrectHorse!Battery9"
EMAIL = "mfa@example.com"
ENROLL = {"current_password": PASSWORD}
MAX_FAILURES = 4


class _FakeRedis:
    """The slice of Redis the JWT service uses."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def setex(self, key: str, _ttl: Any, value: Any) -> bool:
        self.data[key] = value
        return True

    async def set(self, key: str, value: Any) -> bool:
        self.data[key] = value
        return True

    async def get(self, key: str) -> Any:
        return self.data.get(key)

    async def getdel(self, key: str) -> Any:
        return self.data.pop(key, None)

    async def exists(self, key: str) -> int:
        return int(key in self.data)

    async def delete(self, key: str) -> int:
        return int(self.data.pop(key, None) is not None)

    async def scan(self, *_a: Any, **_k: Any) -> tuple[int, list[str]]:
        return 0, []


class _AuditSpy:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def log_event(self, **kwargs: Any) -> None:
        self.events.append(kwargs)

    async def flush_buffer(self) -> None: ...


@pytest.fixture(name="url", scope="module")
def url_fixture() -> Iterator[str]:
    with PostgresContainer(image="postgres:16-alpine") as server:
        host, port = server.get_container_host_ip(), server.get_exposed_port(5432)
        url = f"postgresql://{server.username}:{server.password}@{host}:{port}/{server.dbname}"
        result = subprocess.run(
            [str(ALEMBIC), "upgrade", "head"],
            cwd=REPO_ROOT,
            env={**os.environ, "DATABASE_URL": url},
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        yield url


@pytest_asyncio.fixture(name="sessions")
async def sessions_fixture(url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _cipher_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings, "DOCUMENT_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    monkeypatch.setattr(settings, "ENABLE_MFA", True)


@pytest_asyncio.fixture(name="user_id")
async def user_fixture(sessions: async_sessionmaker[AsyncSession]) -> uuid.UUID:
    uid = uuid.uuid4()
    async with sessions() as db:
        db.add(
            User.create_with_password(
                email=f"{uid}-{EMAIL}",
                username=str(uid),
                password=PASSWORD,
                id=uid,
                organization_id=uuid.uuid4(),
                is_active=True,
            )
        )
        await db.commit()
    return uid


@pytest.fixture(name="audit")
def audit_fixture() -> _AuditSpy:
    return _AuditSpy()


@pytest.fixture(name="app")
def app_fixture(
    sessions: async_sessionmaker[AsyncSession], audit: _AuditSpy
) -> FastAPI:
    app = FastAPI()
    app.middleware("http")(AuditTrailMiddleware())
    app.state.audit_logger = audit
    app.include_router(auth_router.router)
    jwt_service = JWTService(redis_client=_FakeRedis())  # type: ignore[arg-type]
    throttle = LoginThrottle(
        InMemoryRateLimitStore(), max_failures=MAX_FAILURES, lockout_seconds=60
    )

    async def session() -> AsyncIterator[AsyncSession]:
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[auth_router.get_jwt_service] = lambda: jwt_service
    app.dependency_overrides[auth_router.get_login_throttle] = lambda: throttle
    app.dependency_overrides[auth_router.get_password_service] = lambda: None
    app.state.jwt = jwt_service
    return app


class _Client:
    def __init__(self, app: FastAPI, email: str) -> None:
        self.app, self.email = app, email

    async def post(self, path: str, body: dict[str, Any], token: str | None = None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with httpx.AsyncClient(
            transport=ASGITransport(app=self.app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            return await client.post(path, json=body, headers=headers)

    async def login(self, code: str | None = None, password: str = PASSWORD):
        body: dict[str, Any] = {"email": self.email, "password": password}
        if code is not None:
            body["totp_code"] = code
        return await self.post("/auth/login", body)


@pytest_asyncio.fixture(name="client")
async def client_fixture(
    app: FastAPI, user_id: uuid.UUID, sessions: async_sessionmaker[AsyncSession]
) -> _Client:
    return _Client(app, f"{user_id}-{EMAIL}")


def _code(secret: str, steps_ahead: int = 0) -> str:
    return pyotp.TOTP(secret).at(time.time() + 30 * steps_ahead)


async def _enrol(client: _Client) -> tuple[str, str, list[str]]:
    """Enrol and activate; return (secret, access token, recovery codes)."""
    token = (await client.login()).json()["tokens"]["access_token"]
    enrolled = await client.post("/auth/mfa/enroll", ENROLL, token)
    assert enrolled.status_code == 200, enrolled.text
    secret = enrolled.json()["secret"]
    activated = await client.post("/auth/mfa/verify", {"code": _code(secret)}, token)
    assert activated.status_code == 200, activated.text
    codes = activated.json()["recovery_codes"]
    # Activation ends every session, this one included; sign in again.
    fresh = (await client.login(codes.pop())).json()["tokens"]["access_token"]
    return secret, fresh, codes


async def test_enrol_returns_a_provisioning_uri_and_stores_the_secret_encrypted(
    client: _Client,
    sessions: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
) -> None:
    token = (await client.login()).json()["tokens"]["access_token"]

    response = await client.post("/auth/mfa/enroll", ENROLL, token)

    body = response.json()
    assert response.status_code == 200
    assert body["provisioning_uri"].startswith("otpauth://totp/")
    assert body["secret"] in body["provisioning_uri"]
    async with sessions() as db:
        stored = (
            await db.execute(
                text("SELECT totp_secret_enc, totp_enabled FROM users WHERE id=:i"),
                {"i": user_id},
            )
        ).one()
    assert stored[0].startswith("fernet:") and body["secret"] not in stored[0]
    assert stored[1] is False


async def test_pending_enrolment_does_not_require_a_code_at_login(
    client: _Client,
) -> None:
    token = (await client.login()).json()["tokens"]["access_token"]
    await client.post("/auth/mfa/enroll", ENROLL, token)

    assert (await client.login()).status_code == 200


async def test_verify_activates_and_recovery_codes_are_hashed_at_rest(
    client: _Client,
    sessions: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
) -> None:
    _, _, codes = await _enrol(client)

    assert len(codes) == 9 and len(set(codes)) == 9
    async with sessions() as db:
        enabled = (
            await db.execute(
                text("SELECT totp_enabled FROM users WHERE id=:i"), {"i": user_id}
            )
        ).scalar_one()
        hashes = {
            r[0]
            for r in await db.execute(
                text("SELECT code_hash FROM mfa_recovery_codes WHERE user_id=:i"),
                {"i": user_id},
            )
        }
    assert enabled is True
    assert len(hashes) == 10 and hashes.isdisjoint(codes)


async def test_verify_with_a_wrong_code_does_not_activate(client: _Client) -> None:
    token = (await client.login()).json()["tokens"]["access_token"]
    await client.post("/auth/mfa/enroll", ENROLL, token)

    response = await client.post("/auth/mfa/verify", {"code": "000000"}, token)

    assert response.status_code == 401
    assert (await client.login()).status_code == 200  # still not enrolled


async def test_enrolled_login_without_a_code_is_401(client: _Client) -> None:
    await _enrol(client)

    assert (await client.login()).status_code == 401


async def test_enrolled_login_with_a_wrong_code_is_401(client: _Client) -> None:
    await _enrol(client)

    assert (await client.login("123456")).status_code == 401


async def test_enrolled_login_with_a_valid_code_passes(client: _Client) -> None:
    secret, _, _ = await _enrol(client)

    response = await client.login(_code(secret, steps_ahead=1))

    assert response.status_code == 200
    assert "access_token" in response.json()["tokens"]


async def test_wrong_password_with_a_valid_code_is_still_401(client: _Client) -> None:
    secret, _, _ = await _enrol(client)

    wrong = await client.login(_code(secret, steps_ahead=1), password="Wrong!Horse9xx")

    assert wrong.status_code == 401


async def test_a_replayed_code_is_rejected(client: _Client) -> None:
    secret, _, _ = await _enrol(client)
    code = _code(secret, steps_ahead=1)

    assert (await client.login(code)).status_code == 200
    assert (await client.login(code)).status_code == 401


async def test_the_activation_code_cannot_be_replayed_at_login(
    client: _Client,
) -> None:
    secret, _, _ = await _enrol(client)

    assert (await client.login(_code(secret))).status_code == 401


async def test_a_recovery_code_works_exactly_once(client: _Client) -> None:
    _, _, codes = await _enrol(client)

    assert (await client.login(codes[0])).status_code == 200
    assert (await client.login(codes[0])).status_code == 401
    assert (await client.login(codes[1])).status_code == 200


async def test_mfa_session_survives_refresh_and_old_sessions_do_not(
    client: _Client, app: FastAPI
) -> None:
    before = (await client.login()).json()["tokens"]  # password-only session
    secret, _, _ = await _enrol(client)

    stale = await client.post(
        "/auth/refresh", {"refresh_token": before["refresh_token"]}
    )
    assert stale.status_code == 401

    tokens = (await client.login(_code(secret, steps_ahead=1))).json()["tokens"]
    refreshed = await client.post(
        "/auth/refresh", {"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    claims = jose_jwt.get_unverified_claims(refreshed.json()["access_token"])
    assert claims["mfa"] is True
    again = await client.post(
        "/auth/refresh", {"refresh_token": refreshed.json()["refresh_token"]}
    )
    assert again.status_code == 200


async def test_disable_requires_a_valid_code(client: _Client) -> None:
    secret, token, _ = await _enrol(client)

    refused = await client.post("/auth/mfa/disable", {"code": "000000"}, token)
    assert refused.status_code == 401
    assert (await client.login()).status_code == 401  # still enrolled

    done = await client.post(
        "/auth/mfa/disable", {"code": _code(secret, steps_ahead=1)}, token
    )
    assert done.status_code == 204
    assert (await client.login()).status_code == 200


async def test_failed_codes_are_throttled_and_audited(
    client: _Client, audit: _AuditSpy, user_id: uuid.UUID
) -> None:
    await _enrol(client)
    audit.events.clear()

    statuses = [
        (await client.login("111111")).status_code for _ in range(MAX_FAILURES + 1)
    ]

    assert statuses[-1] == 429
    failed = [e for e in audit.events if e["event_type"] == AuditEventType.LOGIN_FAILED]
    assert failed and all(e["user_id"] == user_id for e in failed)


async def test_failed_activation_codes_are_throttled(client: _Client) -> None:
    token = (await client.login()).json()["tokens"]["access_token"]
    await client.post("/auth/mfa/enroll", ENROLL, token)

    statuses = [
        (await client.post("/auth/mfa/verify", {"code": "000000"}, token)).status_code
        for _ in range(MAX_FAILURES + 1)
    ]

    assert statuses[-1] == 429


async def test_enrol_needs_the_current_password(client: _Client) -> None:
    token = (await client.login()).json()["tokens"]["access_token"]

    assert (await client.post("/auth/mfa/enroll", {}, token)).status_code == 422
    wrong = await client.post(
        "/auth/mfa/enroll", {"current_password": "Wrong!Horse9xx"}, token
    )
    assert wrong.status_code == 401
    assert (await client.login()).status_code == 200  # owner not locked out


async def test_activation_ends_sessions_started_before_it(client: _Client) -> None:
    before = (await client.login()).json()["tokens"]["access_token"]
    await _enrol(client)

    assert (await client.post("/auth/mfa/enroll", ENROLL, before)).status_code == 401


async def test_disable_and_reenrol_kill_old_mfa_sessions(client: _Client) -> None:
    secret, token, codes = await _enrol(client)
    old = (await client.login(_code(secret, steps_ahead=1))).json()["tokens"]

    done = await client.post("/auth/mfa/disable", {"code": codes[0]}, token)
    assert done.status_code == 204
    assert (await client.post("/auth/mfa/enroll", ENROLL, token)).status_code == 401

    fresh = (await client.login()).json()["tokens"]["access_token"]
    again = (await client.post("/auth/mfa/enroll", ENROLL, fresh)).json()["secret"]
    await client.post("/auth/mfa/verify", {"code": _code(again)}, fresh)

    stale = await client.post("/auth/refresh", {"refresh_token": old["refresh_token"]})
    assert stale.status_code == 401


async def test_recovery_codes_are_long_and_peppered_at_rest(
    client: _Client,
    sessions: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
) -> None:
    _, _, codes = await _enrol(client)

    assert all(re.fullmatch(r"[0-9a-f]{5}(-[0-9a-f]{5}){3}", c) for c in codes)
    # All 80 bits random: the second half of each code differs between codes too.
    assert len({c[-11:] for c in codes}) == len(codes)
    async with sessions() as db:
        stored = {
            r[0]
            for r in await db.execute(
                text("SELECT code_hash FROM mfa_recovery_codes WHERE user_id=:i"),
                {"i": user_id},
            )
        }
    code = codes[0]
    plain = hashlib.sha256(code.replace("-", "").encode()).hexdigest()
    peppered = hmac.new(
        settings.SECRET_KEY.encode(),
        b"mfa-recovery:" + code.replace("-", "").encode(),
        hashlib.sha256,
    ).hexdigest()
    assert peppered in stored and plain not in stored


async def test_non_ascii_digits_are_a_failed_code_not_a_crash(
    client: _Client, audit: _AuditSpy, user_id: uuid.UUID
) -> None:
    await _enrol(client)
    audit.events.clear()

    response = await client.login("١٢٣٤٥٦")

    assert response.status_code == 401
    assert any(
        e["event_type"] == AuditEventType.LOGIN_FAILED and e["user_id"] == user_id
        for e in audit.events
    )


async def test_oversized_code_is_rejected_by_the_schema(client: _Client) -> None:
    assert (await client.login("1" * 33)).status_code == 422


@pytest.mark.parametrize("key", ["", Fernet.generate_key().decode()])
async def test_unusable_encryption_key_is_a_typed_audited_503(
    client: _Client,
    audit: _AuditSpy,
    user_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
) -> None:
    await _enrol(client)
    monkeypatch.setattr(settings, "DOCUMENT_ENCRYPTION_KEY", key)
    audit.events.clear()

    response = await client.login("123456")

    assert response.status_code == 503
    assert "MFA" in response.json()["detail"]
    assert any(e["user_id"] == user_id for e in audit.events)


async def test_flag_off_refuses_new_enrolment_but_never_weakens_enrolled_accounts(
    client: _Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = (await client.login()).json()["tokens"]["access_token"]
    pending = (await client.post("/auth/mfa/enroll", ENROLL, token)).json()["secret"]
    secret, token, codes = await _enrol(client)
    monkeypatch.setattr(settings, "ENABLE_MFA", False)

    assert (await client.post("/auth/mfa/enroll", ENROLL, token)).status_code == 403
    assert (
        await client.post("/auth/mfa/verify", {"code": "000000"}, token)
    ).status_code == 403
    assert (await client.login()).status_code == 401  # enforcement stays
    assert (await client.login(_code(secret, steps_ahead=1))).status_code == 200
    done = await client.post("/auth/mfa/disable", {"code": codes[0]}, token)
    assert done.status_code == 204
    assert pending  # enrolment above ran while the flag was on


def test_every_token_issuing_call_site_is_known() -> None:
    """A new route that mints tokens must be reviewed for the MFA check."""
    allowed = {
        ("api/auth/auth_router.py", "register"),  # a new account cannot be enrolled
        ("api/auth/auth_router.py", "login"),  # checks the second factor
        ("auth/jwt_service.py", "refresh_tokens"),  # keeps the session's mfa claim
    }
    found: set[tuple[str, str]] = set()
    src = REPO_ROOT / "src"
    for path in src.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "generate_token_pair"
                ):
                    found.add((path.relative_to(src).as_posix(), fn.name))
    assert found == allowed


def test_no_token_is_encoded_outside_the_jwt_service() -> None:
    """Minting through ``jwt.encode`` directly would skip the MFA claim and check."""
    offenders = []
    src = REPO_ROOT / "src"
    for path in src.rglob("*.py"):
        if path.relative_to(src).as_posix() == "auth/jwt_service.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "encode"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "jwt"
            ):
                offenders.append(f"{path.relative_to(src)}:{node.lineno}")
    assert not offenders
