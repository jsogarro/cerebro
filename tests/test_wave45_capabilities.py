"""Wave 4.5B tests for capability grants issued from admitted plans."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.core.capabilities.issuer import PlanCapabilityIssuer
from src.core.contracts import (
    ExecutionBudget,
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
    RoutingEdge,
    SensitivityClass,
    TrustClassification,
    WorkerAssignment,
)
from src.models.execution_authority import ExecutionAuthorityBinding

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _worker(
    worker_id: str,
    tools: tuple[str, ...],
) -> WorkerAssignment:
    return WorkerAssignment(
        worker_id=worker_id,
        worker_type=f"{worker_id}-type",
        objective="Use only the tools admitted by the plan.",
        output_schema={},
        permission_scopes=("research:read",),
        tool_allowlist=tools,
    )


def _binding(
    *,
    workers: tuple[WorkerAssignment, ...] = (
        _worker("worker-one", ("mcp.academic_search", "mcp.format_citations")),
        _worker("worker-two", ("arithmetic",)),
    ),
    timeout_seconds: int = 90,
) -> ExecutionAuthorityBinding:
    return ExecutionAuthorityBinding.create_for_test(
        authority_id="authority-1",
        authority_version="1",
        run_id="binding-run",
        workflow_definition_id="workflow-1",
        routing_policy_id="policy-1",
        strategy="direct",
        collaboration_mode="hierarchical",
        domains=("research",),
        supervisor_id="supervisor-1",
        supervisor_type="research",
        workers=workers,
        edges=(
            RoutingEdge(
                source_node_id="supervisor-1",
                target_node_id=workers[0].worker_id,
                relation="delegates",
            ),
        ),
        provider_model_policy=ProviderModelPolicy(
            primary=ProviderModelRoute(provider="openrouter", model="openai/gpt-5"),
            fallback_mode=FallbackMode.FAIL_CLOSED,
            fallbacks=(),
            provider_allowlist=("openrouter",),
            model_allowlist=("openai/gpt-5",),
        ),
        budget=ExecutionBudget(
            max_cost_usd=Decimal("1.00"),
            max_total_tokens=1000,
            max_tool_invocations=10,
            max_parallel_tasks=len(workers),
            max_attempts_per_task=1,
            task_timeout_seconds=timeout_seconds,
        ),
        stop_conditions=("complete",),
        evaluator_requirements=(),
        deadline=NOW + timedelta(minutes=5),
        compiled_at=NOW,
    )


def test_issue_returns_one_static_safe_grant_per_allowlisted_tool() -> None:
    grants = PlanCapabilityIssuer.issue(
        _binding(),
        run_id="admitted-run",
        task_id="admitted-task",
        issued_at=NOW,
    )

    assert [grant.tool_name for grant in grants] == [
        "mcp.academic_search",
        "mcp.format_citations",
        "arithmetic",
    ]
    assert len({grant.grant_id for grant in grants}) == len(grants)
    assert all(grant.run_id == "admitted-run" for grant in grants)
    assert all(grant.task_id == "admitted-task" for grant in grants)
    assert all(grant.issued_at == NOW for grant in grants)
    assert all(
        grant.capability_scope == f"plan-issued:{grant.tool_name}" for grant in grants
    )
    assert all(grant.sensitivity is SensitivityClass.READ_ONLY for grant in grants)
    assert all(
        grant.max_input_trust is TrustClassification.EXTERNAL_UNTRUSTED
        for grant in grants
    )
    assert all(grant.tool_versions == ("1.0.0",) for grant in grants)
    assert all(not grant.requires_approval for grant in grants)


def test_issue_expiry_is_exactly_the_admitted_task_timeout() -> None:
    timeout = 37

    grants = PlanCapabilityIssuer.issue(
        _binding(timeout_seconds=timeout),
        run_id="run-1",
        task_id="task-1",
        issued_at=NOW,
    )

    assert grants
    assert all(grant.expires_at == NOW + timedelta(seconds=timeout) for grant in grants)


def test_issue_returns_no_grants_for_a_plan_with_no_tools() -> None:
    grants = PlanCapabilityIssuer.issue(
        _binding(
            workers=(
                _worker("worker-one", ()),
                _worker("worker-two", ()),
            )
        ),
        run_id="run-1",
        task_id="task-1",
        issued_at=NOW,
    )

    assert grants == ()
