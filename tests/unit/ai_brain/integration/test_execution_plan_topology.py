"""Execution-spy coverage for the immutable plan topology boundary."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest

from src.agents.models import AgentResult, AgentTask
from src.ai_brain.integration.execution_plan_topology import (
    ExecutionPlanTopologyExecutor,
    ExecutionTopologyUnsupportedError,
    PlanExecutionResult,
)
from src.api.services.direct_execution_service import DirectExecutionService
from src.api.services.execution_authority_resolver import (
    MappingExecutionAuthorityResolver,
)
from src.core.contracts import (
    CollaborationMode,
    ExecutionBudget,
    ExecutionPlan,
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
    RoutingDecision,
    RoutingEdge,
    WorkerAssignment,
)
from src.models.execution_authority import (
    ExecutionAuthorityBinding,
    ExecutionAuthorityReference,
)
from src.models.research_project import ResearchProject, ResearchQuery


def _plan(
    *,
    mode: CollaborationMode = CollaborationMode.PARALLEL,
    workers: tuple[WorkerAssignment, ...] | None = None,
    edges: tuple[RoutingEdge, ...] = (),
    supervisor_id: str | None = None,
    evaluator_requirements: tuple[str, ...] = (),
) -> ExecutionPlan:
    now = datetime(2026, 7, 29, tzinfo=UTC)
    selected_workers = workers or (
        WorkerAssignment(
            worker_id="worker-a",
            worker_type="synthesis",
            objective="First output",
            output_schema={},
            permission_scopes=(),
            tool_allowlist=(),
        ),
        WorkerAssignment(
            worker_id="worker-b",
            worker_type="synthesis",
            objective="Second output",
            output_schema={},
            permission_scopes=(),
            tool_allowlist=(),
        ),
    )
    return ExecutionPlan(
        execution_plan_id="plan-1",
        plan_version=1,
        run_id="run-1",
        workflow_definition_id="workflow-1",
        workflow_definition_version="1",
        routing_policy_id="policy-1",
        routing_policy_version="1",
        compiled_at=now,
        deadline=now + timedelta(minutes=1),
        amendment=None,
        routing_decision=RoutingDecision(
            routing_decision_id="decision-1",
            strategy="balanced",
            domains=("research",),
            collaboration_mode=mode,
            supervisor_id=supervisor_id,
            supervisor_type="research" if supervisor_id else None,
            workers=selected_workers,
            edges=edges,
            provider_model_policy=ProviderModelPolicy(
                primary=ProviderModelRoute(provider="gemini", model="gemini-2.5-pro"),
                fallback_mode=FallbackMode.FAIL_CLOSED,
                fallbacks=(),
                provider_allowlist=("gemini",),
                model_allowlist=("gemini-2.5-pro",),
            ),
            budget=ExecutionBudget(
                max_cost_usd=Decimal("1"),
                max_total_tokens=100,
                max_tool_invocations=0,
                max_parallel_tasks=len(selected_workers),
                max_attempts_per_task=1,
                task_timeout_seconds=30,
            ),
            stop_conditions=("complete",),
            evaluator_requirements=evaluator_requirements,
        ),
    )


@pytest.mark.asyncio
async def test_parallel_plan_executes_each_plan_local_worker_instance() -> None:
    calls: list[tuple[str, str]] = []

    async def execute(worker: WorkerAssignment, task: AgentTask) -> AgentResult:
        calls.append((str(worker.worker_id), task.id))
        return AgentResult(task.id, "success", {"worker": str(worker.worker_id)}, 1, 0)

    executor = ExecutionPlanTopologyExecutor(
        worker_executor=execute,
        capability_checker=lambda worker_type: worker_type == "synthesis",
    )
    result = await executor.execute(
        _plan(), AgentTask(id="root", agent_type="execution_plan", input_data={})
    )

    assert calls == [("worker-a", "root:worker-a"), ("worker-b", "root:worker-b")]
    assert result.output == {
        "worker-a": {"worker": "worker-a"},
        "worker-b": {"worker": "worker-b"},
    }


@pytest.mark.asyncio
async def test_a_worker_task_inherits_its_parent_s_durable_context() -> None:
    worker_tasks: list[AgentTask] = []
    parent_context = {
        "run_id": "run-from-admission",
        "task_id": "task-from-admission",
        "attempt_id": "attempt-from-admission",
        "organization_id": "org-from-admission",
    }

    async def execute(_worker: WorkerAssignment, task: AgentTask) -> AgentResult:
        worker_tasks.append(task)
        return AgentResult(task.id, "success", {}, 1.0, 0.0)

    parent_task = AgentTask(
        id="ephemeral-plan-task",
        agent_type="execution_plan",
        input_data={},
        context=parent_context,
    )
    await ExecutionPlanTopologyExecutor(
        worker_executor=execute,
        capability_checker=lambda _: True,
    ).execute(_plan(), parent_task)

    assert len(worker_tasks) == 2
    assert [worker_task.context for worker_task in worker_tasks] == [
        parent_context,
        parent_context,
    ]


@pytest.mark.asyncio
async def test_hierarchical_plan_obeys_worker_dependencies_without_supervisor_graph() -> (
    None
):
    calls: list[str] = []
    workers = (
        WorkerAssignment(
            worker_id="first",
            worker_type="synthesis",
            objective="A",
            output_schema={},
            permission_scopes=(),
            tool_allowlist=(),
        ),
        WorkerAssignment(
            worker_id="second",
            worker_type="synthesis",
            objective="B",
            output_schema={},
            permission_scopes=(),
            tool_allowlist=(),
        ),
    )
    plan = _plan(
        mode=CollaborationMode.HIERARCHICAL,
        workers=workers,
        supervisor_id="root",
        edges=(
            RoutingEdge(
                source_node_id="root", target_node_id="first", relation="delegates"
            ),
            RoutingEdge(
                source_node_id="first", target_node_id="second", relation="depends_on"
            ),
        ),
    )

    async def execute(worker: WorkerAssignment, task: AgentTask) -> AgentResult:
        calls.append(str(worker.worker_id))
        return AgentResult(task.id, "success", {}, 1, 0)

    result = await ExecutionPlanTopologyExecutor(
        worker_executor=execute, capability_checker=lambda _: True
    ).execute(
        plan, AgentTask(id="root-task", agent_type="execution_plan", input_data={})
    )

    assert calls == ["first", "second"]
    assert result.workers_used == 2


def test_unsupported_plan_fails_closed_before_worker_execution() -> None:
    worker_executor = AsyncMock()
    executor = ExecutionPlanTopologyExecutor(
        worker_executor=worker_executor, capability_checker=lambda _: True
    )

    with pytest.raises(ExecutionTopologyUnsupportedError) as exc_info:
        executor.admit(_plan(evaluator_requirements=("quality",)))

    assert exc_info.value.code == "EXECUTION_TOPOLOGY_UNSUPPORTED"
    worker_executor.assert_not_called()


@pytest.mark.asyncio
async def test_direct_service_admits_before_state_and_bypasses_legacy_routing_bridge() -> (
    None
):
    events: list[str] = []
    plan = _plan(
        mode=CollaborationMode.DIRECT,
        workers=(
            WorkerAssignment(
                worker_id="only",
                worker_type="synthesis",
                objective="Answer",
                output_schema={},
                permission_scopes=(),
                tool_allowlist=(),
            ),
        ),
    )

    class BridgeSpy:
        def admit_execution_plan(self, execution_plan: ExecutionPlan) -> None:
            assert execution_plan is plan
            events.append("admit")

        async def execute_execution_plan(
            self, execution_plan: ExecutionPlan, task: AgentTask
        ) -> PlanExecutionResult:
            events.append("execute-plan")
            return PlanExecutionResult(worker_results={})

        async def execute_routing_decision(self, **_kwargs: object) -> object:
            raise AssertionError("legacy supervisor bridge must not run")

    binding = ExecutionAuthorityBinding.create_for_test(
        authority_id="authority-1",
        authority_version="1",
        run_id="run-1",
        workflow_definition_id="workflow-1",
        routing_policy_id="policy-1",
        strategy="balanced",
        collaboration_mode="direct",
        domains=("research",),
        supervisor_id=None,
        supervisor_type=None,
        workers=plan.routing_decision.workers,
        edges=(),
        provider_model_policy=plan.routing_decision.provider_model_policy,
        budget=plan.routing_decision.budget,
        stop_conditions=("complete",),
        evaluator_requirements=(),
        compiled_at=plan.compiled_at,
        deadline=plan.deadline,
    )

    @dataclass
    class Allocation:
        supervisor_type: str = "research"

    @dataclass
    class Proposal:
        agent_allocation: Allocation

    router = AsyncMock()
    router.route.side_effect = lambda **_kwargs: (
        events.append("route") or Proposal(Allocation())
    )
    compiler = Mock()
    compiler.compile.side_effect = lambda *_args: events.append("compile") or plan
    service = DirectExecutionService(
        masr_router=router,
        supervisor_bridge=BridgeSpy(),
        supervisor_factory=Mock(),
        execution_authority_resolver=MappingExecutionAuthorityResolver(
            {("authority-1", "1"): binding}
        ),
        execution_plan_compiler=compiler,
    )
    project = ResearchProject(
        title="Plan topology",
        user_id="user-1",
        query=ResearchQuery(text="Answer", domains=["research"], depth_level="survey"),
    )

    execution_id = await service.start_research_execution(
        project,
        authority_reference=ExecutionAuthorityReference(
            authority_id="authority-1", authority_version="1"
        ),
        # Authority resolution is tenant-scoped; ``create_for_test`` bindings
        # belong to this organization.
        organization_id="tenant-1",
    )
    assert events == ["route", "compile", "admit"]
    assert execution_id in service.active_executions
    await asyncio.gather(*service._background_tasks)
    assert events == ["route", "compile", "admit", "execute-plan"]
