"""Issue immutable capability grants from admitted execution authority."""

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Final, Protocol

from src.core.contracts.capabilities import CapabilityGrant, SensitivityClass
from src.core.contracts.execution_plan import (
    ExecutionBudget,
    ExecutionPlan,
    WorkerAssignment,
)
from src.core.contracts.provenance import TrustClassification
from src.mcp.tools.academic_search_tool import ACADEMIC_SEARCH_OUTBOUND_HOSTS
from src.mcp.tools.citation_tool import CITATION_OUTBOUND_HOSTS

CAPABILITY_GRANTS_CONTEXT_KEY = "capability_grants"
"""Agent-task context key reserved for the admitted grant contracts."""

CAPABILITY_SCOPE_PREFIX = "plan-issued:"
"""Scope namespace that identifies grants minted from admitted authority."""

DEFAULT_TOOL_VERSION = "1.0.0"
"""The version pinned for tools whose plan declaration has no version field."""

OUTBOUND_TOOL_HOSTS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "mcp.academic_search": ACADEMIC_SEARCH_OUTBOUND_HOSTS,
        "mcp.format_citations": CITATION_OUTBOUND_HOSTS,
    }
)
"""Pinned outbound host sets for tools classified as ``EXTERNAL_READ``."""


class _AdmittedAuthority(Protocol):
    """The binding fields needed before a plan is compiled."""

    @property
    def workers(self) -> tuple[WorkerAssignment, ...]: ...

    @property
    def budget(self) -> ExecutionBudget: ...

    @property
    def deadline(self) -> datetime: ...


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

        grants: list[CapabilityGrant] = []
        seen_grant_ids: set[str] = set()
        for worker in workers:
            tools = tuple(str(tool) for tool in worker.tool_allowlist)
            if not tools:
                continue
            if tools and not worker.permission_scopes:
                raise ValueError(
                    f"worker {worker.worker_id!r} declares tools without a permission scope"
                )
            expires_at = issued_at + PlanCapabilityIssuer._grant_ttl(budget)
            if expires_at > deadline:
                raise ValueError(
                    "budget-derived capability grant deadline exceeds the admitted plan deadline"
                )
            for tool_name in tools:
                grant_id = uuid.uuid4().hex
                if grant_id in seen_grant_ids:
                    raise ValueError(f"duplicate capability grant id {grant_id!r}")
                seen_grant_ids.add(grant_id)
                declared_hosts = OUTBOUND_TOOL_HOSTS.get(tool_name)
                if declared_hosts is not None:
                    sensitivity = SensitivityClass.EXTERNAL_READ
                    declared_set = frozenset(declared_hosts)
                    allowed_hosts = tuple(
                        host
                        for host in worker.allowed_outbound_hosts
                        if host in declared_set
                    )
                else:
                    sensitivity = SensitivityClass.READ_ONLY
                    allowed_hosts = ()
                grants.append(
                    CapabilityGrant(
                        grant_id=grant_id,
                        run_id=run_id,
                        task_id=task_id,
                        capability_scope=f"{CAPABILITY_SCOPE_PREFIX}{tool_name}",
                        tool_name=tool_name,
                        tool_versions=(DEFAULT_TOOL_VERSION,),
                        sensitivity=sensitivity,
                        max_input_trust=TrustClassification.EXTERNAL_UNTRUSTED,
                        requires_approval=False,
                        allowed_hosts=allowed_hosts,
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
    "OUTBOUND_TOOL_HOSTS",
    "PlanCapabilityIssuer",
]
