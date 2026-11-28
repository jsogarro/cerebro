"""Composition for evaluation runs: an isolated service and one authority per run.

MASR proposes worker types the plan executor cannot run and always proposes
fallback models, which the v1 topology rejects, so an evaluation run pins its
plan instead of routing it. ``PinnedPlanRouter`` proposes exactly the topology
``build_trial_authority`` binds; the plan compiler still checks the two agree.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.ai_brain.router.routing_types import RoutingStrategy
from src.api.services.component_catalog import build_application_component_registry
from src.api.services.direct_execution_service import DirectExecutionService
from src.api.services.execution_authority_resolver import (
    PersistedExecutionAuthorityResolver,
)
from src.core.capabilities.issuer import OUTBOUND_TOOL_HOSTS
from src.core.config import Settings
from src.core.contracts import (
    ExecutionBudget,
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
    RoutingPolicy,
    Run,
    RunStatus,
    WorkerAssignment,
    WorkflowControlMode,
    WorkflowDefinition,
)
from src.models.execution_authority import ExecutionAuthorityBinding

if TYPE_CHECKING:
    from src.ai_brain.router.masr import MASRouter


class LiveEnvironmentError(RuntimeError):
    """The environment cannot host an evaluation run."""


DOMAIN_WORKERS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "research": ("literature_review", "comparative_analysis"),
        "analytics": ("data_analysis", "statistical_modeling"),
        "content": ("content_planning", "drafting"),
        "finance": ("financial_analysis", "risk_assessment"),
    }
)
TRIAL_STRATEGY = RoutingStrategy.BALANCED
TRIAL_COLLABORATION_MODE = "parallel"
TRIAL_MAX_TOTAL_TOKENS = 200_000
TRIAL_MAX_TOOL_INVOCATIONS = 10
GRANT_DEADLINE_MARGIN_SECONDS = 60
TOOL_PERMISSION_SCOPE = "research:read"
_WORKFLOW_ID = "evaluation-trial"
_ROUTING_POLICY_ID = "evaluation-trial"
_REQUESTED_BY = "evaluation-harness"


@dataclass(frozen=True)
class _Model:
    provider: str
    model_name: str


@dataclass(frozen=True)
class _Optimization:
    primary_model: _Model
    fallback_models: tuple[_Model, ...] = ()


@dataclass(frozen=True)
class _Allocation:
    supervisor_type: str | None
    worker_types: tuple[str, ...]


@dataclass(frozen=True)
class _Analysis:
    domains: tuple[str, ...]


@dataclass(frozen=True)
class PinnedProposal:
    """The fields of a MASR decision that the plan compiler and service read."""

    collaboration_mode: str
    routing_strategy: RoutingStrategy
    agent_allocation: _Allocation
    complexity_analysis: _Analysis
    optimization_result: _Optimization


class PinnedPlanRouter:
    """Propose the trial plan for the query's single domain."""

    def __init__(self, model_route: ProviderModelRoute) -> None:
        self._model = _Model(model_route.provider, model_route.model)

    async def route(
        self, query: str, context: dict[str, Any] | None = None, **_: Any
    ) -> PinnedProposal:
        domains = tuple((context or {}).get("domains") or ())
        if len(domains) != 1 or domains[0] not in DOMAIN_WORKERS:
            raise LiveEnvironmentError(
                f"an evaluation query must name one known domain, got {domains!r}"
            )
        return PinnedProposal(
            collaboration_mode=TRIAL_COLLABORATION_MODE,
            routing_strategy=TRIAL_STRATEGY,
            agent_allocation=_Allocation(
                supervisor_type=None, worker_types=DOMAIN_WORKERS[domains[0]]
            ),
            complexity_analysis=_Analysis(domains=domains),
            optimization_result=_Optimization(primary_model=self._model),
        )

    async def health_check(self) -> dict[str, Any]:
        return {"status": "pinned"}


@asynccontextmanager
async def build_harvest_service(
    settings: Settings, *, model_route: ProviderModelRoute
) -> AsyncIterator[tuple[DirectExecutionService, PersistedExecutionAuthorityResolver]]:
    """Yield a judging-off service bound to the evaluation database only.

    No dispatcher, relay or event publisher is built, and the resolver is not
    warmed from snapshots: it resolves only the bindings minted in this process.
    """
    if not settings.EVAL_RUN_DATABASE_URL:
        raise LiveEnvironmentError("EVAL_RUN_DATABASE_URL is not set")
    engine = create_async_engine(settings.EVAL_RUN_DATABASE_URL)
    resolver = PersistedExecutionAuthorityResolver()
    service = DirectExecutionService(
        masr_router=cast("MASRouter", PinnedPlanRouter(model_route)),
        component_registry=build_application_component_registry(),
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        execution_authority_resolver=resolver,
    )
    try:
        yield service, resolver
    finally:
        try:
            await service.close()
        finally:
            await engine.dispose()


def trial_wall_seconds(task_timeout_seconds: int) -> int:
    """One attempt per task plus the grant's admission margin."""
    return task_timeout_seconds + GRANT_DEADLINE_MARGIN_SECONDS


def build_trial_authority(
    *,
    domain: str,
    organization_id: str,
    resolver: PersistedExecutionAuthorityResolver,
    per_run_cap: Decimal,
    task_timeout_seconds: int,
    model_route: ProviderModelRoute,
    tool_grants: tuple[str, ...] = (),
) -> ExecutionAuthorityBinding:
    """Mint and cache one run's authority; mint it right before admission.

    The deadline starts now, and a tool-granted run is admitted only while
    ``now + grant TTL`` still fits it, i.e. within the 60 s margin.
    """
    if domain not in DOMAIN_WORKERS:
        raise LiveEnvironmentError(f"unknown evaluation domain {domain!r}")
    effective_outbound_hosts = tuple(
        dict.fromkeys(
            host
            for tool_name in tool_grants
            for host in OUTBOUND_TOOL_HOSTS.get(tool_name, ())
        )
    )
    now = datetime.now(UTC)
    worker_types = DOMAIN_WORKERS[domain]
    budget = ExecutionBudget(
        max_cost_usd=per_run_cap,
        max_total_tokens=TRIAL_MAX_TOTAL_TOKENS,
        max_tool_invocations=TRIAL_MAX_TOOL_INVOCATIONS if tool_grants else 0,
        max_parallel_tasks=len(worker_types),
        max_attempts_per_task=1,
        task_timeout_seconds=task_timeout_seconds,
    )
    run = Run(
        run_id=f"eval-{uuid.uuid4()}",
        tenant_id=organization_id,
        workflow_definition_id=_WORKFLOW_ID,
        workflow_definition_version="1",
        routing_policy_id=_ROUTING_POLICY_ID,
        routing_policy_version="1",
        idempotency_key=str(uuid.uuid4()),
        requested_by=_REQUESTED_BY,
        status=RunStatus.CREATED,
        created_at=now,
        updated_at=now,
    )
    binding = ExecutionAuthorityBinding(
        authority_id=f"eval-authority-{uuid.uuid4()}",
        authority_version=str(uuid.uuid4()),
        run=run,
        workflow_definition=WorkflowDefinition(
            workflow_definition_id=_WORKFLOW_ID,
            workflow_version="1",
            name="evaluation trial",
            description="One pinned evaluation run",
            control_mode=WorkflowControlMode.PREDEFINED,
            task_types=("research",),
            input_schema={},
            output_schema={},
            default_routing_policy_id=_ROUTING_POLICY_ID,
            default_routing_policy_version="1",
            created_at=now,
        ),
        routing_policy=RoutingPolicy(
            routing_policy_id=_ROUTING_POLICY_ID,
            routing_policy_version="1",
            strategy=TRIAL_STRATEGY.value,
            collaboration_mode=TRIAL_COLLABORATION_MODE,
            worker_types=worker_types,
            max_parallel_tasks=budget.max_parallel_tasks,
            max_attempts_per_task=budget.max_attempts_per_task,
            task_timeout_seconds=budget.task_timeout_seconds,
            provider_allowlist=(model_route.provider,),
            model_allowlist=(model_route.model,),
        ),
        domains=(domain,),
        supervisor_id=None,
        supervisor_type=None,
        workers=tuple(
            WorkerAssignment(
                worker_id=f"worker-{index}",
                worker_type=worker_type,
                objective=f"{worker_type} for one evaluation query",
                output_schema={},
                permission_scopes=(TOOL_PERMISSION_SCOPE,) if tool_grants else (),
                tool_allowlist=tool_grants,
                allowed_outbound_hosts=effective_outbound_hosts,
            )
            for index, worker_type in enumerate(worker_types, start=1)
        ),
        edges=(),
        provider_model_policy=ProviderModelPolicy(
            primary=model_route,
            fallback_mode=FallbackMode.FAIL_CLOSED,
            fallbacks=(),
            provider_allowlist=(model_route.provider,),
            model_allowlist=(model_route.model,),
        ),
        budget=budget,
        stop_conditions=("complete",),
        evaluator_requirements=(),
        deadline=now + timedelta(seconds=trial_wall_seconds(task_timeout_seconds)),
        plan_id_factory=lambda: str(uuid.uuid4()),
        clock=lambda: datetime.now(UTC),
    )
    resolver.cache_binding(binding)
    return binding
