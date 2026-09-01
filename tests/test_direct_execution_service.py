"""
Tests for Direct Execution Service

Tests the direct MASR routing and supervisor execution service that replaces
the Temporal workflow system.
"""

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.agents.supervisors.analytics_supervisor import AnalyticsSupervisor
from src.agents.supervisors.content_supervisor import ContentSupervisor
from src.agents.supervisors.finance_supervisor import FinanceSupervisor
from src.agents.supervisors.research_supervisor import ResearchSupervisor
from src.agents.tools.mediation import (
    ATTEMPT_ID_CONTEXT_KEY,
    ORGANIZATION_ID_CONTEXT_KEY,
    RUN_ID_CONTEXT_KEY,
    TASK_ID_CONTEXT_KEY,
    ToolCallIdentity,
)
from src.ai_brain.integration.masr_supervisor_bridge import MASRSupervisorBridge
from src.ai_brain.router.masr import MASRouter
from src.ai_brain.router.routing_types import RoutingStrategy
from src.api.services.component_catalog import build_application_component_registry
from src.api.services.direct_execution_service import (
    DirectExecutionService,
    ExecutionStatus,
    close_direct_execution_service,
    configure_direct_execution_service,
    get_direct_execution_service,
)
from src.api.services.execution_authority_resolver import (
    MappingExecutionAuthorityResolver,
)
from src.core.capabilities import CAPABILITY_GRANTS_CONTEXT_KEY, PlanCapabilityIssuer
from src.core.contracts import (
    CapabilityGrant,
    ExecutionBudget,
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
    RoutingEdge,
    WorkerAssignment,
)
from src.core.kernel import (
    RegistryEntry,
    TypedRegistry,
)
from src.core.kernel.component_keys import SUPERVISOR_KEYS
from src.models.db.base import Base
from src.models.db.capability import AgentCapabilityGrant
from src.models.db.run_lifecycle import AgentRun, AgentRunTask, AgentTaskAttempt
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
from src.repositories.capability_repository import CapabilityRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository


# Lightweight dataclass stand-ins so the SUT's ``asdict(routing_decision)``
# call works. Real ``RoutingDecision`` requires constructing five transitive
# dataclass dependencies (ComplexityAnalysis, OptimizationResult, ...) that
# this test does not exercise; the SUT only reads ``agent_allocation.*`` and
# scalar estimate fields off the decision and serializes the rest opaquely.
@dataclass
class _FakeAgentAllocation:
    supervisor_type: str = "research"
    worker_count: int = 3
    worker_types: list[str] = field(
        default_factory=lambda: ["literature", "analysis", "synthesis"]
    )


@dataclass
class _FakeComplexityAnalysis:
    """Minimal complexity analysis stub for single-domain path."""

    domains: list[str] = field(default_factory=lambda: ["research"])
    decomposition: None = None  # No decomposition = single-domain path


@dataclass
class _FakeRoutingDecision:
    query_id: str = "test-query-123"
    collaboration_mode: str = "hierarchical"
    agent_allocation: _FakeAgentAllocation = field(default_factory=_FakeAgentAllocation)
    complexity_analysis: _FakeComplexityAnalysis = field(
        default_factory=_FakeComplexityAnalysis
    )
    estimated_cost: float = 0.015
    estimated_latency_ms: int = 120000
    estimated_quality: float = 0.87
    confidence_score: float = 0.85
    context: dict[str, Any] = field(default_factory=dict)
    routing_strategy: RoutingStrategy = RoutingStrategy.BALANCED
    optimization_result: Any = field(
        default_factory=lambda: type(
            "_Optimization",
            (),
            {
                "primary_model": type(
                    "_Model",
                    (),
                    {"provider": "gemini", "model_name": "gemini-2.5-pro"},
                )(),
                "fallback_models": [],
            },
        )()
    )


def _authority_binding() -> ExecutionAuthorityBinding:
    now = datetime(2026, 7, 28, tzinfo=UTC)
    workers = tuple(
        WorkerAssignment(
            worker_id=f"worker-{worker_type}",
            worker_type=worker_type,
            objective=f"Handle {worker_type}",
            output_schema={},
            permission_scopes=(),
            tool_allowlist=(),
        )
        for worker_type in ("literature", "analysis", "synthesis")
    )
    return ExecutionAuthorityBinding.create_for_test(
        authority_id="test-authority",
        authority_version="1",
        run_id="test-run",
        workflow_definition_id="test-workflow",
        routing_policy_id="test-policy",
        strategy="balanced",
        collaboration_mode="hierarchical",
        domains=("research",),
        supervisor_id="test-supervisor",
        supervisor_type="research",
        workers=workers,
        edges=(
            RoutingEdge(
                source_node_id="test-supervisor",
                target_node_id="worker-literature",
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
            max_parallel_tasks=3,
            max_attempts_per_task=1,
            task_timeout_seconds=1,
        ),
        stop_conditions=("complete",),
        evaluator_requirements=(),
        deadline=now.replace(year=2027),
        compiled_at=now,
    )


TEST_AUTHORITY_REFERENCE = ExecutionAuthorityReference(
    authority_id="test-authority", authority_version="1"
)

# Authority resolution and every execution read are tenant-scoped. This is the
# organization ``_authority_binding``'s run belongs to, so these tests act as
# the tenant that owns the work rather than as an unscoped caller.
TEST_ORGANIZATION_ID = "tenant-1"


def _configure_test_authority(
    service: DirectExecutionService,
) -> DirectExecutionService:
    binding = _authority_binding()
    service.execution_authority_resolver = MappingExecutionAuthorityResolver(
        {("test-authority", "1"): binding}
    )
    original_start = service.start_research_execution

    async def start_with_test_authority(*args: Any, **kwargs: Any) -> str:
        kwargs.setdefault("authority_reference", TEST_AUTHORITY_REFERENCE)
        kwargs.setdefault("organization_id", TEST_ORGANIZATION_ID)
        return await original_start(*args, **kwargs)

    service.start_research_execution = start_with_test_authority  # type: ignore[method-assign]
    return service


@pytest.fixture
def execution_service():
    """Module-level DirectExecutionService with mocked dependencies.

    Shared by ``TestDirectExecutionService`` and
    ``TestDirectExecutionPerformance``; each test gets a fresh instance.
    """
    masr_router = AsyncMock()
    masr_router.route.return_value = _FakeRoutingDecision()
    masr_router.health_check.return_value = {"status": "healthy"}

    bridge = AsyncMock()
    bridge.health_check.return_value = {"status": "healthy"}
    bridge.admit_execution_plan = Mock()
    agent_result = Mock()
    agent_result.output = {
        "research_findings": ["Finding 1"],
        "literature_sources": ["Source 1"],
        "synthesis": "ok",
        "quality_metrics": {"confidence": 0.89},
    }
    plan_result = Mock()
    plan_result.output = agent_result.output
    plan_result.workers_used = 3
    bridge.execute_execution_plan.return_value = plan_result

    publisher = AsyncMock()
    publisher.publish_project_event.return_value = None

    supervisor_factory = Mock()
    supervisor_factory.health_check = AsyncMock(return_value={"status": "healthy"})

    return _configure_test_authority(
        DirectExecutionService(
            masr_router=masr_router,
            supervisor_bridge=bridge,
            supervisor_factory=supervisor_factory,
            event_publisher=publisher,
        )
    )


@pytest.fixture
def sample_project():
    """Module-level sample ResearchProject for execution tests."""
    return ResearchProject(
        title="Test Research Project",
        query=ResearchQuery(
            text="What are the impacts of AI on society?",
            domains=["ai", "sociology"],
            depth_level=ResearchDepth.COMPREHENSIVE,
        ),
        user_id="test-user-123",
        scope=ResearchScope(max_sources=25),
    )


class TestDirectExecutionService:
    """Test suite for DirectExecutionService."""

    # Fixtures (execution_service, sample_project) are module-level so
    # TestDirectExecutionPerformance can share them.

    @pytest.mark.asyncio
    async def test_start_research_execution_success(
        self, execution_service, sample_project
    ):
        """Test successful research execution start."""

        execution_id = await execution_service.start_research_execution(sample_project)

        assert execution_id is not None
        assert execution_id in execution_service.active_executions

        execution_status = execution_service.active_executions[execution_id]
        assert execution_status.project_id == str(sample_project.id)
        assert execution_status.status == "pending"
        assert execution_status.current_phase == "initialization"

    @pytest.mark.asyncio
    async def test_execution_workflow_complete_flow(
        self, execution_service, sample_project
    ):
        """Test complete execution workflow from start to finish."""

        # Start execution
        execution_id = await execution_service.start_research_execution(sample_project)

        # Wait for async execution to complete
        await asyncio.sleep(0.1)  # Give time for background task

        # Verify MASR router was called (mocks live on the service instance)
        execution_service.masr_router.route.assert_called_once()
        call_args = execution_service.masr_router.route.call_args
        assert sample_project.query.text in str(call_args)

        # Plan-backed execution dispatches through the topology executor seam.
        execution_service.supervisor_bridge.execute_execution_plan.assert_called_once()

        # Check execution status
        execution_status = execution_service.active_executions[execution_id]

        # The execution should be completed (or completing)
        assert execution_status.status in ["running", "completed"]
        assert execution_status.routing_decision is not None
        assert execution_status.supervisor_type == "research"

    @pytest.mark.asyncio
    async def test_a_service_with_no_session_factory_produces_a_non_durable_identity(
        self,
        execution_service,
        sample_project,
    ):
        execution_id = await execution_service.start_research_execution(sample_project)
        background_tasks = tuple(execution_service._background_tasks)
        await asyncio.gather(*background_tasks)

        plan_task = (
            execution_service.supervisor_bridge.execute_execution_plan.call_args.args[1]
        )
        identity = ToolCallIdentity.from_agent_task(plan_task)

        assert plan_task.context == {}
        assert identity.bound is True
        assert identity.durable is False
        assert execution_id in execution_service.active_executions

    @pytest.mark.asyncio
    async def test_a_plan_task_carries_the_durable_run_task_and_attempt(self):
        organization_id = "00000000-0000-0000-0000-0000000000ab"
        raw_binding = _authority_binding()
        binding = replace(
            raw_binding,
            run=raw_binding.run.model_copy(
                update={
                    "tenant_id": organization_id,
                    "idempotency_key": "durable-identity-key",
                }
            ),
        )
        resolver = MappingExecutionAuthorityResolver({("test-authority", "1"): binding})
        router = AsyncMock()
        router.route.return_value = _FakeRoutingDecision()
        bridge = AsyncMock()
        bridge.admit_execution_plan = Mock()
        bridge.execute_execution_plan.return_value = Mock(
            output={"ok": True}, workers_used=0
        )
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        tables = [
            AgentRun.__table__,
            AgentRunTask.__table__,
            AgentTaskAttempt.__table__,
        ]

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all, tables=tables)

        service = DirectExecutionService(
            masr_router=router,
            supervisor_bridge=bridge,
            supervisor_factory=Mock(),
            session_factory=session_factory,
            execution_authority_resolver=resolver,
        )
        # This test isolates the lifecycle identity wiring from the existing
        # config-snapshot/event/checkpoint persistence paths.
        service._persist_config_snapshot = AsyncMock()
        service._append_event = AsyncMock()
        try:
            execution_id = await service.start_research_execution(
                ResearchProject(
                    title="Durable identity project",
                    query=ResearchQuery(
                        text="Check durable identity propagation",
                        domains=["research"],
                        depth_level=ResearchDepth.COMPREHENSIVE,
                    ),
                    user_id="test-user-123",
                    scope=ResearchScope(max_sources=1),
                ),
                authority_reference=TEST_AUTHORITY_REFERENCE,
                organization_id=organization_id,
            )
            # A caller-visible owner is not the source of lifecycle identity.
            service.active_executions[execution_id].organization_id = "spoofed-owner"
            background_tasks = tuple(service._background_tasks)
            await asyncio.gather(*background_tasks)

            plan_task = bridge.execute_execution_plan.call_args.args[1]
            async with session_factory() as session:
                lifecycle_repo = RunLifecycleRepository(session)
                run_row = await lifecycle_repo.get_run(
                    binding.run.run_id, organization_id=organization_id
                )
                assert run_row is not None
                task_rows = await lifecycle_repo.get_tasks_for_run(
                    binding.run.run_id, organization_id=organization_id
                )
                assert len(task_rows) == 1
                task_row = task_rows[0]
                attempt_rows = await lifecycle_repo.get_attempts_for_task(
                    task_row.task_id, organization_id=organization_id
                )
                assert len(attempt_rows) == 1
                attempt_row = attempt_rows[0]

            assert plan_task.id != task_row.task_id
            assert plan_task.context == {
                RUN_ID_CONTEXT_KEY: run_row.run_id,
                TASK_ID_CONTEXT_KEY: task_row.task_id,
                ATTEMPT_ID_CONTEXT_KEY: attempt_row.attempt_id,
                ORGANIZATION_ID_CONTEXT_KEY: str(run_row.organization_id),
                "capability_grants": (),
            }
        finally:
            await service.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_admission_persists_binding_grants_with_durable_identity_before_execution(
        self,
    ):
        organization_id = "00000000-0000-0000-0000-0000000000ac"
        raw_binding = _authority_binding()
        workers = tuple(
            worker.model_copy(
                update={
                    "permission_scopes": (f"{worker.worker_id}:read",),
                    "tool_allowlist": (f"{worker.worker_id}:search",),
                }
            )
            for worker in raw_binding.workers
        )
        binding = replace(
            raw_binding,
            run=raw_binding.run.model_copy(
                update={
                    "tenant_id": organization_id,
                    "idempotency_key": "capability-admission-key",
                }
            ),
            workers=workers,
            budget=raw_binding.budget.model_copy(update={"max_tool_invocations": 3}),
        )
        project = ResearchProject(
            title="Plan grant project",
            query=ResearchQuery(
                text="Check plan grants",
                domains=["research"],
                depth_level=ResearchDepth.COMPREHENSIVE,
            ),
            user_id="test-user-123",
            scope=ResearchScope(max_sources=1),
        )
        execution_status = ExecutionStatus(
            execution_id="exec-plan-grants",
            project_id=str(project.id),
            status="pending",
            # Admission authority, not this optional compiled-plan cache, is
            # the source of capability grants.
            execution_plan=None,
            organization_id=organization_id,
        )
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        tables = [
            AgentRun.__table__,
            AgentRunTask.__table__,
            AgentTaskAttempt.__table__,
            AgentCapabilityGrant.__table__,
        ]
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all, tables=tables)

        bridge = AsyncMock()
        service = DirectExecutionService(
            masr_router=AsyncMock(),
            supervisor_bridge=bridge,
            supervisor_factory=Mock(),
            session_factory=session_factory,
            capability_issuer=PlanCapabilityIssuer(),
        )
        service._persist_config_snapshot = AsyncMock()
        service._append_event = AsyncMock()
        try:
            await service._admit_run(execution_status, binding, project)

            async with session_factory() as session:
                lifecycle_repo = RunLifecycleRepository(session)
                run_row = await lifecycle_repo.get_run(
                    binding.run.run_id, organization_id=organization_id
                )
                assert run_row is not None
                task_rows = await lifecycle_repo.get_tasks_for_run(
                    binding.run.run_id, organization_id=organization_id
                )
                assert len(task_rows) == 1
                task_row = task_rows[0]
                grants = await CapabilityRepository(session).list_grants_for_task(
                    run_row.run_id,
                    task_row.task_id,
                    organization_id=organization_id,
                )

            assert len(grants) == sum(
                len(worker.tool_allowlist) for worker in binding.workers
            )
            durable_grant_ids = {grant.grant_id for grant in grants}
            assert len(durable_grant_ids) == len(grants) > 0
            assert durable_grant_ids == {
                grant.grant_id for grant in execution_status.capability_grants
            }
            assert {(grant.run_id, grant.task_id) for grant in grants} == {
                (run_row.run_id, task_row.task_id)
            }
            assert {str(grant.organization_id) for grant in grants} == {organization_id}
            assert {(grant.capability_scope, grant.tool_name) for grant in grants} == {
                (f"plan-issued:{worker.worker_id}:search", f"{worker.worker_id}:search")
                for worker in binding.workers
            }
            expected_ttl = timedelta(
                seconds=(
                    binding.budget.max_attempts_per_task
                    * binding.budget.task_timeout_seconds
                )
            )
            assert all(
                grant.expires_at - grant.issued_at == expected_ttl for grant in grants
            )
            assert all(
                isinstance(grant, CapabilityGrant)
                for grant in execution_status.capability_grants
            )
            assert {
                (grant.capability_scope, grant.tool_name)
                for grant in execution_status.capability_grants
            } == {(grant.capability_scope, grant.tool_name) for grant in grants}
            context = service._durable_agent_task_context(execution_status)
            assert context[CAPABILITY_GRANTS_CONTEXT_KEY] == (
                execution_status.capability_grants
            )
            bridge.execute_execution_plan.assert_not_called()
        finally:
            await service.close()
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_injected_supervisor_registry_does_not_reach_plan_backed_execution(
        self,
        execution_service,
        sample_project,
    ):
        """A caller-injected supervisor registry has no effect on plan-backed
        execution: the topology executor bypasses the legacy routing bridge
        entirely, so the override never has anywhere to be forwarded to."""

        class InjectedResearchSupervisor(ResearchSupervisor):
            pass

        class InjectedContentSupervisor(ContentSupervisor):
            pass

        class InjectedAnalyticsSupervisor(AnalyticsSupervisor):
            pass

        class InjectedFinanceSupervisor(FinanceSupervisor):
            pass

        default_registry = build_application_component_registry()
        replacements = {
            SUPERVISOR_KEYS["research"]: InjectedResearchSupervisor,
            SUPERVISOR_KEYS["content"]: InjectedContentSupervisor,
            SUPERVISOR_KEYS["analytics"]: InjectedAnalyticsSupervisor,
            SUPERVISOR_KEYS["finance"]: InjectedFinanceSupervisor,
        }
        injected_registry = TypedRegistry(
            RegistryEntry(entry.key, replacements.get(entry.key, entry.component))
            for entry in default_registry.entries
        )
        execution_service = _configure_test_authority(
            DirectExecutionService(
                masr_router=execution_service.masr_router,
                supervisor_bridge=execution_service.supervisor_bridge,
                supervisor_factory=execution_service.supervisor_factory,
                event_publisher=execution_service.event_publisher,
                supervisor_registry=injected_registry,
            )
        )

        plan_called = asyncio.Event()
        plan_result = (
            execution_service.supervisor_bridge.execute_execution_plan.return_value
        )

        async def signal_plan_call(*args, **kwargs):
            plan_called.set()
            return plan_result

        execution_service.supervisor_bridge.execute_execution_plan.side_effect = (
            signal_plan_call
        )

        await execution_service.start_research_execution(sample_project)
        await asyncio.wait_for(plan_called.wait(), timeout=1)

        execution_service.supervisor_bridge.execute_execution_plan.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_execution_status(self, execution_service, sample_project):
        """Test getting execution status."""

        execution_id = await execution_service.start_research_execution(sample_project)

        status = await execution_service.get_execution_status(
            execution_id, organization_id=TEST_ORGANIZATION_ID
        )
        assert status is not None
        assert status.execution_id == execution_id
        assert status.project_id == str(sample_project.id)

    @pytest.mark.asyncio
    async def test_get_execution_status_nonexistent(self, execution_service):
        """Test getting status for non-existent execution."""

        status = await execution_service.get_execution_status(
            "nonexistent-id", organization_id=TEST_ORGANIZATION_ID
        )
        assert status is None

    @pytest.mark.asyncio
    async def test_cancel_execution(self, execution_service, sample_project):
        """Test canceling an active execution."""

        execution_id = await execution_service.start_research_execution(sample_project)

        # Cancel execution
        success = await execution_service.cancel_execution(execution_id)
        assert success is True

        # Verify status is updated
        execution_status = execution_service.active_executions[execution_id]
        assert execution_status.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_execution_nonexistent(self, execution_service):
        """Test canceling non-existent execution."""

        success = await execution_service.cancel_execution("nonexistent-id")
        assert success is False

    @pytest.mark.asyncio
    async def test_max_concurrent_executions(self, execution_service, sample_project):
        """Test maximum concurrent executions limit."""

        # Set low limit for testing
        execution_service.max_concurrent_executions = 2

        # Start maximum executions
        await execution_service.start_research_execution(sample_project)
        await execution_service.start_research_execution(sample_project)

        # Third execution should fail
        with pytest.raises(RuntimeError, match="Maximum concurrent executions"):
            await execution_service.start_research_execution(sample_project)

        assert len(execution_service.active_executions) == 2

    @pytest.mark.asyncio
    async def test_execution_error_handling(self, execution_service, sample_project):
        """Test error handling in execution workflow."""

        # Mock MASR router to raise an exception
        execution_service.masr_router.route.side_effect = Exception(
            "MASR routing failed"
        )

        with pytest.raises(Exception, match="MASR routing failed"):
            await execution_service.start_research_execution(sample_project)

        assert execution_service.active_executions == {}

    @pytest.mark.asyncio
    async def test_get_execution_results(self, execution_service, sample_project):
        """Test getting execution results."""

        execution_id = await execution_service.start_research_execution(sample_project)

        # Wait for execution
        await asyncio.sleep(0.1)

        results = await execution_service.get_execution_results(
            execution_id, organization_id=TEST_ORGANIZATION_ID
        )
        assert results is not None

    @pytest.mark.asyncio
    async def test_list_active_executions(self, execution_service, sample_project):
        """Test listing active executions."""

        # Start multiple executions
        execution_id_1 = await execution_service.start_research_execution(
            sample_project
        )
        execution_id_2 = await execution_service.start_research_execution(
            sample_project
        )

        active_executions = await execution_service.list_active_executions()

        assert len(active_executions) >= 2
        execution_ids = [ex.execution_id for ex in active_executions]
        assert execution_id_1 in execution_ids
        assert execution_id_2 in execution_ids

    @pytest.mark.asyncio
    async def test_cleanup_completed_executions(
        self, execution_service, sample_project
    ):
        """Test cleanup of old completed executions."""

        # Start and complete an execution
        execution_id = await execution_service.start_research_execution(sample_project)

        # Manually mark as completed and set old timestamp
        execution_status = execution_service.active_executions[execution_id]
        execution_status.status = "completed"
        execution_status.completed_at = datetime.now(UTC)

        # Test cleanup (with 0 hour limit to clean immediately)
        cleaned_count = await execution_service.cleanup_completed_executions(
            max_age_hours=0
        )

        assert cleaned_count == 1
        assert execution_id not in execution_service.active_executions

    @pytest.mark.asyncio
    async def test_service_stats(self, execution_service, sample_project):
        """Test service statistics."""

        stats = await execution_service.get_service_stats()

        assert "execution_stats" in stats
        assert "active_executions" in stats
        assert "component_health" in stats

        assert stats["execution_stats"]["total_executions"] >= 0
        assert isinstance(stats["active_executions"], int)

    @pytest.mark.asyncio
    async def test_health_check(self, execution_service):
        """Test service health check."""

        health = await execution_service.health_check()

        assert "status" in health
        assert "components" in health
        assert "service_stats" in health

        assert health["status"] in ["healthy", "degraded", "unknown"]
        assert "masr_router" in health["components"]
        assert "supervisor_bridge" in health["components"]
        assert "supervisor_factory" in health["components"]

    def test_get_direct_execution_service_singleton(self):
        """Test that get_direct_execution_service returns singleton."""

        service1 = get_direct_execution_service()
        service2 = get_direct_execution_service()

        assert service1 is service2
        assert isinstance(service1, DirectExecutionService)


@pytest.mark.asyncio
async def test_application_owned_services_close_only_the_exact_instance() -> None:
    first = configure_direct_execution_service(masr_router=MASRouter())
    second = configure_direct_execution_service(masr_router=MASRouter())
    first_task = asyncio.create_task(asyncio.Event().wait())
    second_task = asyncio.create_task(asyncio.Event().wait())
    first._background_tasks.add(first_task)
    second._background_tasks.add(second_task)

    await close_direct_execution_service(first)

    assert first_task.cancelled()
    assert not second_task.done()
    assert second._background_tasks == {second_task}

    await close_direct_execution_service(second)
    assert second_task.cancelled()


@pytest.mark.asyncio
async def test_close_releases_lazy_fast_path_provider_exactly_once(
    execution_service: DirectExecutionService,
) -> None:
    provider = AsyncMock()
    execution_service._fast_path_provider = provider

    await execution_service.close()
    await execution_service.close()

    provider.close.assert_awaited_once()
    assert execution_service._fast_path_provider is None


@pytest.mark.asyncio
async def test_close_cleans_internally_owned_supervisor_bridge_exactly_once() -> None:
    service = DirectExecutionService(
        masr_router=MASRouter(config={"enable_caching": False}),
    )
    service.supervisor_bridge.cleanup = AsyncMock()

    await service.close()
    await service.close()

    service.supervisor_bridge.cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_leaves_injected_supervisor_bridge_caller_owned() -> None:
    registry = build_application_component_registry()
    bridge = MASRSupervisorBridge(component_registry=registry)
    bridge.cleanup = AsyncMock()
    service = DirectExecutionService(
        masr_router=MASRouter(config={"enable_caching": False}),
        supervisor_bridge=bridge,
        component_registry=registry,
    )

    await service.close()
    await service.close()

    bridge.cleanup.assert_not_awaited()


class TestExecutionStatus:
    """Test suite for ExecutionStatus data class."""

    def test_execution_status_initialization(self):
        """Test ExecutionStatus initialization."""

        status = ExecutionStatus(
            execution_id="test-123", project_id="project-456", status="pending"
        )

        assert status.execution_id == "test-123"
        assert status.project_id == "project-456"
        assert status.status == "pending"
        assert status.progress_percentage == 0.0
        assert status.current_phase == "initialization"
        assert status.agent_results == {}
        assert status.quality_scores == {}
        assert status.errors == []
        assert status.warnings == []
        assert status.retry_count == 0
        assert isinstance(status.started_at, datetime)

    def test_execution_status_default_collections(self):
        """ExecutionStatus dataclass uses default_factory for collections.

        Previously the dataclass coerced ``None`` to ``{}`` in ``__post_init__``;
        the simplified version relies on ``field(default_factory=...)`` instead.
        Explicit ``None`` is no longer accepted as input; callers should omit
        the field to get the default empty container.
        """

        status = ExecutionStatus(
            execution_id="test-123",
            project_id="project-456",
            status="pending",
        )

        assert status.agent_results == {}
        assert status.quality_scores == {}
        assert status.errors == []
        assert status.warnings == []


@pytest.mark.integration
class TestDirectExecutionIntegration:
    """Integration tests for direct execution service."""

    @pytest.mark.asyncio
    async def test_full_integration_flow(self):
        """Test full integration flow with real components."""

        # This would test with real MASR and supervisor components
        # in an integration test environment

        execution_service = DirectExecutionService()

        ResearchProject(
            title="Integration Test Project",
            query=ResearchQuery(
                text="Test query for integration",
                domains=["test"],
                depth_level=ResearchDepth.COMPREHENSIVE,
            ),
            user_id="integration-test-user",
        )

        # Health check should work
        health = await execution_service.health_check()
        assert health["status"] in ["healthy", "degraded", "unknown"]

        # Service stats should be accessible
        stats = await execution_service.get_service_stats()
        assert "execution_stats" in stats

        # Note: Full execution test would require mocking external services
        # or running in a full test environment with all dependencies


# Performance benchmarks
class TestDirectExecutionPerformance:
    """Performance tests for direct execution."""

    @pytest.mark.asyncio
    @pytest.mark.benchmark
    async def test_execution_startup_time(self, execution_service, sample_project):
        """Benchmark execution startup time."""

        start_time = datetime.now()

        execution_id = await execution_service.start_research_execution(sample_project)

        startup_time = (datetime.now() - start_time).total_seconds()

        # Should start quickly (under 100ms)
        assert startup_time < 0.1
        assert execution_id in execution_service.active_executions

    @pytest.mark.asyncio
    async def test_concurrent_execution_performance(
        self, execution_service, sample_project
    ):
        """Test performance with multiple concurrent executions."""

        # Start multiple executions concurrently
        tasks = []
        for _i in range(5):
            task = asyncio.create_task(
                execution_service.start_research_execution(sample_project)
            )
            tasks.append(task)

        start_time = datetime.now()
        execution_ids = await asyncio.gather(*tasks)
        total_time = (datetime.now() - start_time).total_seconds()

        # Should handle concurrent starts efficiently
        assert len(execution_ids) == 5
        assert all(
            ex_id in execution_service.active_executions for ex_id in execution_ids
        )
        assert total_time < 1.0  # Under 1 second for 5 concurrent starts
