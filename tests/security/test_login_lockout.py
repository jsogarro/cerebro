"""Per-account failed-login lockout without account enumeration (issue #155)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from src.api.auth import auth_router
from src.api.middleware.rate_limiter import InMemoryRateLimitStore
from src.auth.models import TokenPair
from src.models.db.session import get_session
from src.security.login_throttle import LoginThrottle

PASSWORD = "CorrectHorse!Battery9"
WRONG = "WrongHorse!Battery9x"
MAX = 3
LOGIN = "/auth/login"


class _User:
    is_active = True
    is_verified = True
    is_superuser = False
    id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    email = "real@example.com"
    username = "real"
    full_name = None
    organization = None
    role = "user"
    created_at = updated_at = last_login = datetime.now(UTC)

    def verify_password(self, password: str) -> bool:
        return password == PASSWORD

    def record_login(self) -> None:
        return None


class _Db:
    async def commit(self) -> None: ...

    async def refresh(self, _row: Any) -> None: ...


class _Jwt:
    async def generate_token_pair(self, **_kw: Any) -> Any:
        return TokenPair(access_token="a", refresh_token="r", expires_in=60)


@pytest.fixture
def throttle() -> LoginThrottle:
    return LoginThrottle(InMemoryRateLimitStore(), max_failures=MAX, lockout_seconds=60)


@pytest.fixture
def app(throttle: LoginThrottle, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    app = FastAPI()
    app.include_router(auth_router.router)

    async def db() -> AsyncIterator[_Db]:
        yield _Db()

    async def get_user(_self: Any, email: str, **_kw: Any) -> Any:
        return _User() if email.lower() == "real@example.com" else None

    monkeypatch.setattr(auth_router.UserRepository, "get_by_email", get_user)
    monkeypatch.setattr(
        auth_router,
        "provisioned_organization_id",
        lambda _u: "00000000-0000-0000-0000-0000000000aa",
    )
    app.dependency_overrides[get_session] = db
    app.dependency_overrides[auth_router.get_password_service] = lambda: None
    app.dependency_overrides[auth_router.get_jwt_service] = lambda: _Jwt()
    app.dependency_overrides[auth_router.get_login_throttle] = lambda: throttle
    yield app


async def _login(app: FastAPI, email: str, password: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        return await client.post(LOGIN, json={"email": email, "password": password})


async def _fail(app: FastAPI, email: str, times: int) -> list[httpx.Response]:
    return [await _login(app, email, WRONG) for _ in range(times)]


@pytest.mark.asyncio
async def test_failures_below_the_limit_stay_401(app: FastAPI) -> None:
    responses = await _fail(app, "real@example.com", MAX)
    assert [r.status_code for r in responses] == [401] * MAX


@pytest.mark.asyncio
async def test_lockout_after_n_failures_blocks_even_the_right_password(
    app: FastAPI,
) -> None:
    await _fail(app, "real@example.com", MAX)

    locked = await _login(app, "real@example.com", PASSWORD)

    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_lockout_is_identical_for_existing_and_unknown_accounts(
    app: FastAPI,
) -> None:
    """Same status and body before and after lockout: no enumeration oracle."""
    pairs = []
    for email in ("real@example.com", "ghost@example.com"):
        before = await _fail(app, email, MAX)
        after = await _login(app, email, WRONG)
        pairs.append(([(r.status_code, r.json()) for r in before], after))

    assert pairs[0][0] == pairs[1][0]
    assert pairs[0][1].status_code == pairs[1][1].status_code == 429
    assert pairs[0][1].json() == pairs[1][1].json()


@pytest.mark.asyncio
async def test_identifier_is_normalised(app: FastAPI) -> None:
    await _fail(app, "real@example.com", MAX - 1)
    await _login(app, "  REAL@Example.com ", WRONG)

    assert (await _login(app, "real@example.com", PASSWORD)).status_code == 429


@pytest.mark.asyncio
async def test_other_accounts_are_unaffected(app: FastAPI) -> None:
    await _fail(app, "ghost@example.com", MAX)

    assert (await _login(app, "real@example.com", PASSWORD)).status_code != 429


@pytest.mark.asyncio
async def test_locked_attempts_do_not_extend_the_lockout(
    app: FastAPI, throttle: LoginThrottle
) -> None:
    await _fail(app, "real@example.com", MAX)
    first = await throttle.begin("real@example.com")
    await _fail(app, "real@example.com", 5)

    assert 0 < await throttle.begin("real@example.com") <= first


@pytest.mark.asyncio
async def test_concurrent_guesses_cannot_outrun_the_limit(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attempts are counted before verification, so a burst cannot all pass."""
    verified = 0

    async def slow_lookup(_self: Any, email: str, **_kw: Any) -> Any:
        nonlocal verified
        verified += 1
        await asyncio.sleep(0.05)
        return None

    monkeypatch.setattr(auth_router.UserRepository, "get_by_email", slow_lookup)

    responses = await asyncio.gather(
        *[_login(app, "real@example.com", WRONG) for _ in range(20)]
    )

    assert verified == MAX
    assert sorted(r.status_code for r in responses).count(429) == 20 - MAX


@pytest.mark.asyncio
async def test_success_clears_the_failure_count(app: FastAPI) -> None:
    await _fail(app, "real@example.com", MAX - 1)
    assert (await _login(app, "real@example.com", PASSWORD)).status_code == 200

    # A fresh budget: MAX-1 more failures still do not lock.
    await _fail(app, "real@example.com", MAX - 1)
    assert (await _login(app, "real@example.com", PASSWORD)).status_code == 200


@pytest.mark.asyncio
async def test_window_expiry_unlocks() -> None:
    now = [1000.0]
    throttle = LoginThrottle(
        InMemoryRateLimitStore(now=lambda: now[0]), max_failures=2, lockout_seconds=60
    )
    assert await throttle.begin("a@example.com") == 0
    assert await throttle.begin("a@example.com") == 0
    assert await throttle.begin("a@example.com") > 0

    now[0] += 61
    assert await throttle.begin("a@example.com") == 0


class _FlakyRedis:
    """Redis client whose first EXPIRE is lost."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.expire_calls = 0

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        self.ttls.setdefault(key, -1)
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.expire_calls += 1
        if self.expire_calls > 1:
            self.ttls[key] = seconds

    async def ttl(self, key: str) -> int:
        return self.ttls[key]


@pytest.mark.asyncio
async def test_lost_expire_does_not_lock_an_account_forever() -> None:
    from src.api.middleware.rate_limiter import RedisRateLimitStore

    store = RedisRateLimitStore("redis://unused")
    redis_client = _FlakyRedis()
    store._client = redis_client  # type: ignore[assignment]

    await store.increment("k", 60)  # first EXPIRE is lost
    await store.increment("k", 60)

    assert redis_client.ttls["rate_limit:k"] == 60
