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
@pytest.mark.xfail(
    strict=True,
    reason=(
        "FINDING: a database outage that lands exactly at the terminal "
        "(SUCCEEDED) persist call is swallowed by _persist_transition's "
        "broad except-and-log, and _execute_research_workflow flips "
        "execution_status.status to 'completed' in memory unconditionally "
        "afterward regardless of whether the durable write landed. If the "
        "process then disappears (crashes, is redeployed) before the "
        "database recovers, the run's true SUCCEEDED outcome — including "
        "final_output, which is only ever journaled inside that same failed "
        "transaction — is never durably recorded. A restart's "
        "restore_active_executions() finds the run still non-terminal "
        "('running', its last successfully committed status) and resurrects "
        "it as still in-flight forever: no fabricated success, but the run's "
        "real terminal state is permanently and silently lost. This is a "
        "direct violation of the wave's own validation bar: 'an API restart "
        "during an active golden run recovers without fabricated success or "
        "lost terminal state.'"
    ),
)
async def test_db_outage_at_terminal_transition_does_not_lose_true_outcome(
    test_engine: AsyncEngine,
) -> None:
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
    assert execution_status._run is not None
    assert execution_status._run.status == RunStatus.RUNNING

    # The real answer the workflow computed, exactly as
    # _execute_research_workflow would set it before persisting SUCCEEDED.
    execution_status.final_output = {"result": "the real answer"}
    execution_status.agent_results = {"result": "the real answer"}

    # The database goes down at the worst possible instant: right as the
    # terminal transition tries to persist.
    toggle.broken = True
    await service._persist_transition(
        execution_status,
        run_target=RunStatus.SUCCEEDED,
        task_target=TaskStatus.SUCCEEDED,
        attempt_target=AttemptStatus.SUCCEEDED,
        event_type="run.succeeded",
        payload={},
    )
    # Mirrors _execute_research_workflow's unconditional flip after
    # _persist_transition returns, regardless of whether it durably landed.
    execution_status.status = "completed"

    # "The process disappears": database recovers, but this Python process
    # (and its in-memory execution_status) is gone. A brand-new service,
    # sharing only Postgres, is what a real restart looks like.
    toggle.broken = False
    service_b = DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=AsyncMock(),
        supervisor_factory=Mock(),
        session_factory=session_factory,
    )
    await service_b.restore_active_executions()

    recovered = service_b.active_executions.get("exec-db-down-terminal")
    assert recovered is not None
    assert recovered.status == "completed"
    assert recovered.final_output == {"result": "the real answer"}

    await service.close()
    await service_b.close()
