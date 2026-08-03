"""Generic, fail-closed execution of an immutable v1 execution plan."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from src.agents.llm_worker_base import PlanProviderUnavailableError
from src.agents.models import AgentResult, AgentTask
from src.core.contracts import (
    CollaborationMode,
    ExecutionPlan,
    FallbackMode,
    WorkerAssignment,
)

from .execution_plan_observability import (
    record_provider_unavailable,
    record_topology_admitted,
    record_topology_rejected,
)


class ExecutionTopologyUnsupportedError(ValueError):
    """A compiled plan cannot be executed by the supported v1 topology."""

    code = "EXECUTION_TOPOLOGY_UNSUPPORTED"


WorkerExecutor = Callable[[WorkerAssignment, AgentTask], Awaitable[AgentResult]]
CapabilityChecker = Callable[[str], bool]


@dataclass(frozen=True)
class PlanExecutionResult:
    """Results keyed by stable, plan-local worker identifiers."""

    worker_results: dict[str, AgentResult]

    @property
    def workers_used(self) -> int:
        return len(self.worker_results)

    @property
    def output(self) -> dict[str, Any]:
        return {
            worker_id: result.output
            for worker_id, result in self.worker_results.items()
        }


class ExecutionPlanTopologyExecutor:
    """Admit and run only the exact generic topology shapes supported in v1."""

    def __init__(
        self,
        *,
        worker_executor: WorkerExecutor,
        capability_checker: CapabilityChecker,
    ) -> None:
        self._worker_executor = worker_executor
        self._capability_checker = capability_checker

    def admit(self, plan: ExecutionPlan) -> None:
        """Reject unsupported authority synchronously, before any dispatch."""

        decision = plan.routing_decision
        unsupported = self._unsupported_reason(plan)
        if unsupported is not None:
            error = ExecutionTopologyUnsupportedError(unsupported)
            record_topology_rejected(plan, error.code, unsupported)
            raise error
        for worker in decision.workers:
            if not self._capability_checker(str(worker.worker_type)):
                reason = f"worker capability {worker.worker_type!r} is unavailable"
                error = ExecutionTopologyUnsupportedError(reason)
                record_topology_rejected(plan, error.code, reason)
                raise error
        record_topology_admitted(plan)

    async def execute(
        self,
        plan: ExecutionPlan,
        task: AgentTask,
    ) -> PlanExecutionResult:
        """Execute plan-local worker instances without consulting MASR or supervisors."""

        self.admit(plan)
        decision = plan.routing_decision
        workers = {str(worker.worker_id): worker for worker in decision.workers}
        dependencies = self._dependencies(plan)
        completed: dict[str, AgentResult] = {}
        pending = set(workers)
        semaphore = asyncio.Semaphore(decision.budget.max_parallel_tasks)

        async def execute_worker(worker: WorkerAssignment) -> AgentResult:
            worker_task = AgentTask(
                id=f"{task.id}:{worker.worker_id}",
                agent_type=str(worker.worker_type),
                input_data={
                    **task.input_data,
                    "execution_plan_id": str(plan.execution_plan_id),
                    "worker_id": str(worker.worker_id),
                    "objective": worker.objective,
                    "output_schema": dict(worker.output_schema),
                    "permission_scopes": list(worker.permission_scopes),
                    "tool_allowlist": list(worker.tool_allowlist),
                    "provider_model_policy": {
                        "primary": {
                            "provider": str(
                                decision.provider_model_policy.primary.provider
                            ),
                            "model": str(decision.provider_model_policy.primary.model),
                        },
                    },
                },
                context=task.context,
                timeout=decision.budget.task_timeout_seconds,
                priority=task.priority,
            )
            async with semaphore:
                return await self._worker_executor(worker, worker_task)

        while pending:
            ready = sorted(
                worker_id
                for worker_id in pending
                if dependencies[worker_id].issubset(completed)
            )
            if not ready:  # Defensive: admission has already proved acyclicity.
                raise ExecutionTopologyUnsupportedError(
                    "topology has no runnable worker"
                )
            try:
                results = await asyncio.gather(
                    *(execute_worker(workers[worker_id]) for worker_id in ready)
                )
            except PlanProviderUnavailableError as exc:
                record_provider_unavailable(
                    plan,
                    str(decision.provider_model_policy.primary.provider),
                    str(exc),
                )
                raise
            completed.update(zip(ready, results, strict=True))
            pending.difference_update(ready)

        return PlanExecutionResult(worker_results=completed)

    def _unsupported_reason(self, plan: ExecutionPlan) -> str | None:
        decision = plan.routing_decision
        if len(decision.domains) != 1:
            return "v1 topology requires exactly one domain"
        if decision.evaluator_requirements:
            return "v1 topology does not support evaluator requirements"
        if decision.stop_conditions != ("complete",):
            return "v1 topology requires the complete stop condition"
        if decision.budget.max_attempts_per_task > 1:
            return "v1 topology does not support retries"
        if (
            decision.provider_model_policy.fallback_mode != FallbackMode.FAIL_CLOSED
            or decision.provider_model_policy.fallbacks
        ):
            return "v1 topology does not support provider substitution or fallback"

        mode = decision.collaboration_mode
        if mode in {CollaborationMode.DIRECT, CollaborationMode.FAST_PATH}:
            if decision.supervisor_id is not None or decision.edges:
                return "direct topology requires one worker with no supervisor or edges"
            return None
        if mode == CollaborationMode.PARALLEL:
            if decision.supervisor_id is not None or decision.edges:
                return "parallel topology requires no supervisor or edges"
            return None
        if mode != CollaborationMode.HIERARCHICAL:
            return f"collaboration mode {mode.value!r} is unsupported"
        return self._validate_hierarchical(plan)

    def _validate_hierarchical(self, plan: ExecutionPlan) -> str | None:
        decision = plan.routing_decision
        assert decision.supervisor_id is not None
        worker_ids = {str(worker.worker_id) for worker in decision.workers}
        root = str(decision.supervisor_id)
        incoming = dict.fromkeys({root, *worker_ids}, 0)
        adjacency: dict[str, set[str]] = {node: set() for node in incoming}
        for edge in decision.edges:
            if edge.relation not in {"delegates", "depends_on"}:
                return f"edge relation {edge.relation!r} is unsupported"
            source, target = str(edge.source_node_id), str(edge.target_node_id)
            adjacency[source].add(target)
            incoming[target] += 1
        if incoming[root] != 0 or any(incoming[node] == 0 for node in worker_ids):
            return "hierarchical topology must have one supervisor root"

        visited: set[str] = set()
        active: set[str] = set()

        def visit(node: str) -> bool:
            if node in active:
                return False
            if node in visited:
                return True
            active.add(node)
            if not all(visit(child) for child in adjacency[node]):
                return False
            active.remove(node)
            visited.add(node)
            return True

        if not visit(root) or visited != set(adjacency):
            return "hierarchical topology must be a rooted worker DAG"
        return None

    @staticmethod
    def _dependencies(plan: ExecutionPlan) -> dict[str, set[str]]:
        decision = plan.routing_decision
        worker_ids = {str(worker.worker_id) for worker in decision.workers}
        dependencies: dict[str, set[str]] = {
            worker_id: set() for worker_id in worker_ids
        }
        for edge in decision.edges:
            source, target = str(edge.source_node_id), str(edge.target_node_id)
            if source in worker_ids and target in worker_ids:
                dependencies[target].add(source)
        return dependencies
