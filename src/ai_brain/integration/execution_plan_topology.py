"""Generic, fail-closed execution of an immutable v1 execution plan."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from src.agents.llm_worker_base import (
    RESERVED_RUNTIME_INPUT_KEYS,
    UPSTREAM_PAYLOAD_LIMIT_BYTES,
    UPSTREAM_RESULTS_KEY,
    PlanProviderUnavailableError,
    render_upstream_evidence,
)
from src.agents.models import AgentResult, AgentTask
from src.core.budget import (
    BUDGET_LEDGER_CONTEXT_KEY,
    BudgetExceededError,
    BudgetLedger,
    check_plan_fits,
)
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
UpstreamChecker = Callable[[str, int], bool]
"""``(worker_type, direct_parent_count)`` -> can that consumer use the data."""

# Workflow definitions at an earlier major version ordered workers by edges
# without delivering data; replaying them under the data contract would
# silently reinterpret them.
MIN_DATA_HANDOFF_WORKFLOW_MAJOR = 2


_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
_REF_KEYWORDS = ("$ref", "$dynamicRef")


class WorkerOutcomeStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    INVALID_OUTPUT = "invalid_output"


# Legacy ``AgentResult.status`` spellings that mean a timeout.
_TIMEOUT_STATUSES = frozenset({"timeout", "timed_out", "timed-out"})


@dataclass(frozen=True)
class WorkerOutcome:
    """A worker's normalized result: only ``SUCCESS`` counts toward a run."""

    worker_id: str
    status: WorkerOutcomeStatus
    code: str | None = None
    reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status is WorkerOutcomeStatus.SUCCESS


def _plain(node: Any) -> Any:
    """Contract schemas are frozen mappings/tuples; the validator wants JSON types."""

    if isinstance(node, Mapping):
        return {key: _plain(value) for key, value in node.items()}
    if isinstance(node, (list, tuple)):
        return [_plain(item) for item in node]
    return node


def _local_refs_only(node: Any) -> bool:
    if isinstance(node, dict):
        for key, value in node.items():
            if (
                key in _REF_KEYWORDS
                and isinstance(value, str)
                and not value.startswith("#")
            ):
                return False
            if not _local_refs_only(value):
                return False
    elif isinstance(node, list):
        return all(_local_refs_only(item) for item in node)
    return True


def schema_rejection_reason(schema: dict[str, Any]) -> str | None:
    """Why a worker output schema cannot be used, or ``None`` when it can."""

    if not schema:
        return None
    if schema.get("$schema", _DRAFT_2020_12) != _DRAFT_2020_12:
        return "output schema must use JSON Schema draft 2020-12"
    if not _local_refs_only(schema):
        return "output schema may only reference local definitions"
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        return f"output schema is invalid: {error.message}"
    return None


def _supports_data_handoff(version: str) -> bool:
    # ASCII only: str.isdigit() accepts "²" (and int() rejects it) and treats
    # other-script digits as numbers.
    if not re.fullmatch(r"(\d+)(\.\d+)*", version, re.ASCII):
        return False
    return int(version.split(".", 1)[0]) >= MIN_DATA_HANDOFF_WORKFLOW_MAJOR


def classify_worker_result(
    worker: WorkerAssignment, result: AgentResult
) -> WorkerOutcome:
    """Normalize a legacy result; anything not provably successful fails."""

    worker_id = str(worker.worker_id)
    status = str(result.status).lower()
    if status in _TIMEOUT_STATUSES:
        return WorkerOutcome(worker_id, WorkerOutcomeStatus.TIMED_OUT, "WORKER_TIMEOUT")
    if status == "failed":
        return WorkerOutcome(worker_id, WorkerOutcomeStatus.FAILED, "WORKER_FAILED")
    if status != "success":
        return WorkerOutcome(
            worker_id, WorkerOutcomeStatus.FAILED, "WORKER_STATUS_UNKNOWN"
        )
    # An agent with nothing to work on reports "success" at zero confidence;
    # that is an abstention, not a finding.
    if result.confidence <= 0:
        return WorkerOutcome(
            worker_id, WorkerOutcomeStatus.INVALID_OUTPUT, "ZERO_CONFIDENCE"
        )
    if worker.output_schema:
        # Report where the output is wrong, never its content.
        error = next(
            iter(
                Draft202012Validator(_plain(worker.output_schema)).iter_errors(
                    result.output
                )
            ),
            None,
        )
        if error is not None:
            where = "/".join(str(part) for part in error.absolute_path) or "<root>"
            return WorkerOutcome(
                worker_id,
                WorkerOutcomeStatus.INVALID_OUTPUT,
                "OUTPUT_SCHEMA_VIOLATION",
                f"{where} violates {error.validator!r}",
            )
    return WorkerOutcome(worker_id, WorkerOutcomeStatus.SUCCESS)


@dataclass(frozen=True)
class PlanExecutionResult:
    """Results keyed by stable, plan-local worker identifiers."""

    worker_results: dict[str, AgentResult]
    outcomes: dict[str, WorkerOutcome] = field(default_factory=dict)
    blocked_worker_ids: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return not self.blocked_worker_ids and all(
            outcome.succeeded for outcome in self.outcomes.values()
        )

    @property
    def failures(self) -> list[dict[str, str | None]]:
        """Stable, content-free failure records for events and diagnostics."""

        failed = [
            {
                "worker_id": outcome.worker_id,
                "status": outcome.status.value,
                "code": outcome.code,
                "reason": outcome.reason,
            }
            for outcome in self.outcomes.values()
            if not outcome.succeeded
        ]
        return failed + [
            {"worker_id": worker_id, "status": "blocked", "code": "UPSTREAM_FAILED"}
            for worker_id in self.blocked_worker_ids
        ]

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
        upstream_checker: UpstreamChecker | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._clock = clock
        self._worker_executor = worker_executor
        self._capability_checker = capability_checker
        self._upstream_checker = upstream_checker

    def admit(self, plan: ExecutionPlan) -> None:
        """Reject unsupported authority synchronously, before any dispatch."""

        decision = plan.routing_decision
        unsupported = self._unsupported_reason(plan)
        if unsupported is not None:
            error = ExecutionTopologyUnsupportedError(unsupported)
            record_topology_rejected(plan, error.code, unsupported)
            raise error
        dependencies = self._dependencies(plan)
        if any(dependencies.values()) and not _supports_data_handoff(
            plan.workflow_definition_version
        ):
            reason = (
                f"workflow version {plan.workflow_definition_version!r} predates "
                "dependency data handoff"
            )
            error = ExecutionTopologyUnsupportedError(reason)
            record_topology_rejected(plan, error.code, reason)
            raise error
        for worker in decision.workers:
            parents = len(dependencies[str(worker.worker_id)])
            if (
                parents
                and self._upstream_checker is not None
                and not self._upstream_checker(str(worker.worker_type), parents)
            ):
                reason = (
                    f"worker {worker.worker_id!r} ({worker.worker_type!r}) cannot "
                    f"consume {parents} upstream result(s)"
                )
                error = ExecutionTopologyUnsupportedError(reason)
                record_topology_rejected(plan, error.code, reason)
                raise error
            schema_problem = schema_rejection_reason(_plain(worker.output_schema))
            if schema_problem is not None:
                reason = f"worker {worker.worker_id!r}: {schema_problem}"
                error = ExecutionTopologyUnsupportedError(reason)
                record_topology_rejected(plan, error.code, reason)
                raise error
            if not self._capability_checker(str(worker.worker_type)):
                reason = f"worker capability {worker.worker_type!r} is unavailable"
                error = ExecutionTopologyUnsupportedError(reason)
                record_topology_rejected(plan, error.code, reason)
                raise error
        try:
            check_plan_fits(decision.budget, len(decision.workers))
        except BudgetExceededError as error:
            record_topology_rejected(plan, error.code, str(error))
            raise
        record_topology_admitted(plan)

    async def execute(
        self,
        plan: ExecutionPlan,
        task: AgentTask,
    ) -> PlanExecutionResult:
        """Execute plan-local worker instances without consulting MASR or supervisors."""

        self.admit(plan)
        decision = plan.routing_decision
        # The run's own ledger when the caller supplies one, so a workflow
        # retry draws on the budget already spent instead of a fresh one.
        ledger = task.context.get(BUDGET_LEDGER_CONTEXT_KEY)
        if ledger is None:
            ledger = BudgetLedger(decision.budget)
        workers = {str(worker.worker_id): worker for worker in decision.workers}
        dependencies = self._dependencies(plan)
        completed: dict[str, AgentResult] = {}
        outcomes: dict[str, WorkerOutcome] = {}
        pending = set(workers)
        preflight_failures: dict[str, WorkerOutcome] = {}
        semaphore = asyncio.Semaphore(decision.budget.max_parallel_tasks)

        async def execute_worker(worker: WorkerAssignment) -> AgentResult | None:
            worker_id = str(worker.worker_id)
            upstream = {
                parent: {
                    "worker_id": parent,
                    "task_id": completed[parent].task_id,
                    "execution_plan_id": str(plan.execution_plan_id),
                    "output": completed[parent].output,
                }
                for parent in sorted(dependencies[worker_id])
            }
            sent = sum(
                len(render_upstream_evidence(parent, entry).encode())
                for parent, entry in upstream.items()
            )
            if sent > UPSTREAM_PAYLOAD_LIMIT_BYTES:
                preflight_failures[worker_id] = WorkerOutcome(
                    worker_id,
                    WorkerOutcomeStatus.FAILED,
                    "UPSTREAM_PAYLOAD_TOO_LARGE",
                    f"upstream data exceeds {UPSTREAM_PAYLOAD_LIMIT_BYTES} bytes",
                )
                return None
            client_input = {
                key: value
                for key, value in task.input_data.items()
                if key not in RESERVED_RUNTIME_INPUT_KEYS
            }
            if upstream:
                client_input[UPSTREAM_RESULTS_KEY] = upstream
            worker_task = AgentTask(
                id=f"{task.id}:{worker.worker_id}",
                agent_type=str(worker.worker_type),
                input_data={
                    **client_input,
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
                context={**task.context, BUDGET_LEDGER_CONTEXT_KEY: ledger},
                timeout=decision.budget.task_timeout_seconds,
                priority=task.priority,
            )
            # One budget covers queueing and the call: a worker waiting on the
            # semaphore cannot outlive the plan deadline.
            remaining = (plan.deadline - self._clock()).total_seconds()
            task_limit = decision.budget.task_timeout_seconds
            code = "DEADLINE_EXCEEDED" if remaining <= task_limit else "TASK_TIMEOUT"
            if remaining > 0:
                try:
                    async with asyncio.timeout(min(remaining, task_limit)) as limit:
                        async with semaphore:
                            ledger.ensure_not_exceeded()
                            result = await self._worker_executor(worker, worker_task)
                except Exception:
                    if not limit.expired():  # raised by the worker, not our limit
                        raise
                else:
                    # A worker that swallows the cancellation must not turn
                    # a late result into a success.
                    if not limit.expired():
                        return result
            preflight_failures[worker_id] = WorkerOutcome(
                worker_id, WorkerOutcomeStatus.TIMED_OUT, code
            )
            return None

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
            # A worker may swallow the error and return a failed result; the
            # ledger still knows a ceiling was crossed.
            ledger.ensure_not_exceeded()
            pending.difference_update(ready)
            for worker_id, result in zip(ready, results, strict=True):
                if result is None:
                    outcomes[worker_id] = preflight_failures[worker_id]
                    continue
                completed[worker_id] = result
                outcomes[worker_id] = classify_worker_result(workers[worker_id], result)
            # A failed worker leaves its dependents unscheduled, so a worker only
            # ever sees parents that succeeded.
            if not all(outcomes[worker_id].succeeded for worker_id in ready):
                break

        return PlanExecutionResult(
            worker_results=completed,
            outcomes=outcomes,
            blocked_worker_ids=tuple(sorted(pending)),
        )

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
