"""Rate-limit and authentication rejections stay audited without hot-path writes."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from src.api.middleware.rate_limiter import InMemoryRateLimitStore, RateLimitMiddleware
from src.middleware.auth_middleware import AuthMiddleware
from src.models.db.audit_log import AuditEventType, AuditLog
from src.models.db.security_alert import AlertType, SecurityAlert
from src.security.audit_logger import AuditLogger


class _Session:
    def __init__(self, store: _Store) -> None:
        self.store = store
        self.pending: list[Any] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def add(self, row: Any) -> None:
        self.pending.append(row)

    async def commit(self) -> None:
        self.store.commits += 1
        self.store.rows.extend(self.pending)


class _Store:
    def __init__(self) -> None:
        self.rows: list[Any] = []
        self.commits = 0

    def __call__(self) -> _Session:
        return _Session(self)

    def of(self, kind: type) -> list[Any]:
        return [row for row in self.rows if isinstance(row, kind)]


def _app(store: _Store, *, limit: int = 2, auth: bool = False) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        store=InMemoryRateLimitStore(),
        requests_per_minute=limit,
        exclude_paths=(),
    )
    if auth:
        app.add_middleware(AuthMiddleware)
    app.state.audit_logger = AuditLogger(session_factory=store, buffer_size=100)

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _settle(app: FastAPI) -> None:
    logger: AuditLogger = app.state.audit_logger
    await logger._rejections.join()
    await logger.flush_buffer()


async def test_limiter_rejections_write_one_event_and_one_alert() -> None:
    store = _Store()
    app = _app(store)
    async with _client(app) as client:
        codes = [(await client.get("/ping")).status_code for _ in range(8)]
    assert codes.count(429) == 6
    # Nothing is written until the worker runs: the request path only queues.
    assert store.commits == 0
    assert app.state.audit_logger._rejections.qsize() == 1
    await app.state.audit_logger.start()
    try:
        await _settle(app)
    finally:
        await app.state.audit_logger.stop()

    events = store.of(AuditLog)
    assert [row.event_type for row in events] == [AuditEventType.RATE_LIMIT_EXCEEDED]
    alerts = store.of(SecurityAlert)
    assert [a.alert_type for a in alerts] == [AlertType.API_RATE_LIMIT]


async def test_unauthenticated_rejection_is_audited_but_never_inline() -> None:
    store = _Store()
    app = _app(store, auth=True)
    async with _client(app) as client:
        assert (await client.get("/ping")).status_code == 401
    assert store.commits == 0
    await app.state.audit_logger.start()
    try:
        await _settle(app)
    finally:
        await app.state.audit_logger.stop()

    assert [r.event_type for r in store.of(AuditLog)] == [
        AuditEventType.UNAUTHORIZED_ACCESS
    ]
    assert [a.alert_type for a in store.of(SecurityAlert)] == [
        AlertType.UNAUTHORIZED_API_ACCESS
    ]


async def test_a_full_queue_drops_events_instead_of_blocking_or_raising() -> None:
    store = _Store()
    logger = AuditLogger(session_factory=store, buffer_size=100)
    request = type("R", (), {})()  # never read: the worker is not running
    for i in range(logger._rejections.maxsize + 50):
        logger.record_rejection(
            AuditEventType.RATE_LIMIT_EXCEEDED, request, key=f"ip:{i}", action="x"
        )
    assert logger._rejections.qsize() == logger._rejections.maxsize


async def test_repeat_rejections_from_one_key_are_sampled() -> None:
    logger = AuditLogger(session_factory=_Store())
    request = type("R", (), {})()
    for _ in range(20):
        logger.record_rejection(
            AuditEventType.RATE_LIMIT_EXCEEDED, request, key="ip:1", action="x"
        )
    assert logger._rejections.qsize() == 1


async def test_failed_logins_alert_once_per_account_and_not_per_email() -> None:
    store = _Store()
    logger = AuditLogger(session_factory=store, buffer_size=100)
    account = uuid.uuid4()
    logger_counts = iter(range(100))

    async def count(identifier: str, event_type: AuditEventType, minutes: int = 15):
        # Prior LOGIN_FAILED rows for this account: one per earlier attempt.
        assert identifier == str(account)
        return next(logger_counts)

    logger._count_recent_events = count  # type: ignore[method-assign]
    for _ in range(8):
        await logger.log_event(
            event_type=AuditEventType.LOGIN_FAILED,
            action="POST /api/v1/auth/login",
            user_id=account,
            email="victim@example.com",
            result="failure",
        )

    alerts = store.of(SecurityAlert)
    assert len(alerts) == 1
    assert alerts[0].alert_type is AlertType.FAILED_LOGIN_ATTEMPTS
    assert alerts[0].user_id == account


@pytest.mark.parametrize("known", [True, False])
async def test_unknown_email_failures_alert_by_address_without_an_account(
    known: bool,
) -> None:
    store = _Store()
    logger = AuditLogger(session_factory=store, buffer_size=100)

    async def count(identifier: str, event_type: AuditEventType, minutes: int = 15):
        return 10

    logger._count_recent_events = count  # type: ignore[method-assign]
    await logger.log_event(
        event_type=AuditEventType.LOGIN_FAILED,
        action="POST /api/v1/auth/login",
        user_id=uuid.uuid4() if known else None,
        result="failure",
        ip_address="203.0.113.9",
    )
    assert len(store.of(SecurityAlert)) == 1
