from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.ai_brain.router.execution_plan_compiler import ExecutionPlanCompiler
from src.ai_brain.router.routing_types import RoutingStrategy
from src.core.contracts import (
    CollaborationMode,
    ExecutionBudget,
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
    RoutingEdge,
    WorkerAssignment,
)
from src.models.execution_authority import ExecutionAuthorityBinding


class _Allocation:
    supervisor_type = "research"
    worker_types = ["literature", "synthesis"]


class _Model:
    provider = "gemini"
    model_name = "gemini-2.5-pro"


class _Optimization:
    primary_model = _Model()
    fallback_models: list[object] = []


class _Complexity:
    domains = ["research"]


class _Decision:
    routing_strategy = RoutingStrategy.BALANCED
    collaboration_mode = CollaborationMode.HIERARCHICAL
    agent_allocation = _Allocation()
    optimization_result = _Optimization()
    complexity_analysis = _Complexity()


def _binding() -> ExecutionAuthorityBinding:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    return ExecutionAuthorityBinding.create_for_test(
        authority_id="authority-1",
        authority_version="1",
        run_id="run-1",
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
            WorkerAssignment(
                worker_id="worker-2",
                worker_type="synthesis",
                objective="Synthesize",
                output_schema={},
                permission_scopes=(),
                tool_allowlist=(),
            ),
        ),
        edges=(
            RoutingEdge(
                source_node_id="supervisor-1",
                target_node_id="worker-1",
                relation="delegates",
            ),
        ),
        provider_model_policy=ProviderModelPolicy(
            primary=ProviderModelRoute(provider="gemini", model="gemini-2.5-pro"),
            fallback_mode=FallbackMode.FAIL_CLOSED,
            fallbacks=(),
            provider_allowlist=("gemini",),
            model_allowlist=("gemini-2.5-pro",),
        ),
        budget=ExecutionBudget(
            max_cost_usd=Decimal("1.00"),
            max_total_tokens=1000,
            max_tool_invocations=0,
            max_parallel_tasks=2,
            max_attempts_per_task=1,
            task_timeout_seconds=60,
        ),
        stop_conditions=("complete",),
        evaluator_requirements=(),
        deadline=now + timedelta(minutes=5),
        compiled_at=now,
    )


def test_compiler_creates_deterministic_frozen_plan_from_matching_proposal() -> None:
    plan = ExecutionPlanCompiler().compile(_Decision(), _binding())

    assert plan.run_id == "run-1"
    assert plan.routing_decision.collaboration_mode.value == "hierarchical"
    assert plan.routing_decision.provider_model_policy.primary.model == "gemini-2.5-pro"


def test_compiler_rejects_unsupported_mode_before_dispatch() -> None:
    proposal = _Decision()
    proposal.collaboration_mode = CollaborationMode.DEBATE

    with pytest.raises(ValueError, match="unsupported"):
        ExecutionPlanCompiler().compile(proposal, _binding())


def test_compiler_rejects_provider_route_mismatch() -> None:
    proposal = _Decision()
    proposal.optimization_result = type(
        "_OptimizationWithFallback",
        (),
        {
            "primary_model": _Model(),
            "fallback_models": [
                type(
                    "_FallbackModel",
                    (),
                    {"provider": "openai", "model_name": "gpt-5"},
                )()
            ],
        },
    )()

    with pytest.raises(ValueError, match="provider/model routes"):
        ExecutionPlanCompiler().compile(proposal, _binding())


def test_compiler_rejects_domain_mismatch() -> None:
    proposal = _Decision()
    proposal.complexity_analysis = type("_Complexity", (), {"domains": ["finance"]})()
    proposal.optimization_result = type(
        "_OptimizationWithoutFallback",
        (),
        {"primary_model": _Model(), "fallback_models": []},
    )()

    with pytest.raises(ValueError, match="domains"):
        ExecutionPlanCompiler().compile(proposal, _binding())
