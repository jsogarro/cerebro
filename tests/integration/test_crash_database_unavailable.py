"""Crash matrix: total or partial database unavailability.

``_admit_run``, ``_persist_transition``, ``_persist_cancellation``, and
``restore_active_executions`` are all documented as "best-effort" — a
missing or failing session factory logs a warning and lets execution
continue in memory-only mode rather than crashing the request. These tests
prove that degradation actually holds against fault-injected DB failures,
and separately prove the sharpest edge of it: a database outage that lands
at exactly the terminal-transition instant.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.ai_brain.router.routing_types import RoutingExecutionPolicy
from src.api.services.direct_execution_service import (
    AWAITING_DURABLE_CONFIRMATION_PHASE,
    DirectExecutionService,
    DurableWriteOutcome,
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


class _AlwaysFailingSessionFactory:
    """Simulates a database that is unreachable for every call."""

    def __call__(self) -> Any:
        raise ConnectionError("simulated database unavailable")


class _ToggleFailingSessionFactory:
    """Wraps a real session factory; fails on demand once ``broken`` is set.

    Models a database that goes down mid-execution rather than being down
    from the start.
    """

    def __init__(self, real_factory: Any) -> None:
        self._real_factory = real_factory
        self.broken = False

    def __call__(self) -> Any:
        if self.broken:
            raise ConnectionError("simulated database outage")
        return self._real_factory()


@pytest.mark.asyncio
async def test_execution_completes_in_memory_when_database_is_never_reachable() -> None:
    """Fixture-mode execution with a session factory that always raises must
    still reach 'completed' in memory — persistence failing must never
    surface as an execution failure."""

    run_id = "db-down-fixture-run"
    binding = make_binding(run_id, authority_id="db-down-fixture-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("db-down-fixture-authority", "1"): binding}
    )
    router = AsyncMock()
    router.route.return_value = _RoutingDecisionStub()
    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()

    service = DirectExecutionService(
        masr_router=router,
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=_AlwaysFailingSessionFactory(),
        execution_authority_resolver=resolver,
    )
    project = make_project()
    execution_id = await service.start_research_execution(
        project,
        execution_policy=RoutingExecutionPolicy.fixture(),
        fixture_result={"result": "computed without a database"},
        authority_reference=ExecutionAuthorityReference(
            authority_id="db-down-fixture-authority", authority_version="1"
        ),
    )

    for _ in range(50):
        if service.active_executions[execution_id].status in (
            "completed",
            "failed",
        ):
            break
        await asyncio.sleep(0.02)

    execution = service.active_executions[execution_id]
    assert execution.status == "completed"
    assert execution.final_output == {"result": "computed without a database"}
    # Admission never persisted — a broken database must not fabricate a
    # run_id that does not exist anywhere durable.
    assert execution.run_id is None

    await service.close()


@pytest.mark.asyncio
async def test_restore_active_executions_returns_zero_when_database_unreachable() -> (
    None
):
    service = DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=AsyncMock(),
        supervisor_factory=Mock(),
        session_factory=_AlwaysFailingSessionFactory(),
    )
    restored = await service.restore_active_executions()
    assert restored == 0
    assert service.active_executions == {}
    await service.close()


@pytest.mark.asyncio
async def test_cancellation_degrades_gracefully_when_database_goes_down(
    test_engine: AsyncEngine,
) -> None:
    """Cancelling a run whose database has gone down since admission must
    still report success to the caller (the in-memory execution really is
    being cancelled) without raising, even though the cancellation can't be
    made durable right now."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    toggle = _ToggleFailingSessionFactory(session_factory)
    run_id = "db-down-cancel-run"
    binding = make_binding(run_id, authority_id="db-down-cancel-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("db-down-cancel-authority", "1"): binding}
    )
    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()
    service = DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=toggle,
        execution_authority_resolver=resolver,
    )
    project = make_project()
    execution_status = ExecutionStatus(
        execution_id="exec-db-down-cancel",
        project_id=str(project.id),
        status="pending",
        current_phase="initialization",
    )
    await service._admit_run(execution_status, binding, project)
    assert execution_status.run_id == run_id
    service.active_executions[execution_status.execution_id] = execution_status

    toggle.broken = True
    cancelled = await service.cancel_execution(execution_status.execution_id)

    assert cancelled is True
    assert execution_status.status == "cancelled"

    toggle.broken = False
    async with session_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            run_id, organization_id=ORG_ID
        )
        assert row is not None
        # The database never learned about the cancellation request — it is
        # still sitting at whatever the last successfully persisted status
        # was (QUEUED, since this run never reached RUNNING).
        assert row.status == RunStatus.QUEUED.value

    await service.close()


@pytest.mark.asyncio
async def test_db_outage_at_terminal_transition_does_not_lose_true_outcome(
    test_engine: AsyncEngine,
) -> None:
    """A database outage landing exactly on the terminal write must neither
    fabricate success nor drop the outcome on the floor: the status stays
    non-terminal while the durable layer has not accepted it, the real
    outcome is held for reconciliation, and it lands — journaled
    ``final_output`` included — once the database is back."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    toggle = _ToggleFailingSessionFactory(session_factory)
    run_id = "db-down-terminal-run"
    binding = make_binding(run_id, authority_id="db-down-terminal-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("db-down-terminal-authority", "1"): binding}
    )
    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()
    service = DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=toggle,
        execution_authority_resolver=resolver,
    )
    service.durable_write_retry_seconds = 0.01
    # Reconcile only when this test says so: a background pass landing the
    # write mid-assertion would make the timing, not the behavior, the thing
    # under test.
    service.durable_reconcile_interval_seconds = 60.0
    project = make_project()
    execution_status = ExecutionStatus(
        execution_id="exec-db-down-terminal",
        project_id=str(project.id),
        status="pending",
        current_phase="initialization",
    )
    await service._admit_run(execution_status, binding, project)
    assert execution_status.run_id == run_id

    # Drive the run to durably RUNNING while the database is healthy — this
    # is the state a real "active golden run" would be in when disaster
    # strikes.
    await service._persist_transition(
        execution_status,
        run_target=RunStatus.RUNNING,
        task_target=TaskStatus.RUNNING,
        attempt_target=AttemptStatus.RUNNING,
        event_type="run.started",
        payload={},
    )
    execution_status.status = "running"
    assert execution_status._run is not None
    assert execution_status._run.status == RunStatus.RUNNING

    # The real answer the workflow computed, exactly as
    # _execute_research_workflow would set it before persisting SUCCEEDED.
    execution_status.final_output = {"result": "the real answer"}
    execution_status.agent_results = {"result": "the real answer"}

    # The database goes down at the worst possible instant: right as the
    # terminal transition tries to persist.
    toggle.broken = True
    outcome = await service._persist_transition(
        execution_status,
        run_target=RunStatus.SUCCEEDED,
        task_target=TaskStatus.SUCCEEDED,
        attempt_target=AttemptStatus.SUCCEEDED,
        event_type="run.succeeded",
        payload={},
    )
    assert outcome is DurableWriteOutcome.UNAVAILABLE

    # What _execute_research_workflow does with that answer: it refuses to
    # publish a terminal status the database never took, and says which
    # outcome is waiting on it instead.
    service._settle_terminal_state(
        execution_status,
        outcome=outcome,
        status="completed",
        phase="completed",
        progress=100.0,
    )
    assert execution_status.status != "completed"
    assert execution_status.unconfirmed_terminal_status == "completed"
    assert execution_status.current_phase == AWAITING_DURABLE_CONFIRMATION_PHASE

    # While the write is outstanding the durable record is honest about it:
    # still running, no fabricated terminal row for a restart to read.
    async with session_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            run_id, organization_id=ORG_ID
        )
        assert row is not None
        assert row.status == RunStatus.RUNNING.value

    # The database comes back. The outcome was held, not discarded, so it is
    # still there to record.
    toggle.broken = False
    assert await service.reconcile_pending_terminal_transitions() == 1
    assert execution_status.status == "completed"
    assert execution_status.unconfirmed_terminal_status is None

    async with session_factory() as session:
        repo = RunLifecycleRepository(session)
        row = await repo.get_run(run_id, organization_id=ORG_ID)
        assert row is not None
        assert row.status == RunStatus.SUCCEEDED.value
        tasks = await repo.get_tasks_for_run(run_id, organization_id=ORG_ID)
        attempts = await repo.get_attempts_for_task(
            tasks[0].task_id, organization_id=ORG_ID
        )
        # final_output is journaled inside the very transaction that failed,
        # so its survival is the proof the true outcome was not lost.
        assert attempts[-1].journaled_result["final_output"] == {
            "result": "the real answer"
        }

    # "The process disappears": a brand-new service sharing only Postgres —
    # what a real restart looks like. The run is durably terminal now, so it
    # is correctly not resurrected as in-flight.
    service_b = DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=AsyncMock(),
        supervisor_factory=Mock(),
        session_factory=session_factory,
    )
    await service_b.restore_active_executions()
    assert "exec-db-down-terminal" not in service_b.active_executions

    await service.close()
    await service_b.close()


@pytest.mark.asyncio
async def test_workflow_does_not_report_success_the_database_refused(
    test_engine: AsyncEngine,
) -> None:
    """The same guarantee through the production path: a database that dies
    between the work finishing and the terminal write must not leave a
    caller polling ``active_executions`` with a 'completed' that nothing
    durable backs."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    toggle = _ToggleFailingSessionFactory(session_factory)
    run_id = "db-down-workflow-terminal-run"
    binding = make_binding(run_id, authority_id="db-down-workflow-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("db-down-workflow-authority", "1"): binding}
    )
    router = AsyncMock()
    router.route.return_value = _RoutingDecisionStub()

    async def _work_then_kill_the_database(*_args: Any, **_kwargs: Any) -> Any:
        toggle.broken = True
        return Mock(output={"result": "the real answer"}, workers_used=1)

    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()
    bridge.execute_execution_plan.side_effect = _work_then_kill_the_database

    service = DirectExecutionService(
        masr_router=router,
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=toggle,
        execution_authority_resolver=resolver,
    )
    service.durable_write_retry_seconds = 0.01
    # Reconcile only when this test says so: a background pass landing the
    # write mid-assertion would make the timing, not the behavior, the thing
    # under test.
    service.durable_reconcile_interval_seconds = 60.0
    project = make_project()
    execution_id = await service.start_research_execution(
        project,
        authority_reference=ExecutionAuthorityReference(
            authority_id="db-down-workflow-authority", authority_version="1"
        ),
    )

    execution = service.active_executions[execution_id]
    for _ in range(100):
        if execution.unconfirmed_terminal_status is not None:
            break
        await asyncio.sleep(0.02)

    assert execution.unconfirmed_terminal_status == "completed"
    assert execution.status != "completed"
    assert execution.current_phase == AWAITING_DURABLE_CONFIRMATION_PHASE
    assert execution.final_output == {"result": "the real answer"}

    toggle.broken = False
    assert await service.reconcile_pending_terminal_transitions() == 1
    assert execution.status == "completed"

    async with session_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            run_id, organization_id=ORG_ID
        )
        assert row is not None
        assert row.status == RunStatus.SUCCEEDED.value

    await service.close()


@pytest.mark.asyncio
async def test_outcome_lost_to_a_dead_process_recovers_as_unfinished_work(
    test_engine: AsyncEngine,
) -> None:
    """The residual case, stated honestly: if the process dies before the
    database comes back, the held outcome dies with it — process memory is
    not durable. What must not happen is a fabricated terminal record. The
    run comes back as unfinished work a restart can see and act on, exactly
    the state the database was left in."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    toggle = _ToggleFailingSessionFactory(session_factory)
    run_id = "db-down-terminal-lost-run"
    binding = make_binding(run_id, authority_id="db-down-terminal-lost-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("db-down-terminal-lost-authority", "1"): binding}
    )
    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()
    service = DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=toggle,
        execution_authority_resolver=resolver,
    )
    service.durable_write_retry_seconds = 0.01
    # Reconcile only when this test says so: a background pass landing the
    # write mid-assertion would make the timing, not the behavior, the thing
    # under test.
    service.durable_reconcile_interval_seconds = 60.0
    project = make_project()
    execution_status = ExecutionStatus(
        execution_id="exec-db-down-terminal-lost",
        project_id=str(project.id),
        status="pending",
        current_phase="initialization",
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
    execution_status.status = "running"
    execution_status.final_output = {"result": "the real answer"}

    toggle.broken = True
    outcome = await service._persist_transition(
        execution_status,
        run_target=RunStatus.SUCCEEDED,
        task_target=TaskStatus.SUCCEEDED,
        attempt_target=AttemptStatus.SUCCEEDED,
        event_type="run.succeeded",
        payload={},
    )
    assert outcome is DurableWriteOutcome.UNAVAILABLE

    # The process disappears with the outcome still parked in its memory.
    toggle.broken = False
    service_b = DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=AsyncMock(),
        supervisor_factory=Mock(),
        session_factory=session_factory,
    )
    await service_b.restore_active_executions()

    recovered = service_b.active_executions.get("exec-db-down-terminal-lost")
    assert recovered is not None
    assert recovered.status == "running"
    assert recovered.unconfirmed_terminal_status is None
    async with session_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            run_id, organization_id=ORG_ID
        )
        assert row is not None
        assert row.status == RunStatus.RUNNING.value
        assert row.completed_at is None

    await service.close()
    await service_b.close()
