"""Crash matrix: is cancellation truthful, including across a restart?

``cancel_execution`` records only the cancellation *request* (CANCELLING) for
a run that has reached RUNNING; CANCELLED is recorded by the workflow once its
task has actually stopped (``_record_requested_cancellation``). A run
cancelled before it started legitimately reaches CANCELLED immediately. The
tests also probe what a restart does with a run stuck at CANCELLING.
"""

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
)
from src.core.contracts import AttemptStatus, RunStatus, TaskStatus
from src.repositories.run_lifecycle_repository import RunLifecycleRepository
from tests.integration.crash_fixtures import ORG_ID, make_binding, make_project

pytestmark = [pytest.mark.integration]


def _service(session_factory, resolver) -> DirectExecutionService:
    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()
    return DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=session_factory,
        execution_authority_resolver=resolver,
    )


@pytest.mark.asyncio
async def test_cancelling_a_running_run_reaches_only_cancelling_never_cancelled(
    test_engine: AsyncEngine,
) -> None:
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "cancel-semantics-running"
    binding = make_binding(run_id, authority_id="cancel-semantics-running-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("cancel-semantics-running-authority", "1"): binding}
    )
    service = _service(session_factory, resolver)
    project = make_project()
    execution_status = ExecutionStatus(
        execution_id="exec-cancel-running",
        project_id=str(project.id),
        status="running",
        current_phase="supervisor_execution",
    )
    await service._admit_run(execution_status, binding, project)
    await service._persist_transition(
        execution_status,
        run_target=RunStatus.RUNNING,
        task_target=TaskStatus.RUNNING,
        attempt_target=AttemptStatus.RUNNING,
        event_type="run.started",
        payload={},
    )
    service.active_executions[execution_status.execution_id] = execution_status

    cancelled = await service.cancel_execution(execution_status.execution_id)
    assert cancelled is True

    async with session_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            run_id, organization_id=ORG_ID
        )
        assert row is not None
        assert row.status == RunStatus.CANCELLING.value
        assert row.status != RunStatus.CANCELLED.value
        assert row.completed_at is None  # CANCELLING is not terminal

    # Calling cancel again must not attempt an illegal CANCELLING ->
    # CANCELLING transition or otherwise raise.
    cancelled_again = await service.cancel_execution(execution_status.execution_id)
    assert cancelled_again is False  # in-memory status already "cancelled"

    await service.close()


@pytest.mark.asyncio
async def test_cancelling_a_not_yet_started_run_legitimately_reaches_cancelled(
    test_engine: AsyncEngine,
) -> None:
    """A run cancelled before it ever reached RUNNING has no in-flight work
    to misrepresent — Run.request_cancellation legitimately transitions
    straight to the real terminal CANCELLED for it. This is the boundary of
    3C's "can only ever record CANCELLING" claim, not a contradiction of
    it."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "cancel-semantics-pending"
    binding = make_binding(run_id, authority_id="cancel-semantics-pending-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("cancel-semantics-pending-authority", "1"): binding}
    )
    service = _service(session_factory, resolver)
    project = make_project()
    execution_status = ExecutionStatus(
        execution_id="exec-cancel-pending",
        project_id=str(project.id),
        status="pending",
        current_phase="initialization",
    )
    await service._admit_run(execution_status, binding, project)
    service.active_executions[execution_status.execution_id] = execution_status

    cancelled = await service.cancel_execution(execution_status.execution_id)
    assert cancelled is True

    async with session_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            run_id, organization_id=ORG_ID
        )
        assert row is not None
        assert row.status == RunStatus.CANCELLED.value
        assert row.completed_at is not None  # genuinely terminal

    await service.close()


@pytest.mark.asyncio
async def test_restart_resurrects_a_cancelling_run_as_running(
    test_engine: AsyncEngine,
) -> None:
    """FINDING: a run stuck at CANCELLING when the process disappears comes
    back after a restart mapped to execution status "running"
    (``_RUN_STATUS_TO_EXECUTION_STATUS[CANCELLING] == "running"``) and
    appears in ``list_active_executions()`` exactly like any other in-flight
    run. Nothing in the recovered ``ExecutionStatus`` — not the status
    string, not current_phase, not any other field — records that a
    cancellation was ever requested. A client that received "cancellation
    accepted" before the crash would, after the restart, see this run
    reported as ordinarily active again. There is also no mechanism anywhere
    in this codebase that ever advances a CANCELLING run to a terminal
    state, restart or not — so this is not merely a display quirk, it is a
    run with no path to ever finishing, silently disguised as a normal
    in-progress run once the process that requested its cancellation is
    gone."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "cancel-semantics-restart"
    binding = make_binding(run_id, authority_id="cancel-semantics-restart-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("cancel-semantics-restart-authority", "1"): binding}
    )
    service_a = _service(session_factory, resolver)
    project = make_project()
    execution_status = ExecutionStatus(
        execution_id="exec-cancel-restart",
        project_id=str(project.id),
        status="running",
        current_phase="supervisor_execution",
    )
    await service_a._admit_run(execution_status, binding, project)
    await service_a._persist_transition(
        execution_status,
        run_target=RunStatus.RUNNING,
        task_target=TaskStatus.RUNNING,
        attempt_target=AttemptStatus.RUNNING,
        event_type="run.started",
        payload={},
    )
    service_a.active_executions[execution_status.execution_id] = execution_status
    assert await service_a.cancel_execution(execution_status.execution_id) is True

    async with session_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            run_id, organization_id=ORG_ID
        )
        assert row is not None
        assert row.status == RunStatus.CANCELLING.value

    # "The process disappears": a brand-new service, sharing only Postgres.
    service_b = DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=AsyncMock(),
        supervisor_factory=Mock(),
        session_factory=session_factory,
    )
    restored = await service_b.restore_active_executions()
    assert restored == 1

    recovered = service_b.active_executions["exec-cancel-restart"]
    # Nothing resumes the work, so the run is failed rather than resurrected
    # as running.
    assert recovered.status == "failed"

    active = await service_b.list_active_executions()
    assert not any(e.execution_id == "exec-cancel-restart" for e in active)

    await service_a.close()
    await service_b.close()


@pytest.mark.asyncio
async def test_workflow_records_cancelled_once_its_task_has_stopped(
    test_engine: AsyncEngine,
) -> None:
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "cancel-semantics-stopped"
    binding = make_binding(run_id, authority_id="cancel-semantics-stopped-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("cancel-semantics-stopped-authority", "1"): binding}
    )
    service = _service(session_factory, resolver)
    project = make_project()
    execution_status = ExecutionStatus(
        execution_id="exec-cancel-stopped",
        project_id=str(project.id),
        status="running",
        current_phase="supervisor_execution",
    )
    await service._admit_run(execution_status, binding, project)
    await service._persist_transition(
        execution_status,
        run_target=RunStatus.RUNNING,
        task_target=TaskStatus.RUNNING,
        attempt_target=AttemptStatus.RUNNING,
        event_type="run.started",
        payload={},
    )
    service.active_executions[execution_status.execution_id] = execution_status
    assert await service.cancel_execution(execution_status.execution_id) is True

    await service._record_requested_cancellation(execution_status)

    async with session_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            run_id, organization_id=ORG_ID
        )
        assert row is not None
        assert row.status == RunStatus.CANCELLED.value
        assert row.completed_at is not None
    assert execution_status.status == "cancelled"

    await service.close()
