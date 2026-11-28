"""A write is durable before its 2xx leaves the server, against real Postgres."""

import uuid
from collections.abc import MutableMapping
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.db import session as session_module
from src.models.db.session import SessionDep
from src.models.db.user import User

pytestmark = [pytest.mark.integration]


def _app() -> FastAPI:
    app = FastAPI()

    @app.post("/users")
    async def create(db: AsyncSession = SessionDep) -> dict[str, str]:
        tag = uuid.uuid4().hex
        db.add(
            User(
                id=uuid.uuid4(), email=f"{tag}@x.io", username=tag, hashed_password="x"
            )
        )
        return {"tag": tag}

    return app


async def _post(app: FastAPI, on_response_start: Any) -> list[MutableMapping[str, Any]]:
    """Drive the ASGI app by hand so a reader can act at the moment the 2xx is sent."""
    sent: list[MutableMapping[str, Any]] = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/users",
        "raw_path": b"/users",
        "query_string": b"",
        "headers": [],
        "server": ("test", 80),
        "client": ("test", 1),
        "scheme": "http",
        "root_path": "",
    }

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)
        if message["type"] == "http.response.start":
            await on_response_start(message["status"])

    await app(scope, receive, send)
    return sent


@pytest.fixture
def sessions(test_engine, monkeypatch) -> async_sessionmaker[AsyncSession]:
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "_async_session_factory", factory)
    return factory


async def _user_count(sessions: async_sessionmaker[AsyncSession]) -> int:
    async with sessions() as reader:
        return await reader.scalar(select(func.count()).select_from(User)) or 0


async def test_write_is_visible_to_another_reader_when_the_2xx_is_sent(sessions):
    seen: list[int] = []

    async def reader(status: int) -> None:
        assert status == 200
        seen.append(await _user_count(sessions))

    await _post(_app(), reader)

    assert seen == [1]


async def test_failed_commit_does_not_return_2xx(sessions, monkeypatch):
    async def boom(self: AsyncSession) -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(AsyncSession, "commit", boom)
    statuses: list[int] = []

    async def record(status: int) -> None:
        statuses.append(status)

    with pytest.raises(RuntimeError, match="commit failed"):
        await _post(_app(), record)

    assert statuses == [] or statuses[0] >= 500
    assert await _user_count(sessions) == 0


async def test_a_dependency_and_its_route_share_one_session(
    test_engine, monkeypatch
) -> None:
    opened = 0
    real = async_sessionmaker(test_engine, expire_on_commit=False)

    def counting() -> AsyncSession:
        nonlocal opened
        opened += 1
        return real()

    monkeypatch.setattr(session_module, "_async_session_factory", counting)
    app = FastAPI()
    seen: list[AsyncSession] = []

    async def tenant(db: AsyncSession = SessionDep) -> None:
        seen.append(db)

    @app.post("/users")
    async def create(
        _: None = Depends(tenant), db: AsyncSession = SessionDep
    ) -> dict[str, bool]:
        seen.append(db)
        return {}

    await _post(app, _ignore_status)

    assert opened == 1
    assert seen[0] is seen[1]


async def _ignore_status(_status: int) -> None:
    return None
