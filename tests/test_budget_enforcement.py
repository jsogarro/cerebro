"""ExecutionBudget ceilings are enforced, not merely declared.

Covers the ledger itself and each enforcement point: pre-dispatch admission,
the model-call boundary (including truncation retries), the tool boundary,
the topology executor, and the terminal state the service records.
"""

from __future__ import annotations

import contextlib
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.llm_worker_base import LLMWorkerAgentBase
from src.agents.models import AgentResult, AgentTask
from src.agents.tools.registry import create_default_registry
from src.ai_brain.integration.execution_plan_topology import (
    ExecutionPlanTopologyExecutor,
)
from src.api.services.direct_execution_service import (
    DirectExecutionService,
    ExecutionStatus,
)
from src.core.budget import (
    BUDGET_LEDGER_CONTEXT_KEY,
    BudgetDimension,
    BudgetExceededError,
    BudgetLedger,
    ReportedUsage,
)
from src.core.contracts import (
    CollaborationMode,
    ExecutionBudget,
    RoutingEdge,
    RunStatus,
    WorkerAssignment,
)
from tests.test_direct_execution_no_retry_loop import (
    _AllocStub,
    _ComplexityAnalysisStub,
    _DecisionStub,
    _make_project,
)
from tests.test_execution_plan_conformance import _plan


def _budget(
    *,
    cost: str = "1",
    tokens: int = 100_000,
    tools: int = 5,
) -> ExecutionBudget:
    return ExecutionBudget(
        max_cost_usd=Decimal(cost),
        max_total_tokens=tokens,
        max_tool_invocations=tools,
        max_parallel_tasks=2,
        max_attempts_per_task=1,
        task_timeout_seconds=30,
    )


def _usage(tokens: int, cost: str = "0.01") -> ReportedUsage:
    return ReportedUsage(total_tokens=tokens, cost_usd=Decimal(cost))


def _with_budget(plan: Any, budget: ExecutionBudget) -> Any:
    decision = plan.routing_decision.model_copy(update={"budget": budget})
    return plan.model_copy(update={"routing_decision": decision})


class TestLedger:
    def test_refuses_call_that_does_not_fit_tokens(self) -> None:
        ledger = BudgetLedger(_budget(tokens=1000))
        with pytest.raises(BudgetExceededError) as caught:
            ledger.reserve_call(1001)
        assert caught.value.dimension is BudgetDimension.TOKENS

    def test_reservations_held_by_concurrent_calls_count(self) -> None:
        ledger = BudgetLedger(_budget(tokens=1000))
        ledger.reserve_call(600)
        with pytest.raises(BudgetExceededError):
            ledger.reserve_call(600)

    def test_settle_replaces_reservation_with_reported_usage(self) -> None:
        ledger = BudgetLedger(_budget(tokens=1000))
        reservation = ledger.reserve_call(900)
        ledger.settle(reservation, _usage(100))
        assert ledger.tokens_used == 100
        ledger.reserve_call(900)

    def test_refuses_once_cost_ceiling_cannot_hold_another_call(self) -> None:
        ledger = BudgetLedger(_budget(cost="0.015"))
        ledger.settle(ledger.reserve_call(10), _usage(10, "0.01"))
        with pytest.raises(BudgetExceededError) as caught:
            ledger.reserve_call(10)
        assert caught.value.dimension is BudgetDimension.COST

    def test_calls_in_flight_each_hold_the_largest_observed_call_cost(self) -> None:
        ledger = BudgetLedger(_budget(cost="0.025"))
        ledger.settle(ledger.reserve_call(10), _usage(10, "0.01"))
        ledger.reserve_call(10)  # 0.01 used + 0.01 held <= 0.025
        with pytest.raises(BudgetExceededError) as caught:
            ledger.reserve_call(10)  # a second in flight would hold 0.03
        assert caught.value.dimension is BudgetDimension.COST

    def test_overshoot_reported_after_the_fact_is_recorded(self) -> None:
        ledger = BudgetLedger(_budget(cost="0.005"))
        ledger.settle(ledger.reserve_call(10), _usage(10, "0.01"))
        assert ledger.exceeded is not None
        with pytest.raises(BudgetExceededError):
            ledger.ensure_not_exceeded()

    def test_token_overshoot_reported_after_the_fact_is_recorded(self) -> None:
        ledger = BudgetLedger(_budget(tokens=1000))
        ledger.settle(ledger.reserve_call(500), _usage(1500))
        assert ledger.exceeded is not None
        assert ledger.exceeded.dimension is BudgetDimension.TOKENS

    @pytest.mark.parametrize(
        "usage",
        [
            ReportedUsage(total_tokens=10, cost_usd=Decimal("NaN")),
            ReportedUsage(total_tokens=10, cost_usd=Decimal("Infinity")),
            ReportedUsage(total_tokens=10, cost_usd=Decimal("-1")),
            ReportedUsage(total_tokens=-5, cost_usd=Decimal("0.01")),
        ],
    )
    def test_malformed_usage_is_treated_as_unknown(self, usage: ReportedUsage) -> None:
        ledger = BudgetLedger(_budget())
        ledger.settle(ledger.reserve_call(700), usage)
        assert ledger.tokens_used == 700
        assert ledger.unknown_usage_calls == 1

    def test_unknown_usage_is_charged_the_reservation_not_zero(self) -> None:
        ledger = BudgetLedger(_budget())
        ledger.settle(ledger.reserve_call(700), None)
        assert ledger.tokens_used == 700
        assert ledger.unknown_usage_calls == 1

    def test_unknown_usage_fails_closed_on_cost(self) -> None:
        ledger = BudgetLedger(_budget())
        ledger.settle(ledger.reserve_call(10), None)
        with pytest.raises(BudgetExceededError) as caught:
            ledger.reserve_call(10)
        assert caught.value.dimension is BudgetDimension.COST

    def test_tool_invocations_are_capped(self) -> None:
        ledger = BudgetLedger(_budget(tools=2))
        ledger.charge_tool_invocation()
        ledger.charge_tool_invocation()
        with pytest.raises(BudgetExceededError) as caught:
            ledger.charge_tool_invocation()
        assert caught.value.dimension is BudgetDimension.TOOL_INVOCATIONS

    def test_exceeded_is_sticky(self) -> None:
        ledger = BudgetLedger(_budget(tokens=1000))
        with pytest.raises(BudgetExceededError):
            ledger.reserve_call(5000)
        with pytest.raises(BudgetExceededError):
            ledger.charge_tool_invocation()


class _Worker(LLMWorkerAgentBase):
    agent_type = "test_budget"

    def _build_prompt(self, query: str, task: AgentTask) -> str:
        return query


def _task(ledger: BudgetLedger | None) -> AgentTask:
    context = {BUDGET_LEDGER_CONTEXT_KEY: ledger} if ledger else {}
    return AgentTask(
        id="t", agent_type="test_budget", input_data={"query": "q"}, context=context
    )


def _route(responses: list[Any]) -> Any:
    router = MagicMock()
    router.route_and_generate = AsyncMock(side_effect=responses)
    return SimpleNamespace(router=router)


def _response(tokens: int, cost: float = 0.01, success: bool = True) -> Any:
    return SimpleNamespace(
        success=success, total_tokens=tokens, cost_estimate=cost, content="x"
    )


class TestModelCallBoundary:
    async def test_call_that_cannot_fit_never_reaches_the_provider(self) -> None:
        ledger = BudgetLedger(_budget(tokens=1000))
        route = _route([_response(10)])
        request = SimpleNamespace(prompt="p", max_tokens=2000)
        with pytest.raises(BudgetExceededError):
            await _Worker()._metered_route_and_generate(
                route, request, _task(ledger), {}
            )
        route.router.route_and_generate.assert_not_called()

    async def test_retries_draw_on_the_same_budget(self) -> None:
        ledger = BudgetLedger(_budget(tokens=3000))
        route = _route([_response(1000), _response(1000)])
        request = SimpleNamespace(prompt="p", max_tokens=1000)
        worker, task = _Worker(), _task(ledger)
        await worker._metered_route_and_generate(route, request, task, {})
        with pytest.raises(BudgetExceededError):
            # 1000 spent + a retry reserving 2002 > 3000
            retry = SimpleNamespace(prompt="p", max_tokens=2000)
            await worker._metered_route_and_generate(route, retry, task, {})
        assert route.router.route_and_generate.await_count == 1
        assert ledger.tokens_used == 1000

    async def test_response_without_usage_is_charged_the_reservation(self) -> None:
        ledger = BudgetLedger(_budget())
        route = _route([_response(0)])
        request = SimpleNamespace(prompt="p", max_tokens=1000)
        await _Worker()._metered_route_and_generate(route, request, _task(ledger), {})
        assert ledger.tokens_used >= 1000
        assert ledger.unknown_usage_calls == 1

    async def test_provider_error_settles_the_reservation(self) -> None:
        ledger = BudgetLedger(_budget())
        route = _route([RuntimeError("boom")])
        request = SimpleNamespace(prompt="p", max_tokens=1000)
        with pytest.raises(RuntimeError):
            await _Worker()._metered_route_and_generate(
                route, request, _task(ledger), {}
            )
        assert ledger.tokens_used >= 1000

    async def test_budget_error_is_not_converted_to_provider_unavailable(
        self,
    ) -> None:
        ledger = BudgetLedger(_budget(tokens=10))
        worker = _Worker()
        task = AgentTask(
            id="t",
            agent_type="test_budget",
            input_data={
                "query": "q",
                "provider_model_policy": {
                    "primary": {"provider": "gemini", "model": "gemini-2.5-pro"}
                },
            },
            context={BUDGET_LEDGER_CONTEXT_KEY: ledger},
        )
        with pytest.raises(BudgetExceededError):
            await worker._generate_with_routing("prompt", task)


class TestToolBoundary:
    async def test_invocation_beyond_ceiling_is_refused_before_the_tool_runs(
        self,
    ) -> None:
        registry = create_default_registry()
        ledger = BudgetLedger(_budget(tools=1))
        params = {"expression": "1+1"}
        await registry.execute("arithmetic", params, budget=ledger)
        with (
            patch.object(registry.boundary, "invoke", AsyncMock()) as invoke,
            pytest.raises(BudgetExceededError),
        ):
            await registry.execute("arithmetic", params, budget=ledger)
        invoke.assert_not_called()

    async def test_zero_tool_budget_refuses_every_call(self) -> None:
        registry = create_default_registry()
        with pytest.raises(BudgetExceededError):
            await registry.execute(
                "arithmetic",
                {"expression": "1+1"},
                budget=BudgetLedger(_budget(tools=0)),
            )

    async def test_budget_error_in_tool_follow_up_generation_propagates(self) -> None:
        registry = MagicMock()
        registry.execute = AsyncMock(
            return_value=SimpleNamespace(success=True, value=1, error=None)
        )
        worker = _Worker()
        call = SimpleNamespace(name="arithmetic", arguments={})
        with (
            patch.object(
                worker,
                "_generate_with_routing",
                AsyncMock(
                    side_effect=BudgetExceededError(
                        BudgetDimension.TOKENS, limit=1, used=1, requested=1
                    )
                ),
            ),
            pytest.raises(BudgetExceededError),
        ):
            await worker._execute_model_tool_call(
                _task(BudgetLedger(_budget())), "p", registry, call, None
            )

    async def test_worker_propagates_budget_error_instead_of_a_tool_failure(
        self,
    ) -> None:
        registry = MagicMock()
        registry.execute = AsyncMock(
            side_effect=BudgetExceededError(
                BudgetDimension.TOOL_INVOCATIONS, limit=0, used=0, requested=1
            )
        )
        call = SimpleNamespace(name="arithmetic", arguments={})
        with pytest.raises(BudgetExceededError):
            await _Worker()._execute_model_tool_call(
                _task(BudgetLedger(_budget(tools=0))), "p", registry, call, None
            )


def _executor(worker_executor: Any) -> ExecutionPlanTopologyExecutor:
    return ExecutionPlanTopologyExecutor(
        worker_executor=worker_executor, capability_checker=lambda _: True
    )


def _task_for_plan() -> AgentTask:
    return AgentTask(id="run", agent_type="execution_plan", input_data={})


class TestTopologyExecutor:
    def test_plan_that_cannot_fit_is_refused_before_dispatch(self) -> None:
        worker_executor = AsyncMock()
        plan = _with_budget(_plan(), _budget(tokens=1000))
        with pytest.raises(BudgetExceededError):
            _executor(worker_executor).admit(plan)
        worker_executor.assert_not_called()

    async def test_run_stops_before_the_call_that_would_exceed(self) -> None:
        workers = (
            WorkerAssignment(
                worker_id=name,
                worker_type="synthesis",
                objective=name,
                output_schema={},
                permission_scopes=(),
                tool_allowlist=(),
            )
            for name in ("first", "second")
        )
        plan = _with_budget(
            _plan(
                mode=CollaborationMode.HIERARCHICAL,
                workers=tuple(workers),
                edges=(
                    RoutingEdge(
                        source_node_id="root",
                        target_node_id="first",
                        relation="delegates",
                    ),
                    RoutingEdge(
                        source_node_id="first",
                        target_node_id="second",
                        relation="depends_on",
                    ),
                    RoutingEdge(
                        source_node_id="root",
                        target_node_id="second",
                        relation="delegates",
                    ),
                ),
            ),
            _budget(tokens=5000),
        )
        executed: list[str] = []

        async def worker_executor(
            worker: WorkerAssignment, task: AgentTask
        ) -> AgentResult:
            ledger = task.context[BUDGET_LEDGER_CONTEXT_KEY]
            reservation = ledger.reserve_call(3000)
            ledger.settle(reservation, _usage(3000))
            executed.append(str(worker.worker_id))
            return AgentResult(
                task_id=task.id,
                status="success",
                output={},
                confidence=1.0,
                execution_time=0.0,
            )

        with pytest.raises(BudgetExceededError):
            await _executor(worker_executor).execute(plan, _task_for_plan())
        assert executed == ["first"]

    async def test_no_worker_starts_once_the_ledger_is_exceeded(self) -> None:
        ledger = BudgetLedger(_budget(tokens=1000))
        with pytest.raises(BudgetExceededError):
            ledger.reserve_call(5000)
        worker_executor = AsyncMock()
        task = AgentTask(
            id="run",
            agent_type="execution_plan",
            input_data={},
            context={BUDGET_LEDGER_CONTEXT_KEY: ledger},
        )
        with pytest.raises(BudgetExceededError):
            await _executor(worker_executor).execute(_plan(), task)
        worker_executor.assert_not_called()

    async def test_overshoot_settled_by_a_successful_worker_fails_the_run(
        self,
    ) -> None:
        async def worker_executor(
            worker: WorkerAssignment, task: AgentTask
        ) -> AgentResult:
            ledger = task.context[BUDGET_LEDGER_CONTEXT_KEY]
            ledger.settle(ledger.reserve_call(10), _usage(10**7))
            return AgentResult(
                task_id=task.id,
                status="success",
                output={},
                confidence=1.0,
                execution_time=0.0,
            )

        one_worker = (
            WorkerAssignment(
                worker_id="only",
                worker_type="synthesis",
                objective="only",
                output_schema={},
                permission_scopes=(),
                tool_allowlist=(),
            ),
        )
        plan = _plan(
            mode=CollaborationMode.DIRECT, workers=one_worker, supervisor_id=None
        )
        with pytest.raises(BudgetExceededError):
            await _executor(worker_executor).execute(plan, _task_for_plan())

    async def test_worker_that_swallows_the_error_still_fails_the_run(self) -> None:
        plan = _plan()

        async def worker_executor(
            worker: WorkerAssignment, task: AgentTask
        ) -> AgentResult:
            ledger = task.context[BUDGET_LEDGER_CONTEXT_KEY]
            with contextlib.suppress(BudgetExceededError):
                ledger.reserve_call(10**9)
            return AgentResult(
                task_id=task.id,
                status="failed",
                output={},
                confidence=0.0,
                execution_time=0.0,
            )

        with pytest.raises(BudgetExceededError):
            await _executor(worker_executor).execute(plan, _task_for_plan())

    async def test_supplied_ledger_is_shared_not_replaced(self) -> None:
        ledger = BudgetLedger(_plan().routing_decision.budget)
        seen: list[Any] = []

        async def worker_executor(
            worker: WorkerAssignment, task: AgentTask
        ) -> AgentResult:
            seen.append(task.context[BUDGET_LEDGER_CONTEXT_KEY])
            return AgentResult(
                task_id=task.id,
                status="success",
                output={},
                confidence=1.0,
                execution_time=0.0,
            )

        task = AgentTask(
            id="run",
            agent_type="execution_plan",
            input_data={},
            context={BUDGET_LEDGER_CONTEXT_KEY: ledger},
        )
        await _executor(worker_executor).execute(_plan(), task)
        assert seen and all(item is ledger for item in seen)


def _service_with_bridge(bridge_effect: Any) -> DirectExecutionService:
    router = MagicMock()
    router.route = AsyncMock(
        return_value=_DecisionStub(_AllocStub(), _ComplexityAnalysisStub())
    )
    bridge = MagicMock()
    bridge.execute_execution_plan = AsyncMock(side_effect=bridge_effect)
    return DirectExecutionService(
        masr_router=router, supervisor_bridge=bridge, event_publisher=None
    )


class TestServiceTerminalState:
    async def test_budget_exceeded_is_terminal_persisted_and_not_retried(
        self,
    ) -> None:
        error = BudgetExceededError(
            BudgetDimension.TOKENS, limit=1000, used=900, requested=500
        )
        service = _service_with_bridge(error)
        project = _make_project()
        status = ExecutionStatus(
            execution_id="exec-b", project_id=str(project.id), status="pending"
        )
        persisted: list[dict[str, Any]] = []

        async def record(_status: Any, **kwargs: Any) -> Any:
            persisted.append(kwargs)
            return MagicMock()

        with (
            patch.object(service, "_persist_transition", side_effect=record),
            patch.object(service, "_settle_terminal_state"),
            pytest.raises(BudgetExceededError),
        ):
            await service._execute_research_workflow(
                project,
                status,
                {},
                routing_decision=_DecisionStub(_AllocStub(), _ComplexityAnalysisStub()),
                execution_plan=_plan(),
            )

        assert service.supervisor_bridge.execute_execution_plan.await_count == 1
        terminal = persisted[-1]
        assert terminal["run_target"] is RunStatus.FAILED
        assert terminal["event_type"] == "run.budget_exceeded"
        assert terminal["payload"]["dimension"] == "tokens"
        assert status.failure_code == "BUDGET_EXCEEDED"

    async def test_workflow_attempts_share_one_ledger(self) -> None:
        service = _service_with_bridge(RuntimeError("provider down"))
        project = _make_project()
        status = ExecutionStatus(
            execution_id="exec-r", project_id=str(project.id), status="pending"
        )
        contexts: list[Any] = []

        async def capture(_plan: Any, task: AgentTask) -> Any:
            contexts.append(task.context[BUDGET_LEDGER_CONTEXT_KEY])
            raise RuntimeError("provider down")

        service.supervisor_bridge.execute_execution_plan = capture
        for _ in range(2):
            with (
                patch.object(service, "_record_attempt_failure", AsyncMock()),
                patch.object(service, "_persist_transition", AsyncMock()),
                contextlib.suppress(RuntimeError),
            ):
                await service._execute_research_workflow.__wrapped__(
                    service,
                    project,
                    status,
                    {},
                    routing_decision=_DecisionStub(
                        _AllocStub(), _ComplexityAnalysisStub()
                    ),
                    execution_plan=_plan(),
                )
        assert len(contexts) == 2 and contexts[0] is contexts[1]
