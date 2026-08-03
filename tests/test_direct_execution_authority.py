"""Pre-dispatch authority enforcement for direct execution."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest

from src.ai_brain.router.execution_plan_compiler import ExecutionPlanCompiler
from src.ai_brain.router.routing_types import RoutingStrategy
from src.api.services.direct_execution_service import DirectExecutionService
from src.api.services.execution_authority_resolver import (
    ExecutionAuthorityRequiredError,
    ExecutionAuthorityUnavailableError,
    MappingExecutionAuthorityResolver,
)
from src.core.contracts import (
    CollaborationMode,
    ExecutionBudget,
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
    RoutingEdge,
    WorkerAssignment,
)
from src.models.execution_authority import (
    ExecutionAuthorityBinding,
    ExecutionAuthorityReference,
)
from src.models.research_project import (
    ResearchDepth,
    ResearchProject,
    ResearchQuery,
    ResearchScope,
)


class _Allocation:
    supervisor_type = "research"
    worker_types = ["literature", "synthesis"]


class _Model:
    provider = "gemini"
    model_name = "gemini-2.5-pro"


class _Optimization:
    primary_model = _Model()
    fallback_models: list[object] = []


class _Complexity:
    domains = ["research"]


class _Decision:
    routing_strategy = RoutingStrategy.BALANCED
    collaboration_mode = CollaborationMode.HIERARCHICAL
    agent_allocation = _Allocation()
    optimization_result = _Optimization()
    complexity_analysis = _Complexity()


def _binding() -> ExecutionAuthorityBinding:
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
            max_cost_usd=Decimal("1.00"),
            max_total_tokens=1000,
            max_tool_invocations=0,
            max_parallel_tasks=2,
            max_attempts_per_task=1,
            task_timeout_seconds=60,
        ),
        stop_conditions=("complete",),
        evaluator_requirements=(),
        deadline=now + timedelta(minutes=5),
        compiled_at=now,
    )


def _project() -> ResearchProject:
    return ResearchProject(
        title="Authority test project",
        query=ResearchQuery(
            text="Research the authority boundary",
            domains=["research"],
            depth_level=ResearchDepth.COMPREHENSIVE,
        ),
        user_id="user-1",
        scope=ResearchScope(max_sources=1),
    )


def _service(
    *, resolver: object | None = None
) -> tuple[DirectExecutionService, AsyncMock]:
    router = AsyncMock()
    router.route.return_value = _Decision()
    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()
    bridge.health_check.return_value = {"status": "healthy"}
    supervisor_factory = Mock()
    supervisor_factory.health_check = AsyncMock(return_value={"status": "healthy"})
    return (
        DirectExecutionService(
            masr_router=router,
            supervisor_bridge=bridge,
            supervisor_factory=supervisor_factory,
            execution_authority_resolver=resolver,
            execution_plan_compiler=ExecutionPlanCompiler(),
        ),
        router,
    )


@pytest.mark.asyncio
async def test_missing_authority_fails_before_masr_state_or_task_creation() -> None:
    service, router = _service()

    with pytest.raises(ExecutionAuthorityRequiredError):
        await service.start_research_execution(_project())

    router.route.assert_not_called()
    assert service.active_executions == {}
    assert service.execution_stats["total_executions"] == 0
    assert service._background_tasks == set()


@pytest.mark.asyncio
async def test_unknown_authority_fails_before_masr_state_or_task_creation() -> None:
    service, router = _service(resolver=MappingExecutionAuthorityResolver({}))

    with pytest.raises(ExecutionAuthorityUnavailableError):
        await service.start_research_execution(
            _project(),
            authority_reference=ExecutionAuthorityReference(
                authority_id="unknown", authority_version="1"
            ),
        )

    router.route.assert_not_called()
    assert service.active_executions == {}
    assert service.execution_stats["total_executions"] == 0
    assert service._background_tasks == set()


@pytest.mark.asyncio
async def test_valid_authority_resolves_routes_and_compiles_once_before_recording_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _binding()
    resolver = Mock()
    resolver.resolve.side_effect = lambda reference: binding
    service, router = _service(resolver=resolver)
    events: list[str] = []
    router.route.side_effect = lambda **_kwargs: events.append("route") or _Decision()
    original_compile = service.execution_plan_compiler.compile
    compile_spy = Mock(
        side_effect=lambda proposal, authority: (
            events.append("compile") or original_compile(proposal, authority)
        )
    )
    monkeypatch.setattr(service.execution_plan_compiler, "compile", compile_spy)
    resolver.resolve.side_effect = lambda reference: events.append("resolve") or binding

    execution_id = await service.start_research_execution(
        _project(),
        authority_reference=ExecutionAuthorityReference(
            authority_id="authority-1", authority_version="1"
        ),
    )

    router.route.assert_awaited_once()
    resolver.resolve.assert_called_once()
    compile_spy.assert_called_once()
    assert events == ["resolve", "route", "compile"]
    assert service.active_executions[execution_id].execution_plan is not None
    assert service.execution_stats["total_executions"] == 1
    await service.close()


@pytest.mark.asyncio
async def test_compiler_mismatch_fails_before_state_or_task_creation() -> None:
    binding = _binding()
    resolver = MappingExecutionAuthorityResolver({("authority-1", "1"): binding})
    service, router = _service(resolver=resolver)
    proposal = _Decision()
    proposal.agent_allocation.worker_types = ["untrusted-worker"]
    router.route.return_value = proposal

    with pytest.raises(ValueError, match="worker types"):
        await service.start_research_execution(
            _project(),
            authority_reference=ExecutionAuthorityReference(
                authority_id="authority-1", authority_version="1"
            ),
        )

    router.route.assert_awaited_once()
    assert service.active_executions == {}
    assert service.execution_stats["total_executions"] == 0
    assert service._background_tasks == set()
