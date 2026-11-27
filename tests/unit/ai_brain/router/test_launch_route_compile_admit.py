"""A query routed under the catalogue's workflow either admits or is rejected
before any worker runs, using the real router, compiler and topology."""

from datetime import UTC, datetime, timedelta

import pytest

from src.ai_brain.integration.execution_plan_topology import (
    ExecutionPlanTopologyExecutor,
)
from src.ai_brain.router.execution_plan_compiler import ExecutionPlanCompiler
from src.ai_brain.router.factory import settings_to_router_config
from src.ai_brain.router.masr import MASRouter
from src.ai_brain.router.routing_types import WorkflowShapeUnsupportedError
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
    """What the launch service will build: the catalogue entry is the authority.

    Only the primary model comes from the proposal, and it must sit inside the
    entry's provider allowlist.
    """
    now = datetime(2026, 11, 27, tzinfo=UTC)
    route = proposal.optimization_result.primary_model
    allowlist = ENTRY.routing_policy.provider_allowlist
    worker_type = ENTRY.routing_policy.worker_types[0]
    return ExecutionAuthorityBinding.create_for_test(
        authority_id="authority-1",
        authority_version="1",
        run_id="run-1",
        workflow_definition_id=ENTRY.workflow.workflow_definition_id,
        routing_policy_id=ENTRY.routing_policy.routing_policy_id,
        strategy=ENTRY.routing_policy.strategy,
        collaboration_mode=ENTRY.routing_policy.collaboration_mode,
        domains=ENTRY.domains,
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
            provider_allowlist=allowlist,
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
    return await router.route(query, {}, shape=ENTRY.shape())


@pytest.mark.asyncio
async def test_simple_query_routes_compiles_and_admits_with_one_registered_worker():
    proposal = await _route("What is photosynthesis?")

    plan = ExecutionPlanCompiler().compile(proposal, _binding(proposal))
    _topology().admit(plan)
    assert len(plan.routing_decision.workers) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "What is photosynthesis?",
        "Summarize recent literature on sleep and memory",
        "Compare and contrast two machine learning approaches for fraud detection "
        "with tradeoffs.",
    ],
)
async def test_simple_and_research_queries_admit_under_the_catalogue_entry(query):
    proposal = await _route(query)

    assert proposal.optimization_result.primary_model.provider in (
        ENTRY.routing_policy.provider_allowlist
    )
    plan = ExecutionPlanCompiler().compile(proposal, _binding(proposal))
    _topology().admit(plan)
    assert [w.worker_type for w in plan.routing_decision.workers] == list(
        ENTRY.routing_policy.worker_types
    )


@pytest.mark.asyncio
async def test_a_query_outside_the_entrys_domain_is_rejected_by_the_router():
    with pytest.raises(WorkflowShapeUnsupportedError, match="domain"):
        await _route("Write a blog post and analyze our quarterly revenue data")


@pytest.mark.asyncio
async def test_a_binding_that_claims_a_supervisor_for_the_fast_path_is_rejected():
    proposal = await _route("What is photosynthesis?")
    binding = _binding(proposal)
    claimed = ExecutionAuthorityBinding(
        **{**binding.__dict__, "supervisor_type": "research"}
    )

    with pytest.raises(ValueError, match="supervisor does not match"):
        ExecutionPlanCompiler().compile(proposal, claimed)


@pytest.mark.asyncio
async def test_out_of_domain_queries_do_not_trip_the_routing_breaker():
    router = MASRouter(
        config=settings_to_router_config(get_settings(), fixture_mode=True)
    )
    for _ in range(6):
        with pytest.raises(WorkflowShapeUnsupportedError):
            await router.route(
                "Write a blog post and analyze our revenue data",
                {},
                shape=ENTRY.shape(),
            )

    decision = await router.route("What is photosynthesis?", {}, shape=ENTRY.shape())
    assert decision.agent_allocation.worker_types == list(
        ENTRY.routing_policy.worker_types
    )


@pytest.mark.asyncio
async def test_routing_picks_only_from_the_shapes_provider_allowlist():
    from dataclasses import replace

    router = MASRouter(
        config=settings_to_router_config(get_settings(), fixture_mode=True)
    )
    for provider in ("gemini", "deepseek"):
        shape = replace(ENTRY.shape(), provider_allowlist=(provider,))
        decision = await router.route("What is photosynthesis?", {}, shape=shape)
        assert decision.optimization_result.primary_model.provider == provider
        assert decision.optimization_result.fallback_models == []


@pytest.mark.asyncio
async def test_an_allowlist_no_model_satisfies_is_refused_not_rerouted():
    from dataclasses import replace

    router = MASRouter(
        config=settings_to_router_config(get_settings(), fixture_mode=True)
    )
    shape = replace(ENTRY.shape(), provider_allowlist=("no-such-provider",))
    with pytest.raises(WorkflowShapeUnsupportedError):
        await router.route("What is photosynthesis?", {}, shape=shape)


@pytest.mark.asyncio
async def test_the_shapes_workers_replace_the_routers_choice():
    from dataclasses import replace

    router = MASRouter(
        config=settings_to_router_config(get_settings(), fixture_mode=True)
    )
    shape = replace(ENTRY.shape(), worker_types=("synthesis",))
    decision = await router.route("What is photosynthesis?", {}, shape=shape)
    assert decision.agent_allocation.worker_types == ["synthesis"]
    assert decision.agent_allocation.worker_count == 1
