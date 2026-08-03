"""Regression test for the research-execution retry loop.

`ExecutionStatus.started_at` is timezone-aware (`datetime.now(UTC)`), but the
`finally` block in `_execute_research_workflow` set `completed_at` with a naive
`datetime.now()` and then subtracted the two. That raised

    TypeError: can't subtract offset-naive and offset-aware datetimes

from the `finally` block on *every* run. Because a `finally` raise propagates
even when the body succeeded, it tripped the `@retry` wrapper and re-ran the
entire workflow — so a query that had already completed was executed again,
leaving `GET /query/execution/{id}/status` pinned at `running / 40%` while the
pipeline churned through retries.

This test drives `_execute_research_workflow` with mocked collaborators and
asserts the workflow runs exactly once and reaches a terminal `completed` state
with a computed duration. On the pre-fix code the `finally` raises, `@retry`
re-runs it three times, and the call ultimately raises `RetryError`.

Dispatch now goes through the plan-topology executor
(``supervisor_bridge.execute_execution_plan``) rather than the legacy
``execute_routing_decision`` bridge call, so this test drives a minimal
compiled ``ExecutionPlan`` through that seam; the regression it guards
(the ``finally`` block's tz-aware duration math) is unchanged live code.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from src.ai_brain.router.routing_types import CollaborationMode
from src.api.services.direct_execution_service import (
    DirectExecutionService,
    ExecutionStatus,
)
from src.core.contracts import (
    CollaborationMode as PlanCollaborationMode,
)
from src.core.contracts import (
    ExecutionBudget,
    ExecutionPlan,
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
    RoutingDecision,
    WorkerAssignment,
)


@dataclass
class _AllocStub:
    supervisor_type: str = "research"


@dataclass
class _ComplexityAnalysisStub:
    """Minimal complexity analysis stub for single-domain path."""

    decomposition: None = None  # No decomposition = single-domain path


@dataclass
class _DecisionStub:
    """Minimal dataclass so ``asdict(routing_decision)`` works."""

    agent_allocation: _AllocStub
    complexity_analysis: _ComplexityAnalysisStub
    # Real RoutingDecision always carries a collaboration_mode; the service
    # checks it for the fast-path bypass before entering orchestration.
    collaboration_mode: CollaborationMode = CollaborationMode.HIERARCHICAL


def _minimal_execution_plan() -> ExecutionPlan:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    worker = WorkerAssignment(
        worker_id="worker-1",
        worker_type="synthesis",
        objective="Answer",
        output_schema={},
        permission_scopes=(),
        tool_allowlist=(),
    )
    return ExecutionPlan(
        execution_plan_id="plan-no-retry-loop",
        plan_version=1,
        run_id="run-1",
        workflow_definition_id="workflow-1",
        workflow_definition_version="1",
        routing_policy_id="policy-1",
        routing_policy_version="1",
        compiled_at=now,
        deadline=now + timedelta(minutes=5),
        amendment=None,
        routing_decision=RoutingDecision(
            routing_decision_id="decision-1",
            strategy="balanced",
            domains=("research",),
            collaboration_mode=PlanCollaborationMode.DIRECT,
            supervisor_id=None,
            supervisor_type=None,
            workers=(worker,),
            edges=(),
            provider_model_policy=ProviderModelPolicy(
                primary=ProviderModelRoute(provider="gemini", model="gemini-2.5-pro"),
                fallback_mode=FallbackMode.FAIL_CLOSED,
                fallbacks=(),
                provider_allowlist=("gemini",),
                model_allowlist=("gemini-2.5-pro",),
            ),
            budget=ExecutionBudget(
                max_cost_usd=Decimal("1"),
                max_total_tokens=1000,
                max_tool_invocations=0,
                max_parallel_tasks=1,
                max_attempts_per_task=1,
                task_timeout_seconds=30,
            ),
            stop_conditions=("complete",),
            evaluator_requirements=(),
        ),
    )


def _make_service() -> DirectExecutionService:
    router = MagicMock()
    router.route = AsyncMock(
        return_value=_DecisionStub(_AllocStub(), _ComplexityAnalysisStub())
    )

    bridge = MagicMock()
    bridge.execute_execution_plan = AsyncMock(
        return_value=SimpleNamespace(
            output={"result": "ok"},
            workers_used=1,
        )
    )

    # event_publisher=None makes _publish_progress_update a no-op.
    return DirectExecutionService(
        masr_router=router, supervisor_bridge=bridge, event_publisher=None
    )


def _make_project() -> MagicMock:
    project = MagicMock()
    project.id = uuid4()
    project.query.text = "compare two studies on sleep and memory"
    project.query.domains = ["research"]
    project.query.model_dump.return_value = {}
    project.scope.model_dump.return_value = {}
    project.user_id = "user-1"
    project.title = "Test project"
    return project


async def test_workflow_runs_once_and_reaches_completed() -> None:
    service = _make_service()
    project = _make_project()
    status = ExecutionStatus(
        execution_id="exec-1",
        project_id=str(project.id),
        status="pending",
        current_phase="initialization",
    )
    routing_decision = _DecisionStub(_AllocStub(), _ComplexityAnalysisStub())
    execution_plan = _minimal_execution_plan()

    # On the pre-fix code the finally raises TypeError, @retry re-runs the
    # workflow, and this await ultimately raises tenacity.RetryError.
    await service._execute_research_workflow(
        project,
        status,
        {},
        routing_decision=routing_decision,
        execution_plan=execution_plan,
    )

    # Exactly one execution — no spurious retry.
    assert service.supervisor_bridge.execute_execution_plan.await_count == 1
    # Terminal state, correctly recorded.
    assert status.status == "completed"
    assert status.progress_percentage == 100.0
    # The finally block's duration math ran without raising.
    assert status.completed_at is not None
    assert status.execution_time_seconds >= 0.0
    # started_at/completed_at must agree on tz-awareness for the subtraction.
    assert status.started_at.tzinfo is not None
    assert status.completed_at.tzinfo is not None
