"""Crash matrix: worker-side faults not already covered by Packet 3C's own
restart tests.

``tests/integration/test_direct_execution_restart_recovery.py`` (3C) already
proves the two headline restart cases: a non-terminal run is rehydrated into
``active_executions``, and a terminal run is not. This file extends that
coverage along three axes the packet's own tests don't reach: (1) a crash
before admission ever completes leaves nothing to resurrect — not even a
partial ghost; (2) the *content* journaled for a terminal run — the actual
material recovery is supposed to preserve — is correct for both SUCCEEDED
and FAILED outcomes, not just the fact that terminal runs are excluded from
resurrection; (3) a run interrupted mid-flight is recovered as still active,
never reporting success it did not achieve.
"""

import asyncio
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
from src.models.execution_authority import ExecutionAuthorityReference
from src.repositories.run_lifecycle_repository import RunLifecycleRepository
from tests.integration.crash_fixtures import (
    ORG_ID,
    _RoutingDecisionStub,
    make_binding,
    make_project,
)

pytestmark = [pytest.mark.integration]


def _service(session_factory, resolver, *, bridge=None) -> DirectExecutionService:
    if bridge is None:
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
async def test_crash_before_admission_leaves_nothing_to_resurrect(
    test_engine: AsyncEngine,
) -> None:
    """A worker crash (or a routing failure) before ``_admit_run`` is ever
    reached must leave zero durable rows and zero in-memory executions — a
    restart must not invent a run that never got far enough to exist."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "worker-crash-pre-admission"
    binding = make_binding(run_id, authority_id="worker-crash-pre-admission-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("worker-crash-pre-admission-authority", "1"): binding}
    )
    router = AsyncMock()
    router.route.side_effect = RuntimeError("simulated routing crash")
    service = _service(session_factory, resolver)
    service.masr_router = router
    project = make_project()

    with pytest.raises(RuntimeError, match="simulated routing crash"):
        await service.start_research_execution(
            project,
            authority_reference=ExecutionAuthorityReference(
                authority_id="worker-crash-pre-admission-authority",
                authority_version="1",
            ),
        )

    assert service.active_executions == {}

    async with session_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            run_id, organization_id=ORG_ID
        )
        assert row is None

    fresh = DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=AsyncMock(),
        supervisor_factory=Mock(),
        session_factory=session_factory,
    )
    restored = await fresh.restore_active_executions()
    assert restored == 0
    assert fresh.active_executions == {}

    await service.close()
    await fresh.close()


@pytest.mark.asyncio
async def test_journaled_result_for_a_succeeded_run_preserves_the_real_output(
    test_engine: AsyncEngine,
) -> None:
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "worker-crash-journal-succeeded"
    binding = make_binding(
        run_id, authority_id="worker-crash-journal-succeeded-authority"
    )
    resolver = MappingExecutionAuthorityResolver(
        {("worker-crash-journal-succeeded-authority", "1"): binding}
    )
    service = _service(session_factory, resolver)
    project = make_project()
    execution_status = ExecutionStatus(
        execution_id="exec-journal-succeeded",
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
    execution_status.final_output = {"answer": "42", "sources": ["a", "b"]}
    execution_status.agent_results = {"literature": {"answer": "42"}}
    await service._persist_transition(
        execution_status,
        run_target=RunStatus.SUCCEEDED,
        task_target=TaskStatus.SUCCEEDED,
        attempt_target=AttemptStatus.SUCCEEDED,
        event_type="run.succeeded",
        payload={},
    )

    async with session_factory() as session:
        lifecycle_repo = RunLifecycleRepository(session)
        row = await lifecycle_repo.get_run(run_id, organization_id=ORG_ID)
        assert row is not None
        assert row.status == RunStatus.SUCCEEDED.value
        assert row.completed_at is not None
        tasks = await lifecycle_repo.get_tasks_for_run(run_id, organization_id=ORG_ID)
        attempts = await lifecycle_repo.get_attempts_for_task(
            tasks[0].task_id, organization_id=ORG_ID
        )
        journaled = attempts[-1].journaled_result
        assert journaled is not None
        assert journaled["final_output"] == {"answer": "42", "sources": ["a", "b"]}
        assert journaled["agent_results"] == {"literature": {"answer": "42"}}
        assert journaled["errors"] == []

    # A succeeded run must not be resurrected as active on restart — its
    # true outcome is already durably recorded above.
    fresh = DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=AsyncMock(),
        supervisor_factory=Mock(),
        session_factory=session_factory,
    )
    await fresh.restore_active_executions()
    assert "exec-journal-succeeded" not in fresh.active_executions

    await service.close()
    await fresh.close()


@pytest.mark.asyncio
async def test_journaled_result_for_a_failed_run_preserves_the_real_error(
    test_engine: AsyncEngine,
) -> None:
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "worker-crash-journal-failed"
    binding = make_binding(run_id, authority_id="worker-crash-journal-failed-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("worker-crash-journal-failed-authority", "1"): binding}
    )
    service = _service(session_factory, resolver)
    project = make_project()
    execution_status = ExecutionStatus(
        execution_id="exec-journal-failed",
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
    execution_status.errors.append("supervisor bridge raised: tool timeout")
    await service._persist_transition(
        execution_status,
        run_target=RunStatus.FAILED,
        task_target=TaskStatus.FAILED,
        attempt_target=AttemptStatus.FAILED,
        event_type="run.failed",
        payload={"error": "tool timeout"},
        reason="tool timeout",
    )

    async with session_factory() as session:
        lifecycle_repo = RunLifecycleRepository(session)
        row = await lifecycle_repo.get_run(run_id, organization_id=ORG_ID)
        assert row is not None
        assert row.status == RunStatus.FAILED.value
        assert row.status_reason == "tool timeout"
        tasks = await lifecycle_repo.get_tasks_for_run(run_id, organization_id=ORG_ID)
        attempts = await lifecycle_repo.get_attempts_for_task(
            tasks[0].task_id, organization_id=ORG_ID
        )
        journaled = attempts[-1].journaled_result
        assert journaled is not None
        assert journaled["final_output"] is None
        assert journaled["errors"] == ["supervisor bridge raised: tool timeout"]

    fresh = DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=AsyncMock(),
        supervisor_factory=Mock(),
        session_factory=session_factory,
    )
    await fresh.restore_active_executions()
    assert "exec-journal-failed" not in fresh.active_executions

    await service.close()
    await fresh.close()


@pytest.mark.asyncio
async def test_worker_crash_mid_flight_recovers_as_active_never_fabricating_success(
    test_engine: AsyncEngine,
) -> None:
    """A worker crash while the supervisor bridge is genuinely still doing
    work — the closest a test gets to a real process kill mid-execution —
    must recover the run as still running, and specifically must not report
    a final_output it never produced."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "worker-crash-mid-flight"
    binding = make_binding(run_id, authority_id="worker-crash-mid-flight-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("worker-crash-mid-flight-authority", "1"): binding}
    )
    router = AsyncMock()
    router.route.return_value = _RoutingDecisionStub()

    supervisor_started = asyncio.Event()
    never_returns = asyncio.Event()

    async def _blocking_execute_plan(*_args, **_kwargs):
        supervisor_started.set()
        await never_returns.wait()  # the "worker" is killed before this fires

    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()
    bridge.execute_execution_plan.side_effect = _blocking_execute_plan

    service_a = _service(session_factory, resolver, bridge=bridge)
    service_a.masr_router = router
    project = make_project()
    execution_id = await service_a.start_research_execution(
        project,
        authority_reference=ExecutionAuthorityReference(
            authority_id="worker-crash-mid-flight-authority", authority_version="1"
        ),
    )
    await asyncio.wait_for(supervisor_started.wait(), timeout=5)

    fresh = DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=AsyncMock(),
        supervisor_factory=Mock(),
        session_factory=session_factory,
    )
    await fresh.restore_active_executions()

    recovered = fresh.active_executions[execution_id]
    assert recovered.status == "running"
    assert recovered.final_output is None
    assert recovered.agent_results == {}

    never_returns.set()
    await service_a.close()
    await fresh.close()
