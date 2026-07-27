"""FastAPI lifecycle ownership and active MASR consumer identity."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

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

    with TestClient(app) as client:
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
        component_registry = app.state.research_kernel.registry
        assert (
            app.state.direct_execution_service.component_registry is component_registry
        )
        assert (
            app.state.agent_execution_service.component_registry is component_registry
        )
        assert app.state.masr_routing_service.router is runtime.router
        assert app.state.masr_routing_service.component_registry is component_registry
        assert (
            app.state.masr_routing_service.bridge.component_registry
            is component_registry
        )
        assert (
            app.state.masr_routing_service.bridge.translator.component_registry
            is component_registry
        )
        assert (
            app.state.masr_routing_service.bridge.resource_pool.component_registry
            is component_registry
        )
        assert app.state.talkhier_session_service.masr_router is runtime.router
        assert (
            app.state.talkhier_session_service.component_registry is component_registry
        )
        assert (
            app.state.talkhier_session_service.supervisor_factory.component_registry
            is component_registry
        )
        assert (
            app.state.talkhier_session_service.masr_bridge.component_registry
            is component_registry
        )
        assert (
            app.state.talkhier_session_service.masr_bridge.translator.component_registry
            is component_registry
        )
        assert (
            app.state.talkhier_session_service.session_coordinator.component_registry
            is component_registry
        )
        assert app.state.supervisor_coordination_service.component_registry is (
            component_registry
        )
        real_executor = app.state.supervisor_coordination_service._get_real_executor()
        assert real_executor.masr_bridge.component_registry is component_registry
        assert (
            real_executor.masr_bridge.translator.component_registry
            is component_registry
        )
        routing_decision = Mock(
            agent_allocation=Mock(
                supervisor_type="research",
                worker_types=["literature_review"],
                retry_attempts=1,
                timeout_seconds=30,
                worker_count=1,
            ),
            complexity_analysis=Mock(domains=[]),
            estimated_quality=0.85,
        )
        created_supervisor = client.portal.call(
            app.state.talkhier_session_service.session_coordinator.create_supervisor,
            routing_decision,
            "research",
        )
        assert created_supervisor.component_registry is component_registry
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


def test_registry_aware_services_reject_mismatched_components() -> None:
    from src.agents.supervisors.supervisor_factory import SupervisorFactory
    from src.ai_brain.integration.masr_supervisor_bridge import MASRSupervisorBridge
    from src.ai_brain.router.masr import MASRouter
    from src.api.services.component_catalog import (
        build_application_component_registry,
    )
    from src.api.services.masr_routing_service import MASRRoutingService
    from src.api.services.talkhier_session_service import TalkHierSessionService

    expected = build_application_component_registry()
    other = build_application_component_registry()

    with pytest.raises(ValueError, match="MASR routing bridge registry mismatch"):
        MASRRoutingService(
            router=MASRouter(config={"enable_caching": False}),
            component_registry=expected,
            bridge=MASRSupervisorBridge(component_registry=other),
        )

    with pytest.raises(
        ValueError, match="TalkHier supervisor factory registry mismatch"
    ):
        TalkHierSessionService(
            component_registry=expected,
            supervisor_factory=SupervisorFactory(component_registry=other),
        )


def test_mounted_supervisor_api_declares_lifespan_service_dependency() -> None:
    from fastapi.routing import APIRoute

    from src.api.routes.supervisor_api import (
        get_application_supervisor_coordination_service,
    )

    mounted_route = next(
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path == "/api/v1/supervisors/{supervisor_type}/execute"
    )

    assert any(
        dependency.call is get_application_supervisor_coordination_service
        for dependency in mounted_route.dependant.dependencies
    )


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
    first_supervisor_coordination = app.state.supervisor_coordination_service

    await second_context.__aenter__()
    second_runtime = app.state.masr_runtime
    second_direct = app.state.direct_execution_service
    second_agent = app.state.agent_execution_service
    second_kernel = app.state.research_kernel
    second_masr_service = app.state.masr_routing_service
    second_talkhier_service = app.state.talkhier_session_service
    second_supervisor_coordination = app.state.supervisor_coordination_service

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
        assert (
            app.state.supervisor_coordination_service is second_supervisor_coordination
        )
        assert first_supervisor_coordination is not second_supervisor_coordination
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
        "supervisor_coordination_service",
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
