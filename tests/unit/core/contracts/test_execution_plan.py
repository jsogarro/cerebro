"""Contract tests for immutable, versioned execution routing authority."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from src.core.contracts import (
    COLLABORATION_MODE_SUPPORT,
    AmendmentValidationStatus,
    CollaborationMode,
    CollaborationModeSupport,
    ExecutionBudget,
    ExecutionPlan,
    FallbackMode,
    PlanAmendment,
    ProviderModelPolicy,
    ProviderModelRoute,
    RoutingDecision,
    RoutingEdge,
    WorkerAssignment,
)

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


def _worker(
    worker_id: str = "worker-research",
    worker_type: str = "literature_review",
) -> WorkerAssignment:
    return WorkerAssignment(
        worker_id=worker_id,
        worker_type=worker_type,
        objective="Find and assess primary sources.",
        output_schema={"type": "object", "required": ["findings"]},
        permission_scopes=("research:read",),
        tool_allowlist=("web-search",),
    )


def _provider_policy() -> ProviderModelPolicy:
    return ProviderModelPolicy(
        primary=ProviderModelRoute(
            provider="openrouter",
            model="openai/gpt-5",
        ),
        fallback_mode=FallbackMode.ORDERED_PROVIDER_MODEL,
        fallbacks=(
            ProviderModelRoute(
                provider="gemini",
                model="gemini-2.5-pro",
            ),
        ),
        provider_allowlist=("openrouter", "gemini"),
        model_allowlist=("openai/gpt-5", "gemini-2.5-pro"),
    )


def _budget() -> ExecutionBudget:
    return ExecutionBudget(
        max_cost_usd="1.50",
        max_total_tokens=100_000,
        max_tool_invocations=20,
        max_parallel_tasks=1,
        max_attempts_per_task=2,
        task_timeout_seconds=300,
    )


def _decision_payload() -> dict[str, Any]:
    return {
        "routing_decision_id": "routing-decision-001",
        "strategy": "quality_focused",
        "domains": ("research",),
        "collaboration_mode": CollaborationMode.DIRECT,
        "supervisor_id": None,
        "supervisor_type": None,
        "workers": (_worker(),),
        "edges": (),
        "provider_model_policy": _provider_policy(),
        "budget": _budget(),
        "stop_conditions": ("objective_satisfied", "budget_exhausted"),
        "evaluator_requirements": ("citation_resolution",),
    }


def _plan_payload() -> dict[str, Any]:
    return {
        "execution_plan_id": "execution-plan-001",
        "plan_version": 1,
        "run_id": "run-001",
        "workflow_definition_id": "workflow.research",
        "workflow_definition_version": "1.0.0",
        "routing_policy_id": "routing.research-default",
        "routing_policy_version": "1.0.0",
        "routing_decision": RoutingDecision(**_decision_payload()),
        "compiled_at": NOW,
        "deadline": NOW + timedelta(minutes=10),
        "amendment": None,
    }


def test_collaboration_support_matrix_has_one_explicit_state_per_mode() -> None:
    assert COLLABORATION_MODE_SUPPORT == {
        CollaborationMode.FAST_PATH: CollaborationModeSupport.IMPLEMENTED,
        CollaborationMode.DIRECT: CollaborationModeSupport.IMPLEMENTED,
        CollaborationMode.PARALLEL: CollaborationModeSupport.IMPLEMENTED,
        CollaborationMode.HIERARCHICAL: CollaborationModeSupport.IMPLEMENTED,
        CollaborationMode.DEBATE: CollaborationModeSupport.EXPLICITLY_REJECTED,
        CollaborationMode.ENSEMBLE: CollaborationModeSupport.EXPLICITLY_REJECTED,
    }
    assert set(COLLABORATION_MODE_SUPPORT) == set(CollaborationMode)
    assert set(CollaborationModeSupport) == {
        CollaborationModeSupport.IMPLEMENTED,
        CollaborationModeSupport.EXPLICITLY_REJECTED,
        CollaborationModeSupport.OUT_OF_WAVE_2,
    }


def test_execution_plan_is_deeply_immutable() -> None:
    plan = ExecutionPlan(**_plan_payload())

    with pytest.raises(ValidationError, match="frozen"):
        plan.plan_version = 2
    with pytest.raises(ValidationError, match="frozen"):
        plan.routing_decision.budget.max_parallel_tasks = 2
    with pytest.raises(AttributeError):
        plan.routing_decision.workers.append(_worker("worker-2"))
    with pytest.raises(TypeError):
        plan.routing_decision.workers[0].output_schema["type"] = "array"


def test_execution_plan_canonical_json_round_trips_deterministically() -> None:
    plan = ExecutionPlan(**_plan_payload())

    canonical = plan.canonical_json()
    restored = ExecutionPlan.model_validate_json(canonical)

    assert restored == plan
    assert restored.canonical_json() == canonical
    assert canonical == json.dumps(
        plan.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def test_routing_decision_canonical_json_is_independent_of_mapping_input_order() -> (
    None
):
    first_payload = _decision_payload()
    first_payload["workers"] = (
        _worker().model_copy(
            update={
                "output_schema": {
                    "type": "object",
                    "properties": {"z": {"type": "string"}, "a": {"type": "number"}},
                }
            }
        ),
    )
    second_payload = _decision_payload()
    second_payload["workers"] = (
        _worker().model_copy(
            update={
                "output_schema": {
                    "properties": {"a": {"type": "number"}, "z": {"type": "string"}},
                    "type": "object",
                }
            }
        ),
    )

    first = RoutingDecision(**first_payload)
    second = RoutingDecision(**second_payload)

    assert first == second
    assert first.canonical_json() == second.canonical_json()


@pytest.mark.parametrize(
    "missing_path",
    (
        "collaboration_mode",
        "workers",
        "edges",
        "provider_model_policy",
        "budget",
        "stop_conditions",
        "evaluator_requirements",
    ),
)
def test_routing_decision_rejects_missing_authority(missing_path: str) -> None:
    payload = _decision_payload()
    del payload[missing_path]

    with pytest.raises(ValidationError, match=missing_path):
        RoutingDecision(**payload)


@pytest.mark.parametrize(
    "missing_path",
    (
        "max_cost_usd",
        "max_total_tokens",
        "max_tool_invocations",
        "max_parallel_tasks",
        "max_attempts_per_task",
        "task_timeout_seconds",
    ),
)
def test_budget_rejects_missing_authority(missing_path: str) -> None:
    payload = _budget().model_dump()
    del payload[missing_path]

    with pytest.raises(ValidationError, match=missing_path):
        ExecutionBudget(**payload)


@pytest.mark.parametrize(
    "field_name",
    (
        "max_total_tokens",
        "max_tool_invocations",
        "max_parallel_tasks",
        "max_attempts_per_task",
        "task_timeout_seconds",
    ),
)
def test_budget_rejects_boolean_integer_authority(field_name: str) -> None:
    payload = _budget().model_dump()
    payload[field_name] = True

    with pytest.raises(ValidationError, match="valid integer"):
        ExecutionBudget(**payload)


@pytest.mark.parametrize("missing_path", ("permission_scopes", "tool_allowlist"))
def test_worker_rejects_missing_security_authority(missing_path: str) -> None:
    payload = _worker().model_dump()
    del payload[missing_path]

    with pytest.raises(ValidationError, match=missing_path):
        WorkerAssignment(**payload)


def test_provider_policy_rejects_ambiguous_or_unauthorized_routes() -> None:
    primary = ProviderModelRoute(provider="openrouter", model="openai/gpt-5")

    with pytest.raises(ValidationError, match="requires at least one fallback"):
        ProviderModelPolicy(
            primary=primary,
            fallback_mode=FallbackMode.ORDERED_PROVIDER_MODEL,
            fallbacks=(),
            provider_allowlist=("openrouter",),
            model_allowlist=("openai/gpt-5",),
        )

    with pytest.raises(ValidationError, match="provider allowlist"):
        ProviderModelPolicy(
            primary=primary,
            fallback_mode=FallbackMode.FAIL_CLOSED,
            fallbacks=(),
            provider_allowlist=("gemini",),
            model_allowlist=("openai/gpt-5",),
        )


def test_routing_decision_rejects_malformed_topology() -> None:
    payload = _decision_payload()
    payload["collaboration_mode"] = CollaborationMode.PARALLEL
    payload["workers"] = (_worker(), _worker())
    with pytest.raises(ValidationError, match="worker_id entries must be unique"):
        RoutingDecision(**payload)

    payload["workers"] = (_worker(), _worker("worker-synthesis", "synthesis"))
    payload["budget"] = _budget().model_copy(
        update={"max_parallel_tasks": 2},
    )
    payload["edges"] = (
        RoutingEdge(
            source_node_id="worker-research",
            target_node_id="unknown-worker",
            relation="depends_on",
        ),
    )
    with pytest.raises(ValidationError, match="unknown topology node"):
        RoutingDecision(**payload)


def test_direct_mode_rejects_multiple_workers_and_edges_reject_self_loops() -> None:
    payload = _decision_payload()
    payload["workers"] = (_worker(), _worker("worker-synthesis", "synthesis"))
    payload["budget"] = _budget().model_copy(
        update={"max_parallel_tasks": 2},
    )
    with pytest.raises(ValidationError, match="exactly one worker"):
        RoutingDecision(**payload)

    with pytest.raises(ValidationError, match="cannot target itself"):
        RoutingEdge(
            source_node_id="worker-research",
            target_node_id="worker-research",
            relation="depends_on",
        )


def test_initial_plan_forbids_amendment_metadata() -> None:
    payload = _plan_payload()
    payload["amendment"] = PlanAmendment(
        prior_execution_plan_id="execution-plan-000",
        prior_plan_version=1,
        reason="Provider policy changed.",
        policy_validation=AmendmentValidationStatus.APPROVED,
        budget_validation=AmendmentValidationStatus.APPROVED,
    )

    with pytest.raises(ValidationError, match="version 1 cannot be an amendment"):
        ExecutionPlan(**payload)


def test_amended_plan_requires_prior_plan_reason_and_approved_validations() -> None:
    payload = _plan_payload()
    payload["execution_plan_id"] = "execution-plan-002"
    payload["plan_version"] = 2

    with pytest.raises(ValidationError, match="requires amendment"):
        ExecutionPlan(**payload)

    payload["amendment"] = {
        "prior_execution_plan_id": "execution-plan-001",
        "prior_plan_version": 1,
        "reason": "  ",
        "policy_validation": AmendmentValidationStatus.APPROVED,
        "budget_validation": AmendmentValidationStatus.APPROVED,
    }
    with pytest.raises(ValidationError, match="reason"):
        ExecutionPlan(**payload)

    payload["amendment"]["reason"] = "Provider policy changed."
    payload["amendment"]["budget_validation"] = AmendmentValidationStatus.REJECTED
    with pytest.raises(ValidationError, match="approved policy and budget"):
        ExecutionPlan(**payload)


def test_amended_plan_requires_the_immediately_prior_distinct_plan() -> None:
    payload = _plan_payload()
    payload["execution_plan_id"] = "execution-plan-002"
    payload["plan_version"] = 3
    payload["amendment"] = PlanAmendment(
        prior_execution_plan_id="execution-plan-001",
        prior_plan_version=1,
        reason="Provider policy changed.",
        policy_validation=AmendmentValidationStatus.APPROVED,
        budget_validation=AmendmentValidationStatus.APPROVED,
    )
    with pytest.raises(ValidationError, match="immediately prior plan version"):
        ExecutionPlan(**payload)

    payload["amendment"] = PlanAmendment(
        prior_execution_plan_id="execution-plan-002",
        prior_plan_version=2,
        reason="Provider policy changed.",
        policy_validation=AmendmentValidationStatus.APPROVED,
        budget_validation=AmendmentValidationStatus.APPROVED,
    )
    payload["execution_plan_id"] = "execution-plan-002"
    with pytest.raises(ValidationError, match="distinct prior execution plan"):
        ExecutionPlan(**payload)


def test_valid_amendment_normalizes_reason_and_round_trips() -> None:
    payload = _plan_payload()
    payload["execution_plan_id"] = "execution-plan-002"
    payload["plan_version"] = 2
    payload["amendment"] = PlanAmendment(
        prior_execution_plan_id="execution-plan-001",
        prior_plan_version=1,
        reason="  Provider policy changed.  ",
        policy_validation=AmendmentValidationStatus.APPROVED,
        budget_validation=AmendmentValidationStatus.APPROVED,
    )

    plan = ExecutionPlan(**payload)
    restored = ExecutionPlan.model_validate_json(plan.canonical_json())

    assert plan.amendment is not None
    assert plan.amendment.reason == "Provider policy changed."
    assert restored == plan


def test_plan_versions_reject_boolean_integer_authority() -> None:
    payload = _plan_payload()
    payload["plan_version"] = True
    with pytest.raises(ValidationError, match="valid integer"):
        ExecutionPlan(**payload)

    with pytest.raises(ValidationError, match="valid integer"):
        PlanAmendment(
            prior_execution_plan_id="execution-plan-001",
            prior_plan_version=True,
            reason="Provider policy changed.",
            policy_validation=AmendmentValidationStatus.APPROVED,
            budget_validation=AmendmentValidationStatus.APPROVED,
        )


def test_plan_rejects_incompatible_schema_version_and_invalid_deadline() -> None:
    payload = _plan_payload()
    payload["schema_version"] = "2.0"
    with pytest.raises(ValidationError, match=r"Input should be '1\.0'"):
        ExecutionPlan(**payload)

    payload = _plan_payload()
    payload["deadline"] = NOW
    with pytest.raises(ValidationError, match="deadline must follow compiled_at"):
        ExecutionPlan(**payload)
