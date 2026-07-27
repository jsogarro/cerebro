"""FastAPI lifecycle ownership and active MASR consumer identity."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
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


def test_mounted_supervisor_api_requires_lifespan_or_explicit_override() -> None:
    from src.api.routes import supervisor_api
    from src.api.services.supervisor_coordination_service import (
        get_application_supervisor_coordination_service,
    )

    test_app = FastAPI()
    test_app.include_router(supervisor_api.router)
    client = TestClient(test_app)

    unavailable = client.get("/api/v1/supervisors")

    assert unavailable.status_code == 503
    assert (
        unavailable.json()["detail"] == "Supervisor coordination runtime is unavailable"
    )

    service = Mock()
    service.get_all_supervisors = AsyncMock(return_value=[])
    test_app.dependency_overrides[get_application_supervisor_coordination_service] = (
        lambda: service
    )

    available = client.get("/api/v1/supervisors")

    assert available.status_code == 200
    assert available.json() == {
        "supervisors": [],
        "total_count": 0,
        "active_count": 0,
        "available_count": 0,
    }


@pytest.mark.asyncio
async def test_masr_routing_close_owns_only_internally_created_bridge() -> None:
    from src.ai_brain.integration.masr_supervisor_bridge import MASRSupervisorBridge
    from src.ai_brain.router.masr import MASRouter
    from src.api.services.component_catalog import (
        build_application_component_registry,
    )
    from src.api.services.masr_routing_service import MASRRoutingService

    registry = build_application_component_registry()
    owned = MASRRoutingService(
        router=MASRouter(config={"enable_caching": False}),
        component_registry=registry,
    )
    owned.bridge.cleanup = AsyncMock()
    injected_bridge = MASRSupervisorBridge(component_registry=registry)
    injected_bridge.cleanup = AsyncMock()
    injected = MASRRoutingService(
        router=MASRouter(config={"enable_caching": False}),
        component_registry=registry,
        bridge=injected_bridge,
    )

    await owned.close()
    await owned.close()
    await injected.close()
    await injected.close()

    owned.bridge.cleanup.assert_awaited_once()
    injected_bridge.cleanup.assert_not_awaited()


@pytest.mark.asyncio
async def test_talkhier_close_releases_unique_supervisors_and_owned_bridge() -> None:
    from src.api.services.talkhier_session_service import (
        TalkHierSession,
        TalkHierSessionService,
    )
    from src.models.talkhier_api_models import (
        ConsensusType,
        ProtocolType,
        RefinementStrategy,
        SessionStatus,
    )

    service = TalkHierSessionService()
    service.masr_bridge.cleanup = AsyncMock()
    shared = cast(Any, SimpleNamespace(close=AsyncMock()))
    failing = cast(
        Any,
        SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("close failed"))),
    )
    remaining = cast(Any, SimpleNamespace(close=AsyncMock()))

    def session(session_id: str, supervisor: Any) -> TalkHierSession:
        return TalkHierSession(
            session_id=session_id,
            query="query",
            domains=["research"],
            status=SessionStatus.ACTIVE,
            created_at=datetime.now(UTC),
            protocol_type=ProtocolType.STANDARD,
            refinement_strategy=RefinementStrategy.QUALITY_FOCUSED,
            max_rounds=3,
            min_rounds=1,
            quality_threshold=0.85,
            consensus_type=ConsensusType.WEIGHTED,
            consensus_threshold=0.8,
            timeout_seconds=300,
            participants=[],
            supervisor=supervisor,
        )

    first = session("first", shared)
    second = session("second", shared)
    third = session("third", failing)
    fourth = session("fourth", remaining)
    service.sessions.update(
        {
            first.session_id: first,
            second.session_id: second,
            third.session_id: third,
            fourth.session_id: fourth,
        }
    )

    await service.close()
    await service.close()

    shared.close.assert_awaited_once()
    failing.close.assert_awaited_once()
    remaining.close.assert_awaited_once()
    service.masr_bridge.cleanup.assert_awaited_once()
    assert all(session.supervisor is None for session in (first, second, third, fourth))
    assert service.sessions == {}
    assert service.closed is True


@pytest.mark.asyncio
async def test_talkhier_close_leaves_injected_bridge_caller_owned() -> None:
    from src.ai_brain.integration.masr_supervisor_bridge import MASRSupervisorBridge
    from src.api.services.component_catalog import (
        build_application_component_registry,
    )
    from src.api.services.talkhier_session_service import TalkHierSessionService

    registry = build_application_component_registry()
    bridge = MASRSupervisorBridge(component_registry=registry)
    bridge.cleanup = AsyncMock()
    service = TalkHierSessionService(
        component_registry=registry,
        masr_bridge=bridge,
    )

    await service.close()
    await service.close()

    bridge.cleanup.assert_not_awaited()


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
    first_supervisor_cleanup = AsyncMock()
    first_supervisor_coordination._get_real_executor().masr_bridge.cleanup = (
        first_supervisor_cleanup
    )

    await second_context.__aenter__()
    second_runtime = app.state.masr_runtime
    second_direct = app.state.direct_execution_service
    second_agent = app.state.agent_execution_service
    second_kernel = app.state.research_kernel
    second_masr_service = app.state.masr_routing_service
    second_talkhier_service = app.state.talkhier_session_service
    second_supervisor_coordination = app.state.supervisor_coordination_service
    second_supervisor_cleanup = AsyncMock()
    second_supervisor_coordination._get_real_executor().masr_bridge.cleanup = (
        second_supervisor_cleanup
    )

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
        first_supervisor_cleanup.assert_awaited_once()
        second_supervisor_cleanup.assert_not_awaited()
        assert second_runtime.closed is False
        assert second_direct.closed is False
    finally:
        await second_context.__aexit__(None, None, None)

    assert second_runtime.closed is True
    assert second_direct.closed is True
    assert second_masr_service.closed is True
    assert second_talkhier_service.closed is True
    second_supervisor_cleanup.assert_awaited_once()
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
async def test_supervisor_coordination_close_is_safe_before_initialization() -> None:
    from src.api.services.supervisor_coordination_service import (
        SupervisorCoordinationService,
    )

    service = SupervisorCoordinationService()

    await service.close()
    await service.close()

    assert service._real_executor is None


@pytest.mark.asyncio
async def test_supervisor_coordination_close_cleans_initialized_bridge_once() -> None:
    from src.api.services.supervisor_coordination_service import (
        SupervisorCoordinationService,
    )

    service = SupervisorCoordinationService()
    executor = service._get_real_executor()
    executor.masr_bridge.cleanup = AsyncMock()

    await service.close()
    await service.close()

    executor.masr_bridge.cleanup.assert_awaited_once()
    assert service._real_executor is None


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
