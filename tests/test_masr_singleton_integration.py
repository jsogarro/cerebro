"""FastAPI lifecycle ownership and active MASR consumer identity."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.api.main import app, lifespan


def test_lifespan_injects_one_router_across_active_consumers(
    monkeypatch,
) -> None:
    from src.api.services import direct_execution_service
    from src.api.services.event_publisher import event_publisher
    from src.api.websocket.connection_manager import websocket_manager
    from src.core.config import settings
    from src.models.db import session as db_session

    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
    monkeypatch.setattr(settings, "ADAPTIVE_ROUTING_ENABLED", False)
    monkeypatch.setattr(db_session, "init_db", AsyncMock())
    monkeypatch.setattr(event_publisher, "initialize", AsyncMock())
    monkeypatch.setattr(event_publisher, "shutdown", AsyncMock())
    monkeypatch.setattr(websocket_manager, "shutdown", AsyncMock())
    monkeypatch.setattr(direct_execution_service, "_direct_execution_service", None)

    with TestClient(app):
        from src.api.services.agent_execution_service import (
            get_application_agent_execution_service,
        )

        runtime = app.state.masr_runtime
        assert app.state.direct_execution_service.masr_router is runtime.router
        assert (
            app.state.research_kernel.executor.agent_backend
            is app.state.agent_execution_service
        )
        assert (
            app.state.research_kernel.registry
            is app.state.direct_execution_service.supervisor_registry
        )
        assert app.state.masr_routing_service.router is runtime.router
        assert app.state.talkhier_session_service.masr_router is runtime.router
        assert app.state.direct_execution_service.gemini_service is None
        request = AsyncMock()
        request.app = app
        assert (
            direct_execution_service.get_application_direct_execution_service(request)
            is app.state.direct_execution_service
        )
        assert (
            get_application_agent_execution_service(request)
            is app.state.agent_execution_service
        )

    assert runtime.closed is True
    assert not hasattr(app.state, "research_kernel")
    assert direct_execution_service._direct_execution_service is None


@pytest.mark.asyncio
async def test_overlapping_lifespans_close_only_their_owned_services(
    monkeypatch,
) -> None:
    from src.api.services.event_publisher import event_publisher
    from src.api.websocket.connection_manager import websocket_manager
    from src.core.config import settings
    from src.models.db import session as db_session

    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
    monkeypatch.setattr(settings, "ADAPTIVE_ROUTING_ENABLED", False)
    monkeypatch.setattr(settings, "LANGFUSE_ENABLED", False)
    monkeypatch.setattr(db_session, "init_db", AsyncMock())
    monkeypatch.setattr(event_publisher, "initialize", AsyncMock())
    monkeypatch.setattr(event_publisher, "shutdown", AsyncMock())
    monkeypatch.setattr(websocket_manager, "shutdown", AsyncMock())

    first_context = lifespan(app)
    second_context = lifespan(app)
    await first_context.__aenter__()
    first_runtime = app.state.masr_runtime
    first_direct = app.state.direct_execution_service
    first_agent = app.state.agent_execution_service
    first_kernel = app.state.research_kernel
    first_masr_service = app.state.masr_routing_service
    first_talkhier_service = app.state.talkhier_session_service

    await second_context.__aenter__()
    second_runtime = app.state.masr_runtime
    second_direct = app.state.direct_execution_service
    second_agent = app.state.agent_execution_service
    second_kernel = app.state.research_kernel
    second_masr_service = app.state.masr_routing_service
    second_talkhier_service = app.state.talkhier_session_service

    try:
        await first_context.__aexit__(None, None, None)

        assert first_runtime.closed is True
        assert first_direct.closed is True
        assert first_masr_service.closed is True
        assert first_talkhier_service.closed is True
        assert app.state.masr_runtime is second_runtime
        assert app.state.direct_execution_service is second_direct
        assert app.state.agent_execution_service is second_agent
        assert app.state.research_kernel is second_kernel
        assert first_kernel is not second_kernel
        assert first_agent is not second_agent
        assert app.state.masr_routing_service is second_masr_service
        assert app.state.talkhier_session_service is second_talkhier_service
        assert second_runtime.closed is False
        assert second_direct.closed is False
    finally:
        await second_context.__aexit__(None, None, None)

    assert second_runtime.closed is True
    assert second_direct.closed is True
    assert second_masr_service.closed is True
    assert second_talkhier_service.closed is True
    for state_name in (
        "masr_runtime",
        "direct_execution_service",
        "agent_execution_service",
        "research_kernel",
        "masr_routing_service",
        "talkhier_session_service",
    ):
        assert not hasattr(app.state, state_name)


@pytest.mark.asyncio
async def test_startup_failure_after_event_publisher_init_runs_shutdown(
    monkeypatch,
) -> None:
    from src.api.services import direct_execution_service
    from src.api.services.event_publisher import event_publisher
    from src.api.websocket.connection_manager import websocket_manager
    from src.core.config import settings
    from src.models.db import session as db_session

    monkeypatch.setattr(settings, "GEMINI_API_KEY", None)
    monkeypatch.setattr(settings, "ADAPTIVE_ROUTING_ENABLED", False)
    monkeypatch.setattr(settings, "LANGFUSE_ENABLED", False)
    monkeypatch.setattr(db_session, "init_db", AsyncMock())
    monkeypatch.setattr(event_publisher, "initialize", AsyncMock())
    monkeypatch.setattr(event_publisher, "shutdown", AsyncMock())
    monkeypatch.setattr(websocket_manager, "shutdown", AsyncMock())
    monkeypatch.setattr(
        direct_execution_service,
        "configure_direct_execution_service",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("forced startup failure")),
    )

    with pytest.raises(RuntimeError, match="forced startup failure"):
        async with lifespan(app):
            pytest.fail("lifespan should not yield after startup failure")

    event_publisher.initialize.assert_awaited_once()
    event_publisher.shutdown.assert_awaited_once()
