"""Crash matrix: does a fault mid-write leave a half-written durable record?

``DirectExecutionService._admit_run`` and ``_persist_transition`` each do all
of their repository writes (run/task/attempt row(s) plus the paired event +
outbox rows) inside one ``async with self.session_factory() as session:``
block, committing once at the end. That is the whole of "persist before
publish" being atomic rather than merely ordered: if anything inside the
block raises, the block's ``session.commit()`` is never reached and the
session closes without committing, so SQLAlchemy rolls back everything
flushed so far. These tests prove that rollback actually happens against a
real Postgres testcontainer — not just that the code is shaped to expect it —
and separately prove that a *replayed* admission (the same run_id submitted
twice, as a client retry would produce) neither writes a second durable
record nor repeats the work.
"""

from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.api.services.direct_execution_service import (
    DirectExecutionService,
    ExecutionStatus,
    RunAdmissionError,
)
from src.api.services.execution_authority_resolver import (
    MappingExecutionAuthorityResolver,
)
from src.core.contracts import AttemptStatus, RunStatus, TaskStatus
from src.models.db.run_event import AgentRunEvent
from src.models.db.run_lifecycle import AgentRun, AgentRunTask, AgentTaskAttempt
from src.repositories.run_lifecycle_repository import RunLifecycleRepository
from tests.integration.crash_fixtures import ORG_ID, make_binding, make_project

pytestmark = [pytest.mark.integration]


def _service(session_factory: Any, resolver: Any) -> DirectExecutionService:
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
async def test_admission_failure_mid_write_leaves_zero_rows(
    test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fault after the run/task/attempt rows are added but before commit
    (simulated as the event-append raising) must not leave any of those rows
    durably visible — admission is all-or-nothing.

    The caller learns about it too: this test previously asserted that
    ``_admit_run`` swallowed the failure, which is how an unadmitted run went
    on to report a terminal outcome no durable record held. Atomicity is
    still the subject; failing closed is now part of it.
    """

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "atomicity-admit-fail"
    binding = make_binding(run_id, authority_id="atomicity-admit-fail-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("atomicity-admit-fail-authority", "1"): binding}
    )
    service = _service(session_factory, resolver)
    project = make_project()

    async def _raise(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("simulated crash before commit")

    monkeypatch.setattr(
        "src.repositories.run_event_repository.RunEventRepository.append_event",
        _raise,
    )

    execution_status = ExecutionStatus(
        execution_id="exec-atomicity-admit-fail",
        project_id=str(project.id),
        status="pending",
        current_phase="initialization",
    )
    # Persistence is configured and the write did not land, so admission
    # fails closed rather than handing back a run nothing durable holds.
    with pytest.raises(RunAdmissionError):
        await service._admit_run(execution_status, binding, project)

    assert execution_status.run_id is None

    async with session_factory() as session:
        assert (
            await session.execute(select(AgentRun).where(AgentRun.run_id == run_id))
        ).scalar_one_or_none() is None
        assert (
            await session.execute(
                select(AgentRunTask).where(AgentRunTask.run_id == run_id)
            )
        ).scalar_one_or_none() is None
        assert (
            await session.execute(
                select(AgentTaskAttempt).where(AgentTaskAttempt.run_id == run_id)
            )
        ).scalar_one_or_none() is None
        assert (
            await session.execute(
                select(AgentRunEvent).where(AgentRunEvent.run_id == run_id)
            )
        ).scalar_one_or_none() is None

    await service.close()


@pytest.mark.asyncio
async def test_transition_failure_mid_write_does_not_advance_durable_status(
    test_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fault while persisting the RUNNING transition (after the admission
    committed successfully) must leave the durable run at its last
    successfully committed status (``queued``), not a half-applied
    ``running`` with no matching event."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "atomicity-transition-fail"
    binding = make_binding(run_id, authority_id="atomicity-transition-fail-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("atomicity-transition-fail-authority", "1"): binding}
    )
    service = _service(session_factory, resolver)
    project = make_project()

    execution_status = ExecutionStatus(
        execution_id="exec-atomicity-transition-fail",
        project_id=str(project.id),
        status="pending",
        current_phase="initialization",
    )
    await service._admit_run(execution_status, binding, project)
    assert execution_status.run_id == run_id
    pre_fault_run = execution_status._run
    assert pre_fault_run is not None
    assert pre_fault_run.status == RunStatus.QUEUED

    async def _raise(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("simulated crash after run-row flush, before commit")

    monkeypatch.setattr(
        "src.repositories.run_event_repository.RunEventRepository.append_event",
        _raise,
    )

    await service._persist_transition(
        execution_status,
        run_target=RunStatus.RUNNING,
        task_target=TaskStatus.RUNNING,
        attempt_target=AttemptStatus.RUNNING,
        event_type="run.started",
        payload={"phase": "masr_routing"},
    )

    # The in-memory contract cache must not have advanced past the last
    # durably-committed state either — otherwise a subsequent transition
    # would validate against a status the database never actually reached.
    assert execution_status._run is pre_fault_run
    assert execution_status._run.status == RunStatus.QUEUED

    async with session_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            run_id, organization_id=ORG_ID
        )
        assert row is not None
        assert row.status == RunStatus.QUEUED.value
        events = (
            (
                await session.execute(
                    select(AgentRunEvent).where(AgentRunEvent.run_id == run_id)
                )
            )
            .scalars()
            .all()
        )
        # Only the admission event ("run.admitted") exists — the failed
        # "run.started" append left no trace, matching the run row.
        assert [e.event_type for e in events] == ["run.admitted"]

    await service.close()


@pytest.mark.asyncio
async def test_replayed_admission_does_not_double_execute_supervisor(
    test_engine: AsyncEngine,
) -> None:
    """The same authority submitted twice — an ordinary client network retry
    — must run the workflow once. The replay is coalesced onto the execution
    already handling that run and gets its handle back, so the caller can
    read one status and one result rather than racing two."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "atomicity-replay"
    binding = make_binding(run_id, authority_id="atomicity-replay-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("atomicity-replay-authority", "1"): binding}
    )

    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()
    bridge.execute_execution_plan.return_value = Mock(
        output={"ok": True}, workers_used=1
    )

    from src.api.services.direct_execution_service import DirectExecutionService
    from src.models.execution_authority import ExecutionAuthorityReference

    router = AsyncMock()
    from tests.integration.crash_fixtures import _RoutingDecisionStub

    router.route.return_value = _RoutingDecisionStub()

    service = DirectExecutionService(
        masr_router=router,
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=session_factory,
        execution_authority_resolver=resolver,
    )
    project = make_project()
    reference = ExecutionAuthorityReference(
        authority_id="atomicity-replay-authority", authority_version="1"
    )

    execution_id_1 = await service.start_research_execution(
        project, authority_reference=reference, organization_id=ORG_ID
    )
    execution_id_2 = await service.start_research_execution(
        project, authority_reference=reference, organization_id=ORG_ID
    )
    # One run, one execution: the replay is handed the existing handle rather
    # than a second one that would have to be reconciled against it later.
    assert execution_id_2 == execution_id_1
    assert len(service.active_executions) == 1

    for _ in range(50):
        statuses = [
            service.active_executions[eid].status
            for eid in (execution_id_1, execution_id_2)
        ]
        if all(status in ("completed", "failed") for status in statuses):
            break
        import asyncio

        await asyncio.sleep(0.02)

    # A replayed admission for the same run_id is coalesced before the
    # supervisor bridge is invoked a second time — the non-idempotent effect
    # is what must not repeat.
    assert bridge.execute_execution_plan.await_count == 1
    assert service.active_executions[execution_id_1].status == "completed"

    async with session_factory() as session:
        rows = (
            (await session.execute(select(AgentRun).where(AgentRun.run_id == run_id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1

    await service.close()


@pytest.mark.asyncio
async def test_replayed_admission_after_a_restart_resumes_rather_than_restarts(
    test_engine: AsyncEngine,
) -> None:
    """A replay that arrives after the process handling the run restarted is
    still a replay: the new process recovered that run from the durable
    record, so it must hand back the recovered execution instead of starting
    the work over."""

    import asyncio

    from src.models.execution_authority import ExecutionAuthorityReference
    from tests.integration.crash_fixtures import _RoutingDecisionStub

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "atomicity-replay-after-restart"
    binding = make_binding(run_id, authority_id="atomicity-replay-restart-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("atomicity-replay-restart-authority", "1"): binding}
    )
    reference = ExecutionAuthorityReference(
        authority_id="atomicity-replay-restart-authority", authority_version="1"
    )

    router = AsyncMock()
    router.route.return_value = _RoutingDecisionStub()
    supervisor_started = asyncio.Event()
    release_supervisor = asyncio.Event()

    async def _blocking_execute_plan(*_args: Any, **_kwargs: Any) -> Any:
        supervisor_started.set()
        await release_supervisor.wait()
        return Mock(output={"ok": True}, workers_used=1)

    bridge_a = AsyncMock()
    bridge_a.admit_execution_plan = Mock()
    bridge_a.execute_execution_plan.side_effect = _blocking_execute_plan
    service_a = DirectExecutionService(
        masr_router=router,
        supervisor_bridge=bridge_a,
        supervisor_factory=Mock(),
        session_factory=session_factory,
        execution_authority_resolver=resolver,
    )
    project = make_project()
    execution_id = await service_a.start_research_execution(
        project, authority_reference=reference, organization_id=ORG_ID
    )
    await asyncio.wait_for(supervisor_started.wait(), timeout=5)

    # The restart: a new process, sharing only Postgres, recovers the run.
    bridge_b = AsyncMock()
    bridge_b.admit_execution_plan = Mock()
    service_b = DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=bridge_b,
        supervisor_factory=Mock(),
        session_factory=session_factory,
        execution_authority_resolver=resolver,
    )
    assert await service_b.restore_active_executions() >= 1
    assert execution_id in service_b.active_executions

    replayed = await service_b.start_research_execution(
        project, authority_reference=reference, organization_id=ORG_ID
    )

    assert replayed == execution_id
    bridge_b.admit_execution_plan.assert_not_called()
    assert bridge_b.execute_execution_plan.await_count == 0

    release_supervisor.set()
    await service_a.close()
    await service_b.close()
