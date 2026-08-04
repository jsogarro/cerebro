"""Integration tests for the DB-backed resolver's persistence write path.

``register`` and ``warm_from_snapshots`` are the only two methods that touch
the database; everything else about the resolver is covered without one in
``tests/unit/api/services/test_persisted_execution_authority_resolver.py``.
These prove the register-then-resolve path a fresh admission takes, and the
cold-start path a restarted process takes to make its cache truthful again.
"""

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.services.execution_authority_resolver import (
    ExecutionAuthorityUnavailableError,
    PersistedExecutionAuthorityResolver,
)
from src.core.contracts import (
    ExecutionBudget,
    FallbackMode,
    PinnedComponentKind,
    PinnedComponentVersion,
    PinnedVersions,
    ProviderModelPolicy,
    ProviderModelRoute,
    Run,
    WorkerAssignment,
)
from src.models.execution_authority import (
    ExecutionAuthorityBinding,
    ExecutionAuthorityReference,
)
from src.repositories.run_lifecycle_repository import RunLifecycleRepository

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 8, 4, tzinfo=UTC)
ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")

PINNED_VERSIONS = PinnedVersions(
    workflow_definition_id="workflow-1",
    workflow_definition_version="1",
    routing_policy_id="policy-1",
    routing_policy_version="1",
    event_envelope_version="1.0",
    components=(
        PinnedComponentVersion(
            kind=PinnedComponentKind.PROMPT,
            component_id="research.synthesis",
            version="3",
        ),
    ),
)


def _with_tenant(
    binding: ExecutionAuthorityBinding, *, idempotency_key: str = "key-1"
) -> ExecutionAuthorityBinding:
    """Fix up ``create_for_test``'s hardcoded ``tenant_id="tenant-1"`` to a
    real UUID string, since the tenant identity contract requires
    ``tenant_id`` to round-trip through ``uuid.UUID(...)``. Also allows
    overriding the equally hardcoded ``idempotency_key`` so two bindings for
    the same tenant don't collide on ``(tenant_id, idempotency_key)``."""
    return dataclasses.replace(
        binding,
        run=binding.run.model_copy(
            update={"tenant_id": str(ORG_ID), "idempotency_key": idempotency_key}
        ),
    )


def _make_binding(run_id: str = "run-1") -> ExecutionAuthorityBinding:
    return _with_tenant(_make_raw_binding(run_id))


def _make_raw_binding(run_id: str) -> ExecutionAuthorityBinding:
    return ExecutionAuthorityBinding.create_for_test(
        authority_id="authority-1",
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
            max_parallel_tasks=2,
            max_attempts_per_task=1,
            task_timeout_seconds=1,
        ),
        stop_conditions=("complete",),
        evaluator_requirements=(),
        deadline=NOW + timedelta(minutes=5),
        compiled_at=NOW,
    )


async def _seed_run(session: AsyncSession, binding: ExecutionAuthorityBinding) -> Run:
    await RunLifecycleRepository(session).create_run(
        binding.run, organization_id=ORG_ID
    )
    await session.flush()
    return binding.run


@pytest.mark.asyncio
async def test_register_persists_and_caches_so_resolve_succeeds(
    db_session: AsyncSession,
) -> None:
    binding = _make_binding()
    await _seed_run(db_session, binding)
    resolver = PersistedExecutionAuthorityResolver(clock=lambda: NOW)

    await resolver.register(
        db_session,
        binding=binding,
        organization_id=ORG_ID,
        config_snapshot_id="snapshot-1",
        pinned_versions=PINNED_VERSIONS,
    )

    resolved = resolver.resolve(
        ExecutionAuthorityReference(authority_id="authority-1", authority_version="1")
    )
    assert resolved.run.run_id == binding.run.run_id
    assert resolved.workers == binding.workers


@pytest.mark.asyncio
async def test_warm_from_snapshots_rebuilds_the_cache_after_a_restart(
    db_session: AsyncSession,
) -> None:
    binding = _make_binding()
    await _seed_run(db_session, binding)

    # First process: registers and would normally serve requests, then dies.
    writer = PersistedExecutionAuthorityResolver(clock=lambda: NOW)
    await writer.register(
        db_session,
        binding=binding,
        organization_id=ORG_ID,
        config_snapshot_id="snapshot-1",
        pinned_versions=PINNED_VERSIONS,
    )
    await db_session.commit()

    # A fresh resolver with an empty cache — simulating the restarted process
    # — cannot resolve anything until it warms from persistence.
    restarted = PersistedExecutionAuthorityResolver(clock=lambda: NOW)
    with pytest.raises(ExecutionAuthorityUnavailableError):
        restarted.resolve(
            ExecutionAuthorityReference(
                authority_id="authority-1", authority_version="1"
            )
        )

    loaded_count = await restarted.warm_from_snapshots(db_session)

    assert loaded_count == 1
    resolved = restarted.resolve(
        ExecutionAuthorityReference(authority_id="authority-1", authority_version="1")
    )
    assert resolved.run.run_id == binding.run.run_id
    assert resolved.provider_model_policy == binding.provider_model_policy
    assert resolved.budget == binding.budget


@pytest.mark.asyncio
async def test_warm_from_snapshots_loads_every_persisted_binding(
    db_session: AsyncSession,
) -> None:
    binding_a = _make_binding(run_id="run-a")
    await _seed_run(db_session, binding_a)
    await PersistedExecutionAuthorityResolver().register(
        db_session,
        binding=binding_a,
        organization_id=ORG_ID,
        config_snapshot_id="snapshot-a",
        pinned_versions=PINNED_VERSIONS,
    )

    binding_b = _with_tenant(
        ExecutionAuthorityBinding.create_for_test(
            authority_id="authority-2",
            authority_version="1",
            run_id="run-b",
            workflow_definition_id="workflow-1",
            routing_policy_id="policy-1",
            strategy="balanced",
            collaboration_mode="hierarchical",
            domains=("research",),
            supervisor_id="supervisor-1",
            supervisor_type="research",
            workers=(),
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
                task_timeout_seconds=1,
            ),
            stop_conditions=("complete",),
            evaluator_requirements=(),
            deadline=NOW + timedelta(minutes=5),
            compiled_at=NOW,
        ),
        idempotency_key="key-2",
    )
    await _seed_run(db_session, binding_b)
    await PersistedExecutionAuthorityResolver().register(
        db_session,
        binding=binding_b,
        organization_id=ORG_ID,
        config_snapshot_id="snapshot-b",
        pinned_versions=PINNED_VERSIONS,
    )

    resolver = PersistedExecutionAuthorityResolver()
    loaded_count = await resolver.warm_from_snapshots(db_session)

    assert loaded_count == 2
    assert (
        resolver.resolve(
            ExecutionAuthorityReference(
                authority_id="authority-1", authority_version="1"
            )
        ).run.run_id
        == "run-a"
    )
    assert (
        resolver.resolve(
            ExecutionAuthorityReference(
                authority_id="authority-2", authority_version="1"
            )
        ).run.run_id
        == "run-b"
    )
