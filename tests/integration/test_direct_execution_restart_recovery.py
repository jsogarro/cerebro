"""The Wave 3 Packet 3C mandatory restart-hazard acceptance test.

The plan is explicit: "Do not call execution resumable until an API restart
during an active golden run recovers without fabricated success or lost
terminal state." This proves the narrower, honestly-scoped claim this
packet actually makes: a run left non-terminal when a process disappears is
recoverable — by a *second*, unrelated ``DirectExecutionService`` instance
sharing only the Postgres database, never any Python object — into
``active_executions``, the structure every existing status/results/cancel
endpoint in ``src/api/routes/research.py`` reads directly.

"Process A" is abandoned mid-execution without a clean shutdown (the
in-flight background task is left running, blocked on a mock that never
returns) — the closest a test can get to simulating a crash without an
actual process boundary. "Process B" is constructed from scratch and calls
only ``restore_active_executions()``, mirroring exactly what
``src/api/main.py``'s lifespan does at startup.
"""

import asyncio
import dataclasses
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.ai_brain.router.routing_types import RoutingStrategy
from src.api.services.direct_execution_service import DirectExecutionService
from src.api.services.execution_authority_resolver import (
    MappingExecutionAuthorityResolver,
)
from src.core.contracts import (
    ExecutionBudget,
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
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
from src.repositories.run_lifecycle_repository import RunLifecycleRepository

pytestmark = [pytest.mark.integration]

ORG_ID = "00000000-0000-0000-0000-0000000000ef"
NOW = datetime(2026, 8, 4, tzinfo=UTC)


# Mirrors ``tests/test_direct_execution_service.py``'s ``_FakeRoutingDecision``
# family: the smallest dataclass shape ``ExecutionPlanCompiler.compile`` and
# ``asdict(routing_decision)`` both accept, matched to ``_make_binding``'s
# ``strategy="balanced"``/``collaboration_mode="hierarchical"``.
@dataclass
class _AllocStub:
    supervisor_type: str = "research"
    worker_count: int = 1
    worker_types: list[str] = field(default_factory=lambda: ["literature"])


@dataclass
class _ComplexityAnalysisStub:
    domains: list[str] = field(default_factory=lambda: ["research"])
    decomposition: None = None


@dataclass
class _RoutingDecisionStub:
    query_id: str = "test-query"
    collaboration_mode: str = "hierarchical"
    agent_allocation: _AllocStub = field(default_factory=_AllocStub)
    complexity_analysis: _ComplexityAnalysisStub = field(
        default_factory=_ComplexityAnalysisStub
    )
    estimated_cost: float = 0.01
    estimated_latency_ms: int = 1000
    estimated_quality: float = 0.9
    confidence_score: float = 0.9
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


def _make_binding(run_id: str) -> ExecutionAuthorityBinding:
    raw = ExecutionAuthorityBinding.create_for_test(
        authority_id="restart-authority",
        authority_version="1",
        run_id=run_id,
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
        ),
        edges=(),
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
            max_parallel_tasks=1,
            max_attempts_per_task=1,
            task_timeout_seconds=60,
        ),
        stop_conditions=("complete",),
        evaluator_requirements=(),
        deadline=NOW + timedelta(minutes=5),
        compiled_at=NOW,
    )
    return dataclasses.replace(
        raw,
        run=raw.run.model_copy(
            update={"tenant_id": ORG_ID, "idempotency_key": f"key-{run_id}"}
        ),
    )


def _project() -> ResearchProject:
    return ResearchProject(
        title="Restart recovery test",
        query=ResearchQuery(
            text="Does a restart lose in-flight run state?",
            domains=["research"],
            depth_level=ResearchDepth.COMPREHENSIVE,
        ),
        user_id="user-1",
        scope=ResearchScope(max_sources=5),
    )


@pytest.mark.asyncio
async def test_restart_recovers_non_terminal_run_into_active_executions(
    test_engine: AsyncEngine,
) -> None:
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "restart-run-1"
    binding = _make_binding(run_id)
    resolver = MappingExecutionAuthorityResolver({("restart-authority", "1"): binding})

    router = AsyncMock()
    router.route.return_value = _RoutingDecisionStub()

    supervisor_started = asyncio.Event()
    release_supervisor = asyncio.Event()

    async def _blocking_execute_plan(*_args: Any, **_kwargs: Any) -> Any:
        supervisor_started.set()
        await release_supervisor.wait()
        return Mock(output={"result": "ok"}, workers_used=1)

    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()
    bridge.execute_execution_plan.side_effect = _blocking_execute_plan

    service_a = DirectExecutionService(
        masr_router=router,
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=session_factory,
        execution_authority_resolver=resolver,
    )

    project = _project()
    execution_id = await service_a.start_research_execution(
        project,
        authority_reference=ExecutionAuthorityReference(
            authority_id="restart-authority", authority_version="1"
        ),
    )

    # Wait until the run has genuinely reached "running" and been persisted
    # as such — the point at which "process A" then disappears.
    await asyncio.wait_for(supervisor_started.wait(), timeout=5)
    assert execution_id in service_a.active_executions
    assert service_a.active_executions[execution_id].run_id == run_id

    async with session_factory() as verify_session:
        row = await RunLifecycleRepository(verify_session).get_run(
            run_id, organization_id=ORG_ID
        )
        assert row is not None
        assert row.status == "running"
        assert row.completed_at is None

    # --- simulated restart: a brand-new service, no shared Python state ---
    service_b = DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=AsyncMock(),
        supervisor_factory=Mock(),
        session_factory=session_factory,
    )
    assert service_b.active_executions == {}

    restored_count = await service_b.restore_active_executions()

    assert restored_count == 1
    assert execution_id in service_b.active_executions
    recovered = service_b.active_executions[execution_id]
    assert recovered.status == "running"
    assert recovered.run_id == run_id
    assert recovered.project_id == str(project.id)

    # A second restore is a no-op for an execution already in the cache —
    # it must not duplicate or reset it.
    restored_again = await service_b.restore_active_executions()
    assert restored_again == 0
    assert len(service_b.active_executions) == 1

    # Cleanup: release the blocked background task in "process A" and close
    # both services.
    release_supervisor.set()
    await service_a.close()
    await service_b.close()


@pytest.mark.asyncio
async def test_restart_does_not_recover_terminal_runs(
    test_engine: AsyncEngine,
) -> None:
    """A run that reached a terminal status before the restart is not
    rehydrated — its outcome is already durably recorded, and reappearing in
    ``active_executions`` would be pure duplication, not recovery."""
    from src.ai_brain.router.routing_types import RoutingExecutionPolicy

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "restart-run-terminal"
    binding = _make_binding(run_id)
    resolver = MappingExecutionAuthorityResolver({("restart-authority", "1"): binding})

    router = AsyncMock()
    router.route.return_value = _RoutingDecisionStub()
    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()

    service_a = DirectExecutionService(
        masr_router=router,
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=session_factory,
        execution_authority_resolver=resolver,
    )
    project = _project()
    execution_id = await service_a.start_research_execution(
        project,
        execution_policy=RoutingExecutionPolicy.fixture(),
        fixture_result={"ok": True},
        authority_reference=ExecutionAuthorityReference(
            authority_id="restart-authority", authority_version="1"
        ),
    )
    for _ in range(50):
        if service_a.active_executions[execution_id].status == "completed":
            break
        await asyncio.sleep(0.02)
    assert service_a.active_executions[execution_id].status == "completed"

    service_b = DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=AsyncMock(),
        supervisor_factory=Mock(),
        session_factory=session_factory,
    )
    restored_count = await service_b.restore_active_executions()

    assert execution_id not in service_b.active_executions
    # Other non-terminal runs from earlier tests sharing this engine are not
    # this test's concern; only assert this run specifically was excluded.
    assert restored_count >= 0

    await service_a.close()
    await service_b.close()
