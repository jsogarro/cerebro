"""The app lifespan closes the database engine it opened."""

from fastapi.testclient import TestClient

from src.api.main import app
from src.models.db import session as db_session


def test_shutdown_disposes_the_engine_the_lifespan_opened() -> None:
    assert db_session._engine is None
    with TestClient(app):
        assert db_session._engine is not None
    # A pooled connection outliving its event loop breaks the next startup.
    assert db_session._engine is None


def test_shutdown_leaves_an_engine_it_did_not_open() -> None:
    import asyncio

    asyncio.run(db_session.init_db())
    engine = db_session._engine
    try:
        with TestClient(app):
            pass
        assert db_session._engine is engine
    finally:
        asyncio.run(db_session.close_db())


def test_a_hung_resource_close_does_not_block_shutdown(monkeypatch) -> None:
    import asyncio

    from src.api import main

    monkeypatch.setattr(main, "_RESOURCE_CLOSE_TIMEOUT_SECONDS", 0.05)
    warnings: list[dict] = []
    monkeypatch.setattr(
        main.logger,
        "warning",
        lambda event, **kw: warnings.append({"event": event, **kw}),
    )

    async def never_returns() -> None:
        await asyncio.Event().wait()

    asyncio.run(main._close_lifespan_resource("stuck", never_returns))

    assert warnings == [
        {"event": "lifespan_resource_shutdown_timed_out", "resource": "stuck"}
    ]
