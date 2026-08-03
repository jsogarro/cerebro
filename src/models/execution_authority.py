"""Trusted compiler inputs for routed execution authority."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.core.contracts import (
    ExecutionBudget,
    ProviderModelPolicy,
    RoutingEdge,
    RoutingPolicy,
    Run,
    WorkerAssignment,
    WorkflowDefinition,
)


class ExecutionAuthorityReference(BaseModel):
    """Opaque public handle for a trusted authority record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    authority_id: str = Field(min_length=1, max_length=255)
    authority_version: str = Field(min_length=1, max_length=100)


@dataclass(frozen=True)
class ExecutionAuthorityBinding:
    """Complete immutable authority supplied only by a trusted resolver."""

    authority_id: str
    authority_version: str
    run: Run
    workflow_definition: WorkflowDefinition
    routing_policy: RoutingPolicy
    domains: tuple[str, ...]
    supervisor_id: str | None
    supervisor_type: str | None
    workers: tuple[WorkerAssignment, ...]
    edges: tuple[RoutingEdge, ...]
    provider_model_policy: ProviderModelPolicy
    budget: ExecutionBudget
    stop_conditions: tuple[str, ...]
    evaluator_requirements: tuple[str, ...]
    deadline: datetime
    plan_id_factory: Callable[[], str]
    clock: Callable[[], datetime]

    @classmethod
    def create_for_test(
        cls,
        *,
        authority_id: str,
        authority_version: str,
        run_id: str,
        workflow_definition_id: str,
        routing_policy_id: str,
        strategy: str,
        collaboration_mode: str,
        domains: tuple[str, ...],
        supervisor_id: str | None,
        supervisor_type: str | None,
        workers: tuple[WorkerAssignment, ...],
        edges: tuple[RoutingEdge, ...],
        provider_model_policy: ProviderModelPolicy,
        budget: ExecutionBudget,
        stop_conditions: tuple[str, ...],
        evaluator_requirements: tuple[str, ...],
        deadline: datetime,
        compiled_at: datetime,
    ) -> "ExecutionAuthorityBinding":
        """Build a complete explicit binding for compiler tests only."""
        from src.core.contracts import (
            RoutingPolicy,
            Run,
            RunStatus,
            WorkflowControlMode,
            WorkflowDefinition,
        )

        run = Run(
            run_id=run_id,
            tenant_id="tenant-1",
            workflow_definition_id=workflow_definition_id,
            workflow_definition_version="1",
            routing_policy_id=routing_policy_id,
            routing_policy_version="1",
            idempotency_key="key-1",
            requested_by="user-1",
            status=RunStatus.CREATED,
            created_at=compiled_at,
            updated_at=compiled_at,
        )
        workflow = WorkflowDefinition(
            workflow_definition_id=workflow_definition_id,
            workflow_version="1",
            name="research",
            description="research",
            control_mode=WorkflowControlMode.PREDEFINED,
            task_types=("research",),
            input_schema={},
            output_schema={},
            default_routing_policy_id=routing_policy_id,
            default_routing_policy_version="1",
            created_at=compiled_at,
        )
        policy = RoutingPolicy(
            routing_policy_id=routing_policy_id,
            routing_policy_version="1",
            strategy=strategy,
            collaboration_mode=collaboration_mode,
            worker_types=tuple(worker.worker_type for worker in workers),
            max_parallel_tasks=budget.max_parallel_tasks,
            max_attempts_per_task=budget.max_attempts_per_task,
            task_timeout_seconds=budget.task_timeout_seconds,
            provider_allowlist=provider_model_policy.provider_allowlist,
            model_allowlist=provider_model_policy.model_allowlist,
        )
        return cls(
            authority_id=authority_id,
            authority_version=authority_version,
            run=run,
            workflow_definition=workflow,
            routing_policy=policy,
            domains=domains,
            supervisor_id=supervisor_id,
            supervisor_type=supervisor_type,
            workers=workers,
            edges=edges,
            provider_model_policy=provider_model_policy,
            budget=budget,
            stop_conditions=stop_conditions,
            evaluator_requirements=evaluator_requirements,
            deadline=deadline,
            plan_id_factory=lambda: "plan-1",
            clock=lambda: compiled_at,
        )
