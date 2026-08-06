"""Shared helpers for the Wave 3 Packet 3E crash-matrix tests.

Every crash/recovery test needs the same three things: a resolvable
execution authority binding, a research project to execute, and a routing
decision shape ``ExecutionPlanCompiler``/``asdict`` accept without a live
MASR router. This module is the one place those are built, mirroring the
patterns already established by
``tests/integration/test_direct_execution_restart_recovery.py`` and
``tests/integration/test_main_lifespan_restart_recovery.py`` so the crash
tests stay consistent with the packets they build on rather than drifting
into their own shapes.
"""

import dataclasses
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai_brain.router.routing_types import RoutingStrategy
from src.core.contracts import (
    ExecutionBudget,
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
    Run,
    RunStatus,
    WorkerAssignment,
)
from src.models.execution_authority import ExecutionAuthorityBinding
from src.models.research_project import (
    ResearchDepth,
    ResearchProject,
    ResearchQuery,
    ResearchScope,
)
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository

ORG_ID = "00000000-0000-0000-0000-0000000000ee"
NOW = datetime(2026, 8, 4, tzinfo=UTC)


@dataclass
class _AllocStub:
    supervisor_type: str = "research"
    worker_count: int = 1
    worker_types: list[str] = field(default_factory=lambda: ["literature"])


@dataclass
class _ComplexityAnalysisStub:
    domains: list[str] = field(default_factory=lambda: ["research"])
    decomposition: None = None


@dataclass
class _RoutingDecisionStub:
    query_id: str = "test-query"
    collaboration_mode: str = "hierarchical"
    agent_allocation: _AllocStub = field(default_factory=_AllocStub)
    complexity_analysis: _ComplexityAnalysisStub = field(
        default_factory=_ComplexityAnalysisStub
    )
    estimated_cost: float = 0.01
    estimated_latency_ms: int = 1000
    estimated_quality: float = 0.9
    confidence_score: float = 0.9
    context: dict[str, Any] = field(default_factory=dict)
    routing_strategy: RoutingStrategy = RoutingStrategy.BALANCED
    optimization_result: Any = field(
        default_factory=lambda: type(
            "_Optimization",
            (),
            {
                "primary_model": type(
                    "_Model",
                    (),
                    {"provider": "gemini", "model_name": "gemini-2.5-pro"},
                )(),
                "fallback_models": [],
            },
        )()
    )


def make_binding(
    run_id: str, *, authority_id: str = "crash-authority", org_id: str = ORG_ID
) -> ExecutionAuthorityBinding:
    """Build an admittable authority binding for one run_id/authority_id pair."""

    raw = ExecutionAuthorityBinding.create_for_test(
        authority_id=authority_id,
        authority_version="1",
        run_id=run_id,
        workflow_definition_id="workflow-1",
        routing_policy_id="policy-1",
        strategy="balanced",
        collaboration_mode="hierarchical",
        domains=("research",),
        supervisor_id="supervisor-1",
        supervisor_type="research",
        workers=(
            WorkerAssignment(
                worker_id="worker-1",
                worker_type="literature",
                objective="Find sources",
                output_schema={},
                permission_scopes=(),
                tool_allowlist=(),
            ),
        ),
        edges=(),
        provider_model_policy=ProviderModelPolicy(
            primary=ProviderModelRoute(provider="gemini", model="gemini-2.5-pro"),
            fallback_mode=FallbackMode.FAIL_CLOSED,
            fallbacks=(),
            provider_allowlist=("gemini",),
            model_allowlist=("gemini-2.5-pro",),
        ),
        budget=ExecutionBudget(
            max_cost_usd=0,
            max_total_tokens=1,
            max_tool_invocations=0,
            max_parallel_tasks=1,
            max_attempts_per_task=1,
            task_timeout_seconds=60,
        ),
        stop_conditions=("complete",),
        evaluator_requirements=(),
        deadline=NOW + timedelta(minutes=5),
        compiled_at=NOW,
    )
    return dataclasses.replace(
        raw,
        run=raw.run.model_copy(
            update={"tenant_id": org_id, "idempotency_key": f"key-{run_id}"}
        ),
    )


def make_project(title: str = "Crash matrix test") -> ResearchProject:
    return ResearchProject(
        title=title,
        query=ResearchQuery(
            text="Does a fault at this transition lose or fabricate the outcome?",
            domains=["research"],
            depth_level=ResearchDepth.COMPREHENSIVE,
        ),
        user_id="user-1",
        scope=ResearchScope(max_sources=5),
    )


async def seed_run_with_event(
    session: AsyncSession,
    *,
    run_id: str,
    org_id: str = ORG_ID,
    destinations: tuple[str, ...] = ("redis",),
    event_type: str = "run.admitted",
    payload: dict[str, Any] | None = None,
) -> str:
    """Persist a minimal run plus one durable event with outbox rows.

    Mirrors ``tests/integration/test_outbox_relay.py``'s seeding helper —
    the smallest durable state an outbox-relay test needs, independent of
    the full ``DirectExecutionService`` admission path.

    Returns:
        The event's ``event_id``.
    """
    run = Run(
        run_id=run_id,
        tenant_id=org_id,
        workflow_definition_id="workflow-1",
        workflow_definition_version="1",
        routing_policy_id="policy-1",
        routing_policy_version="1",
        idempotency_key=f"key-{run_id}",
        requested_by="user-1",
        status=RunStatus.CREATED,
        created_at=NOW,
        updated_at=NOW,
    )
    await RunLifecycleRepository(session).create_run(run, organization_id=org_id)
    event_id = str(uuid.uuid4())
    await RunEventRepository(session).append_event(
        run_id=run_id,
        organization_id=org_id,
        event_id=event_id,
        aggregate_type="run",
        aggregate_id=run_id,
        event_type=event_type,
        event_type_version="1",
        occurred_at=NOW,
        producer="test",
        deduplication_key=event_id,
        payload=payload or {"note": "seeded"},
        destinations=destinations,
    )
    await session.commit()
    return event_id


__all__ = [
    "NOW",
    "ORG_ID",
    "_RoutingDecisionStub",
    "make_binding",
    "make_project",
    "seed_run_with_event",
]
