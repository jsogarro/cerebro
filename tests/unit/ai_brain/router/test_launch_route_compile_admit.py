"""A query routed under the catalogue's workflow either admits or is rejected
before any worker runs, using the real router, compiler and topology."""

from datetime import UTC, datetime, timedelta

import pytest

from src.ai_brain.integration.execution_plan_topology import (
    ExecutionPlanTopologyExecutor,
    ExecutionTopologyUnsupportedError,
)
from src.ai_brain.router.execution_plan_compiler import ExecutionPlanCompiler
from src.ai_brain.router.factory import settings_to_router_config
from src.ai_brain.router.masr import MASRouter
from src.ai_brain.router.routing_types import RoutingStrategy
from src.api.services.workflow_catalog import default_workflow_entries
from src.core.config import get_settings
from src.core.contracts import (
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
    WorkerAssignment,
)
from src.core.kernel.component_keys import AGENT_KEYS
from src.models.execution_authority import ExecutionAuthorityBinding

(ENTRY,) = default_workflow_entries()


def _binding(proposal) -> ExecutionAuthorityBinding:
    """What the launch service will build: entry shape, proposal's primary route."""
    now = datetime(2026, 11, 27, tzinfo=UTC)
    route = proposal.optimization_result.primary_model
    worker_type = ENTRY.routing_policy.worker_types[0]
    return ExecutionAuthorityBinding.create_for_test(
        authority_id="authority-1",
        authority_version="1",
        run_id="run-1",
        workflow_definition_id=ENTRY.workflow.workflow_definition_id,
        routing_policy_id=ENTRY.routing_policy.routing_policy_id,
        strategy=ENTRY.routing_policy.strategy,
        collaboration_mode=ENTRY.routing_policy.collaboration_mode,
        domains=tuple(d.value for d in proposal.complexity_analysis.domains),
        supervisor_id=None,
        supervisor_type=None,  # single-worker modes have no supervisor node
        workers=(
            WorkerAssignment(
                worker_id="worker-1",
                worker_type=worker_type,
                objective="answer the objective",
                output_schema={},
                permission_scopes=(),
                tool_allowlist=(),
            ),
        ),
        edges=(),
        provider_model_policy=ProviderModelPolicy(
            primary=ProviderModelRoute(provider=route.provider, model=route.name),
            fallback_mode=FallbackMode.FAIL_CLOSED,
            fallbacks=(),
            provider_allowlist=(route.provider,),
            model_allowlist=(route.name,),
        ),
        budget=ENTRY.budget_ceiling,
        stop_conditions=("complete",),
        evaluator_requirements=(),
        deadline=now + timedelta(minutes=5),
        compiled_at=now,
    )


async def _never_called(worker, task):
    raise AssertionError("a worker ran during admission")


def _topology() -> ExecutionPlanTopologyExecutor:
    return ExecutionPlanTopologyExecutor(
        worker_executor=_never_called,
        capability_checker=lambda worker_type: AGENT_KEYS.get(worker_type) is not None,
    )


async def _route(query: str):
    router = MASRouter(
        config=settings_to_router_config(get_settings(), fixture_mode=True)
    )
    # The launch path routes under the strategy its catalogue policy pins.
    return await router.route(
        query, {}, strategy=RoutingStrategy(ENTRY.routing_policy.strategy)
    )


@pytest.mark.asyncio
async def test_simple_query_routes_compiles_and_admits_with_one_registered_worker():
    proposal = await _route("What is photosynthesis?")

    assert proposal.collaboration_mode.value == "fast_path"
    assert proposal.optimization_result.fallback_models == []
    assert list(proposal.agent_allocation.worker_types) == [
        ENTRY.routing_policy.worker_types[0]
    ]
    plan = ExecutionPlanCompiler().compile(proposal, _binding(proposal))
    _topology().admit(plan)
    assert len(plan.routing_decision.workers) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "Compare and contrast two machine learning approaches for fraud detection "
        "with tradeoffs.",
        "Summarize recent literature on sleep and memory",
    ],
)
async def test_queries_the_catalogue_cannot_serve_are_rejected_before_dispatch(query):
    proposal = await _route(query)

    with pytest.raises((ValueError, ExecutionTopologyUnsupportedError)):
        _topology().admit(ExecutionPlanCompiler().compile(proposal, _binding(proposal)))


@pytest.mark.asyncio
async def test_a_binding_that_claims_a_supervisor_for_the_fast_path_is_rejected():
    proposal = await _route("What is photosynthesis?")
    binding = _binding(proposal)
    claimed = ExecutionAuthorityBinding(
        **{**binding.__dict__, "supervisor_type": "research"}
    )

    with pytest.raises(ValueError, match="supervisor does not match"):
        ExecutionPlanCompiler().compile(proposal, claimed)
