"""The maintenance factory is explicit, never a silent fallback in production."""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.config import settings
from src.models.db import session as db_session

SRC = Path(__file__).resolve().parents[1] / "src"
# Modules allowed to ask for the cross-tenant connection.
MAINTENANCE_USERS = {"src/api/main.py", "src/models/db/session.py"}


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    yield
    await db_session.close_db()


@pytest.mark.parametrize("environment", ["production", "test", "development"])
async def test_postgres_refuses_to_start_without_a_maintenance_url(
    monkeypatch, environment
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", environment)
    monkeypatch.setattr(settings, "MAINTENANCE_DATABASE_URL", None)
    # The connection probe is not under test; hand back a SQLite engine for it.
    monkeypatch.setattr(
        db_session,
        "create_engine",
        lambda *a, **k: create_async_engine("sqlite+aiosqlite://"),
    )
    with pytest.raises(RuntimeError, match="MAINTENANCE_DATABASE_URL"):
        await db_session.init_db("postgresql+asyncpg://u:p@h/d")
    with pytest.raises(RuntimeError, match="not initialized"):
        db_session.get_session_factory()


async def test_sqlite_falls_back_to_the_application_factory(monkeypatch) -> None:
    monkeypatch.setattr(settings, "MAINTENANCE_DATABASE_URL", None)
    await db_session.init_db("sqlite+aiosqlite://")
    assert (
        db_session.get_maintenance_session_factory() is db_session.get_session_factory()
    )


async def test_a_configured_url_gets_its_own_engine(monkeypatch) -> None:
    monkeypatch.setattr(settings, "MAINTENANCE_DATABASE_URL", "sqlite+aiosqlite://")
    await db_session.init_db("sqlite+aiosqlite://")
    assert (
        db_session.get_maintenance_session_factory()
        is not db_session.get_session_factory()
    )


def test_only_allowlisted_modules_use_the_maintenance_factory() -> None:
    users = {
        str(path.relative_to(SRC.parent))
        for path in SRC.rglob("*.py")
        if "get_maintenance_session_factory" in path.read_text()
    }
    assert users == MAINTENANCE_USERS
