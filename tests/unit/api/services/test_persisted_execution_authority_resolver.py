"""Unit tests for the DB-backed resolver's serialization and cache behavior.

No database is involved here: these tests pin the contract-preserving
serialize/deserialize round-trip and the cache-read semantics of ``resolve``,
independent of how the cache gets populated (``register``/
``warm_from_snapshots`` are exercised against a real Postgres testcontainer
under ``tests/integration/``).
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.api.services.execution_authority_resolver import (
    ExecutionAuthorityUnavailableError,
    PersistedExecutionAuthorityResolver,
    deserialize_execution_authority_binding,
    serialize_execution_authority_binding,
)
from src.core.contracts import (
    ExecutionBudget,
    FallbackMode,
    ProviderModelPolicy,
    ProviderModelRoute,
    RoutingEdge,
    WorkerAssignment,
)
from src.models.execution_authority import (
    ExecutionAuthorityBinding,
    ExecutionAuthorityReference,
)

NOW = datetime(2026, 8, 4, tzinfo=UTC)


def _make_binding(
    *, authority_id: str = "authority-1", authority_version: str = "1"
) -> ExecutionAuthorityBinding:
    return ExecutionAuthorityBinding.create_for_test(
        authority_id=authority_id,
        authority_version=authority_version,
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
            max_cost_usd=0,
            max_total_tokens=1,
            max_tool_invocations=0,
            max_parallel_tasks=2,
            max_attempts_per_task=1,
            task_timeout_seconds=1,
        ),
        stop_conditions=("complete",),
        evaluator_requirements=(),
        deadline=NOW + timedelta(minutes=5),
        compiled_at=NOW,
    )


# --- serialize / deserialize round-trip -------------------------------------


def test_round_trip_preserves_every_data_field() -> None:
    binding = _make_binding()

    configuration = serialize_execution_authority_binding(binding)
    rebuilt = deserialize_execution_authority_binding(
        configuration,
        authority_id=binding.authority_id,
        authority_version=binding.authority_version,
        clock=lambda: NOW,
        plan_id_factory=lambda: "plan-rebuilt",
    )

    assert rebuilt.authority_id == binding.authority_id
    assert rebuilt.authority_version == binding.authority_version
    assert rebuilt.run == binding.run
    assert rebuilt.workflow_definition == binding.workflow_definition
    assert rebuilt.routing_policy == binding.routing_policy
    assert rebuilt.domains == binding.domains
    assert rebuilt.supervisor_id == binding.supervisor_id
    assert rebuilt.supervisor_type == binding.supervisor_type
    assert rebuilt.workers == binding.workers
    assert rebuilt.edges == binding.edges
    assert rebuilt.provider_model_policy == binding.provider_model_policy
    assert rebuilt.budget == binding.budget
    assert rebuilt.stop_conditions == binding.stop_conditions
    assert rebuilt.evaluator_requirements == binding.evaluator_requirements
    assert rebuilt.deadline == binding.deadline


def test_configuration_excludes_the_non_serializable_callables() -> None:
    binding = _make_binding()

    configuration = serialize_execution_authority_binding(binding)

    assert "plan_id_factory" not in configuration
    assert "clock" not in configuration


def test_deserialize_supplies_the_caller_provided_clock_and_plan_id_factory() -> None:
    binding = _make_binding()
    configuration = serialize_execution_authority_binding(binding)

    rebuilt = deserialize_execution_authority_binding(
        configuration,
        authority_id=binding.authority_id,
        authority_version=binding.authority_version,
        clock=lambda: NOW,
        plan_id_factory=lambda: "plan-rebuilt",
    )

    assert rebuilt.clock() == NOW
    assert rebuilt.plan_id_factory() == "plan-rebuilt"


# --- cache-backed resolve -----------------------------------------------------


def test_resolve_misses_raise_unavailable_when_the_cache_is_empty() -> None:
    resolver = PersistedExecutionAuthorityResolver()

    with pytest.raises(ExecutionAuthorityUnavailableError):
        resolver.resolve(
            ExecutionAuthorityReference(
                authority_id="authority-1", authority_version="1"
            )
        )


def test_cache_binding_makes_resolve_return_it() -> None:
    resolver = PersistedExecutionAuthorityResolver()
    binding = _make_binding()

    resolver.cache_binding(binding)

    resolved = resolver.resolve(
        ExecutionAuthorityReference(
            authority_id=binding.authority_id,
            authority_version=binding.authority_version,
        )
    )
    assert resolved is binding


def test_resolve_is_scoped_to_the_exact_authority_id_and_version() -> None:
    resolver = PersistedExecutionAuthorityResolver()
    resolver.cache_binding(
        _make_binding(authority_id="authority-1", authority_version="1")
    )

    with pytest.raises(ExecutionAuthorityUnavailableError):
        resolver.resolve(
            ExecutionAuthorityReference(
                authority_id="authority-1", authority_version="2"
            )
        )


def test_default_clock_and_plan_id_factory_are_usable() -> None:
    resolver = PersistedExecutionAuthorityResolver()

    assert isinstance(resolver._clock(), datetime)
    assert isinstance(resolver._plan_id_factory(), str)
