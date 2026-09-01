"""Issue immutable capability grants from admitted execution authority."""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from src.core.contracts.capabilities import CapabilityGrant, SensitivityClass
from src.core.contracts.execution_plan import (
    ExecutionBudget,
    ExecutionPlan,
    WorkerAssignment,
)
from src.core.contracts.provenance import TrustClassification

CAPABILITY_GRANTS_CONTEXT_KEY = "capability_grants"
"""Agent-task context key reserved for the admitted grant contracts."""

CAPABILITY_SCOPE_PREFIX = "plan-issued:"
"""Scope namespace that identifies grants minted from admitted authority."""

DEFAULT_TOOL_VERSION = "1.0.0"
"""The version pinned for tools whose plan declaration has no version field."""


class _AdmittedAuthority(Protocol):
    """The binding fields needed before a plan is compiled."""

    workers: tuple[WorkerAssignment, ...]
    budget: ExecutionBudget
    deadline: datetime


class PlanCapabilityIssuer:
    """Translate admitted worker declarations into validated grant contracts.

    The issuer accepts either the compiled ``ExecutionPlan`` used by the
    service or its immutable authority binding for direct unit-level callers.
    In both cases the source is admission authority, not an invocation. The
    grant constraints are static safe defaults because the worker declaration
    has no per-tool version, sensitivity, or input-trust fields.
    """

    @staticmethod
    def issue(
        admitted_plan: ExecutionPlan | _AdmittedAuthority,
        *,
        run_id: str,
        task_id: str,
        issued_at: datetime,
    ) -> tuple[CapabilityGrant, ...]:
        """Issue one grant for each tool declared by each admitted worker.

        ``run_id`` and ``task_id`` are supplied by the admission transaction's
        durable rows. No invocation data is accepted, so caller-provided
        input, tool version, or scope values cannot alter a grant.
        """
        workers, budget, deadline = PlanCapabilityIssuer._authority_fields(
            admitted_plan
        )
        if isinstance(admitted_plan, ExecutionPlan) and run_id != admitted_plan.run_id:
            raise ValueError("run_id must match the admitted execution plan")
        if issued_at.tzinfo is None or issued_at.utcoffset() is None:
            raise ValueError("issued_at must be timezone-aware")

        expires_at = issued_at + PlanCapabilityIssuer._grant_ttl(budget)
        if expires_at > deadline:
            raise ValueError(
                "budget-derived capability grant deadline exceeds the admitted plan deadline"
            )

        grants: list[CapabilityGrant] = []
        seen_grant_ids: set[str] = set()
        for worker in workers:
            tools = tuple(str(tool) for tool in worker.tool_allowlist)
            if tools and not worker.permission_scopes:
                raise ValueError(
                    f"worker {worker.worker_id!r} declares tools without a permission scope"
                )
            for tool_name in tools:
                grant_id = uuid.uuid4().hex
                if grant_id in seen_grant_ids:
                    raise ValueError(f"duplicate capability grant id {grant_id!r}")
                seen_grant_ids.add(grant_id)
                grants.append(
                    CapabilityGrant(
                        grant_id=grant_id,
                        run_id=run_id,
                        task_id=task_id,
                        capability_scope=f"{CAPABILITY_SCOPE_PREFIX}{tool_name}",
                        tool_name=tool_name,
                        tool_versions=(DEFAULT_TOOL_VERSION,),
                        sensitivity=SensitivityClass.READ_ONLY,
                        max_input_trust=TrustClassification.EXTERNAL_UNTRUSTED,
                        requires_approval=False,
                        issued_at=issued_at,
                        expires_at=expires_at,
                    )
                )
        return tuple(grants)

    @staticmethod
    def _authority_fields(
        admitted_plan: ExecutionPlan | _AdmittedAuthority,
    ) -> tuple[Sequence[WorkerAssignment], ExecutionBudget, datetime]:
        if isinstance(admitted_plan, ExecutionPlan):
            decision = admitted_plan.routing_decision
            return decision.workers, decision.budget, admitted_plan.deadline
        return admitted_plan.workers, admitted_plan.budget, admitted_plan.deadline

    @staticmethod
    def _grant_ttl(budget: ExecutionBudget) -> timedelta:
        """Cover each admitted task's maximum number of timed attempts."""
        return timedelta(
            seconds=budget.max_attempts_per_task * budget.task_timeout_seconds
        )


__all__ = [
    "CAPABILITY_GRANTS_CONTEXT_KEY",
    "CAPABILITY_SCOPE_PREFIX",
    "DEFAULT_TOOL_VERSION",
    "PlanCapabilityIssuer",
]
