"""Admission pins a run to the configuration it was admitted with.

The wave's bar includes "old runs keep pinned semantics after deployment".
That is only true if something on the production write path actually writes
``agent_run_config_snapshots``: a snapshot written solely by tests pins
nothing. These tests exercise the real admission path and then read the
snapshot back the way a cold start does.
"""

from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.api.services.direct_execution_service import (
    DirectExecutionService,
    ExecutionStatus,
)
from src.api.services.execution_authority_resolver import (
    MappingExecutionAuthorityResolver,
    PersistedExecutionAuthorityResolver,
)
from src.models.execution_authority import ExecutionAuthorityReference
from src.repositories.run_config_snapshot_repository import (
    RunConfigSnapshotRepository,
    hash_configuration,
)
from tests.integration.crash_fixtures import (
    ORG_ID,
    _RoutingDecisionStub,
    make_binding,
    make_project,
)

pytestmark = [pytest.mark.integration]


def _service(session_factory: Any, resolver: Any) -> DirectExecutionService:
    router = AsyncMock()
    router.route.return_value = _RoutingDecisionStub()
    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()
    return DirectExecutionService(
        masr_router=router,
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=session_factory,
        execution_authority_resolver=resolver,
    )


@pytest.mark.asyncio
async def test_admission_persists_the_runs_configuration_snapshot(
    test_engine: AsyncEngine,
) -> None:
    """Admitting a run must record the frozen configuration and version pins
    it was admitted under, in the same transaction as the run row."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "pinned-admission-run"
    binding = make_binding(run_id, authority_id="pinned-admission-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("pinned-admission-authority", "1"): binding}
    )
    service = _service(session_factory, resolver)
    project = make_project()
    execution_status = ExecutionStatus(
        execution_id="exec-pinned-admission",
        project_id=str(project.id),
        status="pending",
        current_phase="initialization",
    )

    await service._admit_run(execution_status, binding, project)
    assert execution_status.run_id == run_id

    async with session_factory() as session:
        snapshot = await RunConfigSnapshotRepository(session).get_by_run(
            run_id, organization_id=ORG_ID
        )
        assert snapshot is not None
        assert snapshot.authority_id == "pinned-admission-authority"
        assert snapshot.authority_version == "1"
        assert snapshot.workflow_definition_id == binding.run.workflow_definition_id
        assert (
            snapshot.workflow_definition_version
            == binding.run.workflow_definition_version
        )
        assert snapshot.routing_policy_id == binding.run.routing_policy_id
        assert snapshot.routing_policy_version == binding.run.routing_policy_version
        pinned = snapshot.pinned_versions
        assert pinned["workflow_definition_version"] == (
            binding.run.workflow_definition_version
        )
        assert pinned["routing_policy_version"] == binding.run.routing_policy_version
        assert pinned["event_envelope_version"]
        # The stored digest must describe the stored configuration, so
        # tampering is detectable without trusting the writer.
        assert snapshot.content_sha256 == hash_configuration(snapshot.configuration)

    await service.close()


@pytest.mark.asyncio
async def test_a_cold_start_can_resolve_the_authority_an_admitted_run_pinned(
    test_engine: AsyncEngine,
) -> None:
    """The point of persisting the snapshot: a fresh process warms its
    authority cache from the database and can resolve the exact binding the
    run was admitted with, without the original in-memory mapping."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "cold-start-pinned-run"
    binding = make_binding(run_id, authority_id="cold-start-pinned-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("cold-start-pinned-authority", "1"): binding}
    )
    service = _service(session_factory, resolver)
    project = make_project()
    execution_status = ExecutionStatus(
        execution_id="exec-cold-start-pinned",
        project_id=str(project.id),
        status="pending",
        current_phase="initialization",
    )
    await service._admit_run(execution_status, binding, project)

    cold_resolver = PersistedExecutionAuthorityResolver()
    async with session_factory() as session:
        assert await cold_resolver.warm_from_snapshots(session) >= 1

    restored = cold_resolver.resolve(
        ExecutionAuthorityReference(
            authority_id="cold-start-pinned-authority", authority_version="1"
        ),
        organization_id=ORG_ID,
    )
    assert restored.run.run_id == run_id
    assert restored.run.workflow_definition_version == (
        binding.run.workflow_definition_version
    )
    assert restored.routing_policy.routing_policy_version == (
        binding.routing_policy.routing_policy_version
    )
    assert restored.workers == binding.workers

    await service.close()


@pytest.mark.asyncio
async def test_admission_through_the_public_entry_point_pins_the_run(
    test_engine: AsyncEngine,
) -> None:
    """The snapshot must be written by the path production actually calls,
    not only by the private helper."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "public-entry-pinned-run"
    binding = make_binding(run_id, authority_id="public-entry-pinned-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("public-entry-pinned-authority", "1"): binding}
    )
    service = _service(session_factory, resolver)

    await service.start_research_execution(
        make_project(),
        authority_reference=ExecutionAuthorityReference(
            authority_id="public-entry-pinned-authority", authority_version="1"
        ),
        organization_id=ORG_ID,
    )

    async with session_factory() as session:
        snapshot = await RunConfigSnapshotRepository(session).get_by_run(
            run_id, organization_id=ORG_ID
        )
        assert snapshot is not None
        assert snapshot.authority_id == "public-entry-pinned-authority"

    await service.close()
