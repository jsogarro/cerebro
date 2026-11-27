"""A run succeeds only when every admitted worker did, through the real service path."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from src.agents.models import AgentResult, AgentTask
from src.ai_brain.integration.execution_plan_topology import (
    ExecutionPlanTopologyExecutor,
    ExecutionTopologyUnsupportedError,
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


def _worker(worker_id: str, schema: dict[str, Any] | None = None) -> WorkerAssignment:
    return WorkerAssignment(
        worker_id=worker_id,
        worker_type=worker_id,
        objective=f"objective {worker_id}",
        output_schema=schema or {},
        permission_scopes=(),
        tool_allowlist=(),
    )


def _mode(workers: tuple[WorkerAssignment, ...], hierarchical: bool):
    if hierarchical:
        return CollaborationMode.HIERARCHICAL
    return CollaborationMode.PARALLEL if len(workers) > 1 else CollaborationMode.DIRECT


def _plan(
    workers: tuple[WorkerAssignment, ...], edges: tuple[RoutingEdge, ...] = ()
) -> ExecutionPlan:
    now = datetime(2026, 7, 29, tzinfo=UTC)
    hierarchical = bool(edges)
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
            collaboration_mode=_mode(workers, hierarchical),
            supervisor_id="root" if hierarchical else None,
            supervisor_type="research" if hierarchical else None,
            workers=workers,
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
                max_total_tokens=100_000,
                max_tool_invocations=0,
                max_parallel_tasks=len(workers),
                max_attempts_per_task=1,
                task_timeout_seconds=30,
            ),
            stop_conditions=("complete",),
            evaluator_requirements=(),
        ),
    )


def _result(task: AgentTask, status: str, output: dict[str, Any], confidence=0.9):
    return AgentResult(task.id, status, output, confidence, 0.0)


def _executor(by_worker: dict[str, tuple[str, dict[str, Any], float]], calls=None):
    async def execute(worker: WorkerAssignment, task: AgentTask) -> AgentResult:
        if calls is not None:
            calls.append(str(worker.worker_id))
        status, output, confidence = by_worker[str(worker.worker_id)]
        return _result(task, status, output, confidence)

    return ExecutionPlanTopologyExecutor(
        worker_executor=execute, capability_checker=lambda _: True
    )


_ROOT = AgentTask(id="root", agent_type="execution_plan", input_data={})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "outcome", "code"),
    [
        ("failed", "failed", "WORKER_FAILED"),
        ("timeout", "timed_out", "WORKER_TIMEOUT"),
        ("partial", "failed", "WORKER_STATUS_UNKNOWN"),
    ],
)
async def test_a_worker_that_did_not_succeed_fails_the_plan(status, outcome, code):
    result = await _executor({"a": (status, {"error": "boom"}, 0.9)}).execute(
        _plan((_worker("a"),)), _ROOT
    )

    assert not result.succeeded
    assert result.failures == [
        {"worker_id": "a", "status": outcome, "code": code, "reason": None}
    ]
    assert result.output == {"a": {"error": "boom"}}  # partial output kept


@pytest.mark.asyncio
async def test_zero_confidence_success_is_not_success():
    # Shape of the comparative worker's "no items to compare" abstention.
    abstention = {"comparison_matrix": {}, "synthesis": "No items provided"}
    result = await _executor({"a": ("success", abstention, 0.0)}).execute(
        _plan((_worker("a"),)), _ROOT
    )

    assert not result.succeeded
    assert result.failures[0]["code"] == "ZERO_CONFIDENCE"


@pytest.mark.asyncio
async def test_schema_invalid_output_fails_without_echoing_content_and_blocks_children():
    schema = {
        "type": "object",
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
    }
    edges = (
        RoutingEdge(source_node_id="root", target_node_id="a", relation="delegates"),
        RoutingEdge(source_node_id="a", target_node_id="b", relation="depends_on"),
    )
    calls: list[str] = []
    result = await _executor(
        {
            "a": ("success", {"answer": 42, "secret": "tenant-data"}, 0.9),
            "b": ("success", {"x": 1}, 0.9),
        },
        calls,
    ).execute(_plan((_worker("a", schema), _worker("b")), edges), _ROOT)

    assert calls == ["a"]
    assert not result.succeeded
    assert result.blocked_worker_ids == ("b",)
    failure = result.failures[0]
    assert failure["code"] == "OUTPUT_SCHEMA_VIOLATION"
    assert "tenant-data" not in str(result.failures) and "42" not in str(failure)


@pytest.mark.asyncio
async def test_two_successes_and_one_failure_keep_partials_but_fail_the_plan():
    result = await _executor(
        {
            "a": ("success", {"v": 1}, 0.9),
            "b": ("success", {"v": 2}, 0.9),
            "c": ("failed", {}, 0.0),
        }
    ).execute(_plan((_worker("a"), _worker("b"), _worker("c"))), _ROOT)

    assert not result.succeeded
    assert result.output["a"] == {"v": 1} and result.output["b"] == {"v": 2}
    assert [f["worker_id"] for f in result.failures] == ["c"]


@pytest.mark.asyncio
async def test_all_valid_workers_succeed_and_empty_schema_stays_permissive():
    result = await _executor(
        {"a": ("success", {"anything": [1]}, 0.5)},
    ).execute(_plan((_worker("a", {}),)), _ROOT)

    assert result.succeeded and result.failures == []


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "not-a-type"},
        {"$ref": "https://example.com/schema.json"},
        {"properties": {"x": {"$ref": "file:///etc/passwd"}}},
        {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object"},
    ],
)
def test_admission_rejects_unusable_output_schemas(schema):
    executor = _executor({})
    with pytest.raises(ExecutionTopologyUnsupportedError):
        executor.admit(_plan((_worker("a", schema),)))


def test_local_references_are_resolved_without_io(monkeypatch):
    import socket

    def no_network(*_a, **_k):
        raise AssertionError("schema validation must not touch the network")

    monkeypatch.setattr(socket, "socket", no_network)
    schema = {
        "$defs": {"s": {"type": "string"}},
        "type": "object",
        "properties": {"a": {"$ref": "#/$defs/s"}},
        "required": ["a"],
    }
    executor = _executor({})
    executor.admit(_plan((_worker("a", schema),)))


async def _run_service(plan: ExecutionPlan, executor: ExecutionPlanTopologyExecutor):
    class Bridge:
        def admit_execution_plan(self, execution_plan: ExecutionPlan) -> None:
            executor.admit(execution_plan)

        async def execute_execution_plan(self, execution_plan, task):
            return await executor.execute(execution_plan, task)

    binding = ExecutionAuthorityBinding.create_for_test(
        authority_id="authority-1",
        authority_version="1",
        run_id="run-1",
        workflow_definition_id="workflow-1",
        routing_policy_id="policy-1",
        strategy="balanced",
        collaboration_mode=_mode(plan.routing_decision.workers, False).value,
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
    router.route.return_value = Proposal(Allocation())
    compiler = Mock()
    compiler.compile.return_value = plan
    service = DirectExecutionService(
        masr_router=router,
        supervisor_bridge=Bridge(),
        supervisor_factory=Mock(),
        execution_authority_resolver=MappingExecutionAuthorityResolver(
            {("authority-1", "1"): binding}
        ),
        execution_plan_compiler=compiler,
    )
    project = ResearchProject(
        title="Integrity",
        user_id="user-1",
        query=ResearchQuery(text="Answer", domains=["research"], depth_level="survey"),
    )
    execution_id = await service.start_research_execution(
        project,
        authority_reference=ExecutionAuthorityReference(
            authority_id="authority-1", authority_version="1"
        ),
        organization_id="tenant-1",
    )
    await asyncio.gather(*service._background_tasks, return_exceptions=True)
    return service, service.active_executions[execution_id]


@pytest.mark.asyncio
async def test_service_reports_failed_not_completed_when_every_worker_failed():
    plan = _plan((_worker("a"), _worker("b")))
    service, status = await _run_service(
        plan,
        _executor({"a": ("failed", {"error": "x"}, 0.0), "b": ("failed", {}, 0.0)}),
    )

    assert status.status == "failed"
    assert status.failure_code == "WORKER_FAILED"
    assert status.retry_count == 0  # a semantic failure is not retried
    assert service.execution_stats["successful_executions"] == 0
    assert service.execution_stats["failed_executions"] == 1
    assert status.agent_results["a"] == {"error": "x"}  # partial diagnostics kept
    assert status.final_output is None
    assert any("a" in e and "WORKER_FAILED" in e for e in status.errors)


@pytest.mark.asyncio
async def test_service_still_completes_when_all_workers_succeed():
    service, status = await _run_service(
        _plan((_worker("a"),)), _executor({"a": ("success", {"v": 1}, 0.9)})
    )

    assert status.status == "completed"
    assert status.final_output == {"a": {"v": 1}}
    assert service.execution_stats["successful_executions"] == 1
