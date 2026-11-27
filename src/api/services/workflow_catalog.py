"""Trusted catalogue of the workflows a run may be launched from.

The catalogue is deployment configuration, not request input. Each entry pairs
a versioned ``WorkflowDefinition`` with the ``RoutingPolicy`` it may run under
and the budget ceiling a launch may narrow but never widen. Validation needs no
model call, so an invalid enabled entry fails startup before any run exists.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Iterable
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache

from src.ai_brain.integration.execution_plan_topology import schema_rejection_reason
from src.ai_brain.router.routing_types import WorkflowShape
from src.core.contracts import (
    COLLABORATION_MODE_SUPPORT,
    CollaborationMode,
    CollaborationModeSupport,
    ExecutionBudget,
    RoutingPolicy,
    WorkflowControlMode,
    WorkflowDefinition,
)
from src.core.contracts.base import ContractId, ContractModel
from src.core.kernel.component_keys import AGENT_KEYS

DEFAULT_RESEARCH_WORKFLOW_ID = "research.single-worker"
DEFAULT_RESEARCH_WORKFLOW_VERSION = "1"


class WorkflowCatalogError(ValueError):
    """An enabled catalogue entry is not executable; the message names ids only."""


class WorkflowCatalogEntry(ContractModel):
    """One launchable workflow and the only execution shape it may take."""

    workflow: WorkflowDefinition
    routing_policy: RoutingPolicy
    domains: tuple[ContractId, ...]
    budget_ceiling: ExecutionBudget
    enabled: bool = True

    def shape(self) -> WorkflowShape:
        """The execution shape routing must produce for this entry."""

        policy = self.routing_policy
        return WorkflowShape(
            strategy=policy.strategy,
            domains=tuple(self.domains),
            collaboration_mode=CollaborationMode(policy.collaboration_mode),
            worker_types=tuple(policy.worker_types),
            provider_allowlist=tuple(policy.provider_allowlist),
        )

    @property
    def key(self) -> tuple[str, str]:
        return self.workflow.workflow_definition_id, self.workflow.workflow_version


def _check_executable(entry: WorkflowCatalogEntry, registered: Collection[str]) -> None:
    label = "/".join(entry.key)
    policy, workflow, budget = (
        entry.routing_policy,
        entry.workflow,
        entry.budget_ceiling,
    )
    if (
        workflow.default_routing_policy_id,
        workflow.default_routing_policy_version,
    ) != (policy.routing_policy_id, policy.routing_policy_version):
        raise WorkflowCatalogError(f"{label}: default routing policy mismatch")
    try:
        mode = CollaborationMode(policy.collaboration_mode)
    except ValueError:
        raise WorkflowCatalogError(f"{label}: unknown collaboration mode") from None
    if COLLABORATION_MODE_SUPPORT[mode] is not CollaborationModeSupport.IMPLEMENTED:
        raise WorkflowCatalogError(f"{label}: unsupported collaboration mode")
    if len(entry.domains) != 1 or not policy.worker_types:
        raise WorkflowCatalogError(f"{label}: needs one domain and a worker")
    if mode in {CollaborationMode.FAST_PATH, CollaborationMode.DIRECT} and (
        len(policy.worker_types) != 1
    ):
        raise WorkflowCatalogError(f"{label}: {mode.value} takes exactly one worker")
    unknown = set(policy.worker_types) - set(registered)
    if unknown:
        raise WorkflowCatalogError(f"{label}: unregistered workers {sorted(unknown)}")
    if not policy.provider_allowlist:
        raise WorkflowCatalogError(f"{label}: provider allowlist is empty")
    if budget.max_attempts_per_task != 1 or policy.max_attempts_per_task != 1:
        raise WorkflowCatalogError(f"{label}: retries are not admitted")
    if (
        budget.max_parallel_tasks > len(policy.worker_types)
        or budget.max_parallel_tasks != policy.max_parallel_tasks
        or budget.task_timeout_seconds != policy.task_timeout_seconds
    ):
        raise WorkflowCatalogError(f"{label}: budget disagrees with policy")
    reason = schema_rejection_reason(workflow.model_dump(mode="json")["output_schema"])
    if reason is not None:
        raise WorkflowCatalogError(f"{label}: {reason}")


class ExecutableWorkflowCatalog:
    """Immutable, hash-pinned set of validated entries."""

    def __init__(
        self,
        entries: Iterable[WorkflowCatalogEntry],
        *,
        registered_workers: Collection[str],
    ) -> None:
        self._entries: dict[tuple[str, str], WorkflowCatalogEntry] = {}
        for entry in entries:
            if entry.key in self._entries:
                raise WorkflowCatalogError(f"{'/'.join(entry.key)}: duplicate entry")
            if entry.enabled:
                _check_executable(entry, registered_workers)
            self._entries[entry.key] = entry
        digest = hashlib.sha256()
        for key in sorted(self._entries):
            digest.update(self._entries[key].canonical_json().encode())
        self.catalog_hash = digest.hexdigest()

    def enabled(self) -> tuple[WorkflowCatalogEntry, ...]:
        return tuple(e for _, e in sorted(self._entries.items()) if e.enabled)

    def resolve(
        self, workflow_id: str, workflow_version: str | None
    ) -> WorkflowCatalogEntry | None:
        """Return the enabled entry; an omitted version means the newest enabled."""

        matches = [
            entry
            for entry in self.enabled()
            if entry.workflow.workflow_definition_id == workflow_id
            and workflow_version in (None, entry.workflow.workflow_version)
        ]
        return matches[-1] if matches else None


_PINNED_AT = datetime(2026, 11, 27, tzinfo=UTC)
_RESEARCH_BUDGET = ExecutionBudget(
    max_cost_usd=Decimal("1.00"),
    max_total_tokens=50_000,
    max_tool_invocations=10,
    max_parallel_tasks=1,
    max_attempts_per_task=1,
    task_timeout_seconds=120,
)


def default_workflow_entries() -> tuple[WorkflowCatalogEntry, ...]:
    """The first supported variant: one single-domain research worker."""

    workflow = WorkflowDefinition(
        workflow_definition_id=DEFAULT_RESEARCH_WORKFLOW_ID,
        workflow_version=DEFAULT_RESEARCH_WORKFLOW_VERSION,
        name="Single-worker research",
        description="One research worker answers the objective from admitted evidence.",
        control_mode=WorkflowControlMode.PREDEFINED,
        task_types=("research",),
        input_schema={
            "type": "object",
            "properties": {"objective": {"type": "string", "minLength": 1}},
            "required": ["objective"],
        },
        output_schema={"type": "object"},
        default_routing_policy_id="research.single-worker.policy",
        default_routing_policy_version="1",
        created_at=_PINNED_AT,
    )
    policy = RoutingPolicy(
        routing_policy_id="research.single-worker.policy",
        routing_policy_version="1",
        strategy="balanced",
        collaboration_mode=CollaborationMode.FAST_PATH.value,
        worker_types=("literature_review",),
        max_parallel_tasks=1,
        max_attempts_per_task=1,
        task_timeout_seconds=_RESEARCH_BUDGET.task_timeout_seconds,
        provider_allowlist=("deepseek",),
    )
    return (
        WorkflowCatalogEntry(
            workflow=workflow,
            routing_policy=policy,
            domains=("research",),
            budget_ceiling=_RESEARCH_BUDGET,
        ),
    )


@lru_cache(maxsize=1)
def get_workflow_catalog() -> ExecutableWorkflowCatalog:
    """Validate the deployment catalogue once against the registered workers."""

    from src.api.services.component_catalog import get_default_component_registry

    registry = get_default_component_registry()
    return ExecutableWorkflowCatalog(
        default_workflow_entries(),
        registered_workers=[
            name for name, key in AGENT_KEYS.items() if key in registry.keys
        ],
    )
