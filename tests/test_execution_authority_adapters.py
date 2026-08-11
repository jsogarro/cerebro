"""HTTP and CLI authority-reference adapter contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.ai_brain.router.masr import RoutingStrategy
from src.api.routes import agent_api, query_api
from src.api.services.direct_execution_service import DirectExecutionService
from src.api.services.execution_authority_resolver import (
    ExecutionAuthorityRequiredError,
    ExecutionAuthorityUnavailableError,
    MappingExecutionAuthorityResolver,
)
from src.api.services.research_kernel import compose_application_research_kernel
from src.cli.commands import agents
from src.cli.main import cli
from src.core.contracts import (
    CollaborationMode,
    ExecutionBudget,
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
    RoutingEdge,
    WorkerAssignment,
)
from src.core.kernel import TypedRegistry
from src.middleware.tenant_context import TenantContext, get_tenant_context
from src.models.execution_authority import (
    ExecutionAuthorityBinding,
    ExecutionAuthorityReference,
)

# The query routes require an authenticated tenant, and authority resolution
# is scoped to it. ``create_for_test`` bindings carry this tenant.
OWNING_ORGANIZATION = "tenant-1"


def _tenant_override() -> TenantContext:
    return TenantContext(user_id="user-1", organization_id=OWNING_ORGANIZATION)


class _RoutedBackend:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.start_research_execution = AsyncMock(side_effect=self._start)

    async def _start(
        self, project, context, *, authority_reference=None, organization_id=None
    ):
        if self.error:
            raise self.error
        assert authority_reference == ExecutionAuthorityReference(
            authority_id="authority-1", authority_version="1"
        )
        assert organization_id == OWNING_ORGANIZATION
        return "execution-1"

    async def get_execution_status(self, execution_id, *, organization_id=None):
        return SimpleNamespace(
            status="pending",
            routing_decision={},
            supervisor_type="research",
            agent_results={},
            quality_scores={},
            execution_time_seconds=0.0,
            started_at=datetime(2026, 7, 28, tzinfo=UTC),
        )

    async def get_execution_results(self, execution_id, *, organization_id=None):
        return None

    async def resume_execution(self, project_id):
        return None


class _AuthorityAllocation:
    supervisor_type = "research"
    worker_types = ["literature", "synthesis"]


class _AuthorityModel:
    provider = "gemini"
    model_name = "gemini-2.5-pro"


class _AuthorityOptimization:
    primary_model = _AuthorityModel()
    fallback_models: list[object] = []


class _AuthorityComplexity:
    domains = ["research"]


class _AuthorityDecision:
    routing_strategy = RoutingStrategy.BALANCED
    collaboration_mode = CollaborationMode.HIERARCHICAL
    agent_allocation = _AuthorityAllocation()
    optimization_result = _AuthorityOptimization()
    complexity_analysis = _AuthorityComplexity()


def _lifespan_binding() -> ExecutionAuthorityBinding:
    now = datetime(2026, 7, 28, tzinfo=UTC)
    return ExecutionAuthorityBinding.create_for_test(
        authority_id="authority-1",
        authority_version="1",
        run_id="run-1",
        workflow_definition_id="workflow-1",
        routing_policy_id="policy-1",
        strategy="balanced",
        collaboration_mode="hierarchical",
        domains=("research",),
        supervisor_id="supervisor-1",
        supervisor_type="research",
        workers=(
            WorkerAssignment(
                worker_id="worker-1",
                worker_type="literature",
                objective="Find sources",
                output_schema={},
                permission_scopes=(),
                tool_allowlist=(),
            ),
            WorkerAssignment(
                worker_id="worker-2",
                worker_type="synthesis",
                objective="Synthesize",
                output_schema={},
                permission_scopes=(),
                tool_allowlist=(),
            ),
        ),
        edges=(
            RoutingEdge(
                source_node_id="supervisor-1",
                target_node_id="worker-1",
                relation="delegates",
            ),
        ),
        provider_model_policy=ProviderModelPolicy(
            primary=ProviderModelRoute(provider="gemini", model="gemini-2.5-pro"),
            fallback_mode=FallbackMode.FAIL_CLOSED,
            fallbacks=(),
            provider_allowlist=("gemini",),
            model_allowlist=("gemini-2.5-pro",),
        ),
        budget=ExecutionBudget(
            max_cost_usd=0,
            max_total_tokens=1,
            max_tool_invocations=0,
            max_parallel_tasks=2,
            max_attempts_per_task=1,
            task_timeout_seconds=1,
        ),
        stop_conditions=("complete",),
        evaluator_requirements=(),
        deadline=now + timedelta(minutes=5),
        compiled_at=now,
    )


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ExecutionAuthorityRequiredError("required"), "EXECUTION_AUTHORITY_REQUIRED"),
        (
            ExecutionAuthorityUnavailableError("unavailable"),
            "EXECUTION_AUTHORITY_UNAVAILABLE",
        ),
    ],
)
def test_routed_query_authority_rejections_are_typed_422_before_execution(error, code):
    backend = _RoutedBackend(error)
    test_app = FastAPI()
    test_app.include_router(query_api.router)
    test_app.dependency_overrides[query_api.get_application_research_kernel] = lambda: (
        compose_application_research_kernel(backend)
    )
    test_app.dependency_overrides[get_tenant_context] = _tenant_override

    response = TestClient(test_app).post(
        "/api/v1/query/research",
        json={"query": "Test routed authority"},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": code}}
    assert backend.start_research_execution.await_count == 1


def test_routed_query_forwards_opaque_reference_and_preserves_response_shape():
    backend = _RoutedBackend()
    test_app = FastAPI()
    test_app.include_router(query_api.router)
    test_app.dependency_overrides[query_api.get_application_research_kernel] = lambda: (
        compose_application_research_kernel(backend)
    )
    test_app.dependency_overrides[get_tenant_context] = _tenant_override

    response = TestClient(test_app).post(
        "/api/v1/query/research",
        json={
            "query": "Test routed authority",
            "authority_reference": {
                "authority_id": "authority-1",
                "authority_version": "1",
            },
        },
    )

    assert response.status_code == 200
    assert set(response.json()) == set(query_api.IntelligentQueryResponse.model_fields)
    assert backend.start_research_execution.await_count == 1


def test_lifespan_uses_precomposed_authority_for_routed_query_once(
    monkeypatch: pytest.MonkeyPatch,
):
    """The real application lifespan retains explicitly composed authority."""
    from src.api import main
    from src.api.services import (
        agent_execution_service,
        component_catalog,
        direct_execution_service,
        masr_routing_service,
        supervisor_coordination_service,
        talkhier_session_service,
    )
    from src.models.db import session

    events: list[str] = []
    binding = _lifespan_binding()
    resolver = MappingExecutionAuthorityResolver({("authority-1", "1"): binding})
    resolve = resolver.resolve
    resolve_spy = Mock(
        side_effect=lambda reference, organization_id=None: (
            events.append("resolve")
            or resolve(reference, organization_id=organization_id)
        )
    )
    monkeypatch.setattr(resolver, "resolve", resolve_spy)
    router = SimpleNamespace(
        route=AsyncMock(
            side_effect=lambda **_: events.append("route") or _AuthorityDecision()
        )
    )
    dispatch = AsyncMock(side_effect=lambda *_: events.append("dispatch"))
    supervisor_bridge = AsyncMock()
    supervisor_bridge.admit_execution_plan = Mock()
    execution_service = DirectExecutionService(
        masr_router=router,
        supervisor_bridge=supervisor_bridge,
        supervisor_factory=Mock(),
    )
    execution_service._execute_research_workflow = dispatch
    original_compile = execution_service.execution_plan_compiler.compile
    compile_spy = Mock(
        side_effect=lambda proposal, authority: (
            events.append("compile") or original_compile(proposal, authority)
        )
    )
    monkeypatch.setattr(
        execution_service.execution_plan_compiler, "compile", compile_spy
    )

    class _Runtime:
        def __init__(self):
            self.router = router

        async def close(self) -> None:
            return None

    class _LifespanService:
        def __init__(self, **_kwargs) -> None:
            pass

        async def close(self) -> None:
            return None

    monkeypatch.setattr(main.MASRRuntime, "create", AsyncMock(return_value=_Runtime()))
    monkeypatch.setattr(main.settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(main.event_publisher, "initialize", AsyncMock())
    monkeypatch.setattr(main.event_publisher, "shutdown", AsyncMock())
    monkeypatch.setattr(session, "init_db", AsyncMock())
    monkeypatch.setattr(
        component_catalog, "build_application_component_registry", TypedRegistry
    )
    monkeypatch.setattr(
        direct_execution_service,
        "configure_direct_execution_service",
        lambda **_kwargs: execution_service,
    )
    monkeypatch.setattr(
        agent_execution_service,
        "configure_agent_execution_service",
        lambda **_kwargs: AsyncMock(),
    )
    monkeypatch.setattr(masr_routing_service, "MASRRoutingService", _LifespanService)
    monkeypatch.setattr(
        talkhier_session_service, "TalkHierSessionService", _LifespanService
    )
    monkeypatch.setattr(
        supervisor_coordination_service,
        "SupervisorCoordinationService",
        _LifespanService,
    )

    test_app = FastAPI(lifespan=main.lifespan)
    test_app.include_router(query_api.router)
    test_app.dependency_overrides[get_tenant_context] = _tenant_override
    test_app.state.execution_authority_resolver = resolver

    with TestClient(test_app) as client:
        response = client.post(
            "/api/v1/query/research",
            json={
                "query": "Test routed authority",
                "authority_reference": {
                    "authority_id": "authority-1",
                    "authority_version": "1",
                },
            },
        )

    assert response.status_code == 200
    assert set(response.json()) == set(query_api.IntelligentQueryResponse.model_fields)
    resolve_spy.assert_called_once()
    router.route.assert_awaited_once()
    compile_spy.assert_called_once()
    dispatch.assert_awaited_once()
    assert events == ["resolve", "route", "compile", "dispatch"]


@pytest.mark.parametrize(
    ("path", "payload", "method"),
    [
        (
            "/api/v1/agents/literature-review/execute",
            {"query": "test"},
            "execute_single_agent",
        ),
        (
            "/api/v1/agents/chain",
            {"query": "test", "agent_chain": ["literature-review", "synthesis"]},
            "execute_chain_of_agents",
        ),
        (
            "/api/v1/agents/mixture",
            {"query": "test", "agent_types": ["literature-review", "synthesis"]},
            "execute_mixture_of_agents",
        ),
    ],
)
def test_executing_agent_post_requires_authority_before_agent_service(
    path, payload, method
):
    service = AsyncMock()
    test_app = FastAPI()
    test_app.include_router(agent_api.router)
    test_app.dependency_overrides[agent_api.get_application_agent_research_kernel] = (
        lambda: compose_application_research_kernel(_RoutedBackend(), service)
    )

    response = TestClient(test_app).post(path, json=payload)

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "EXECUTION_AUTHORITY_REQUIRED"}}
    assert getattr(service, method).await_count == 0


@pytest.mark.parametrize(
    ("path", "payload", "code"),
    [
        (
            "/api/v1/agents/literature-review/execute",
            {"query": "test"},
            "EXECUTION_AUTHORITY_REQUIRED",
        ),
        (
            "/api/v1/agents/literature-review/execute",
            {
                "query": "test",
                "authority_reference": {
                    "authority_id": "authority-1",
                    "authority_version": "1",
                },
            },
            "EXECUTION_AUTHORITY_UNAVAILABLE",
        ),
    ],
)
def test_raw_agent_backend_override_fails_closed_before_agent_service(
    path, payload, code
):
    service = AsyncMock()
    test_app = FastAPI()
    test_app.include_router(agent_api.router)
    test_app.dependency_overrides[agent_api.get_application_agent_research_kernel] = (
        lambda: service
    )

    response = TestClient(test_app).post(path, json=payload)

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": code}}
    service.execute_single_agent.assert_not_awaited()


def test_agent_discovery_remains_available_without_authority():
    service = AsyncMock()
    service.get_agent_list.return_value = []
    service.get_service_stats.return_value = {"agent_metrics": {}}
    test_app = FastAPI()
    test_app.include_router(agent_api.router)
    test_app.dependency_overrides[agent_api.get_application_agent_research_kernel] = (
        lambda: compose_application_research_kernel(_RoutedBackend(), service)
    )

    response = TestClient(test_app).get("/api/v1/agents")

    assert response.status_code == 200


def test_cli_query_sends_only_opaque_authority_reference(monkeypatch):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, path, payload):
            calls.append((path, payload))
            return {"output": "ok"}

    monkeypatch.setattr(agents, "ResearchAPIClient", Client)
    result = CliRunner().invoke(
        cli,
        [
            "agents",
            "query",
            "test",
            "--authority-id",
            "authority-1",
            "--authority-version",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "/api/v1/query/research",
            {
                "query": "test",
                "authority_reference": {
                    "authority_id": "authority-1",
                    "authority_version": "1",
                },
            },
        )
    ]


def test_cli_displays_typed_authority_rejection(monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, path, payload):
            from src.cli.client import APIError

            raise APIError(422, {"code": "EXECUTION_AUTHORITY_REQUIRED"})

    monkeypatch.setattr(agents, "ResearchAPIClient", Client)
    result = CliRunner().invoke(cli, ["agents", "query", "test"])

    assert result.exit_code == 1
    assert "EXECUTION_AUTHORITY_REQUIRED" in result.output
