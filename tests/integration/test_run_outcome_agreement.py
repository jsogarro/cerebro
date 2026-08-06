"""The REST surface and the durable event log must tell the same story.

``_execute_research_workflow`` is wrapped in ``@retry``, so any transient
supervisor error is followed by another attempt. These tests pin down what a
caller and the database are each allowed to believe while that is happening —
in particular that a run which ultimately succeeds is never durably recorded
as failed, and that a status the contract refused is never published anyway.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker
from tenacity import wait_none

from src.api.services.direct_execution_service import (
    DirectExecutionService,
    DurableWriteOutcome,
    ExecutionStatus,
)
from src.api.services.execution_authority_resolver import (
    MappingExecutionAuthorityResolver,
)
from src.core.contracts import AttemptStatus, RunStatus, TaskStatus
from src.models.execution_authority import ExecutionAuthorityReference
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository
from tests.integration.crash_fixtures import (
    ORG_ID,
    _RoutingDecisionStub,
    make_binding,
    make_project,
)

pytestmark = [pytest.mark.integration]


@pytest.fixture
def instant_workflow_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip the exponential backoff from the workflow's retry decorator.

    The production wait is 4–10s per attempt; the behavior under test is what
    the retry *does*, not how long it waits for.
    """
    monkeypatch.setattr(
        DirectExecutionService._execute_research_workflow.retry, "wait", wait_none()
    )


@pytest.mark.asyncio
async def test_a_retried_workflow_never_reports_an_outcome_the_database_denies(
    test_engine: AsyncEngine, instant_workflow_retries: None
) -> None:
    """A supervisor that fails once and then succeeds must leave the caller
    and the durable record agreeing on 'succeeded'. The transient failure
    must not be recorded as the run's terminal outcome, because the run had
    not finished — it was about to be retried."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "retried-workflow-run"
    binding = make_binding(run_id, authority_id="retried-workflow-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("retried-workflow-authority", "1"): binding}
    )
    router = AsyncMock()
    router.route.return_value = _RoutingDecisionStub()

    calls = 0

    async def _fail_once_then_succeed(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient supervisor error")
        return Mock(
            output={"result": "the answer on the second attempt"}, workers_used=1
        )

    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()
    bridge.execute_execution_plan.side_effect = _fail_once_then_succeed

    service = DirectExecutionService(
        masr_router=router,
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=session_factory,
        execution_authority_resolver=resolver,
    )
    project = make_project()
    execution_id = await service.start_research_execution(
        project,
        authority_reference=ExecutionAuthorityReference(
            authority_id="retried-workflow-authority", authority_version="1"
        ),
        organization_id=ORG_ID,
    )

    execution = service.active_executions[execution_id]
    for _ in range(200):
        if execution.status in ("completed", "failed"):
            break
        await asyncio.sleep(0.02)

    assert calls == 2
    assert execution.status == "completed"
    assert execution.final_output == {"result": "the answer on the second attempt"}

    async with session_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            run_id, organization_id=ORG_ID
        )
        assert row is not None
        # The single assertion this whole test exists for: what the caller
        # was told and what the database holds are the same outcome.
        assert row.status == RunStatus.SUCCEEDED.value

        events = await RunEventRepository(session).read_events_after(
            run_id, after_sequence=0, limit=100, organization_id=ORG_ID
        )
        event_types = [event.event_type for event in events]
        # A run that succeeded must not carry a terminal failure in its
        # audit history — the attempt failed, the run did not.
        assert "run.failed" not in event_types
        assert "run.succeeded" in event_types

    await service.close()


@pytest.mark.asyncio
async def test_a_refused_transition_never_publishes_the_status_it_was_refused(
    test_engine: AsyncEngine,
) -> None:
    """A transition the contract refuses means the durable record already
    moved somewhere this write cannot reach. Whatever the caller wanted to
    publish, the status a reader gets must be the one the database holds."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "refused-transition-run"
    binding = make_binding(run_id, authority_id="refused-transition-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("refused-transition-authority", "1"): binding}
    )
    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()
    service = DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=session_factory,
        execution_authority_resolver=resolver,
    )
    project = make_project()
    execution_status = ExecutionStatus(
        execution_id="exec-refused-transition",
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
    # The run really did fail, durably and terminally.
    assert (
        await service._persist_transition(
            execution_status,
            run_target=RunStatus.FAILED,
            task_target=TaskStatus.FAILED,
            attempt_target=AttemptStatus.FAILED,
            event_type="run.failed",
            payload={},
            reason="the run failed",
        )
        is DurableWriteOutcome.RECORDED
    )
    execution_status.status = "failed"

    # Something now tries to publish success on a terminal, immutable run.
    outcome = await service._persist_transition(
        execution_status,
        run_target=RunStatus.SUCCEEDED,
        task_target=TaskStatus.SUCCEEDED,
        attempt_target=AttemptStatus.SUCCEEDED,
        event_type="run.succeeded",
        payload={},
    )
    assert outcome is DurableWriteOutcome.REJECTED

    service._settle_terminal_state(
        execution_status,
        outcome=outcome,
        status="completed",
        phase="completed",
        progress=100.0,
    )

    assert execution_status.status == "failed"
    assert execution_status.progress_percentage != 100.0

    async with session_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            run_id, organization_id=ORG_ID
        )
        assert row is not None
        assert row.status == RunStatus.FAILED.value

    await service.close()


@pytest.mark.asyncio
async def test_a_workflow_that_exhausts_its_retries_is_durably_failed(
    test_engine: AsyncEngine, instant_workflow_retries: None
) -> None:
    """The other side of the same rule: once no attempt remains, the failure
    is real and both the caller and the database must say so."""

    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "exhausted-retries-run"
    binding = make_binding(run_id, authority_id="exhausted-retries-authority")
    resolver = MappingExecutionAuthorityResolver(
        {("exhausted-retries-authority", "1"): binding}
    )
    router = AsyncMock()
    router.route.return_value = _RoutingDecisionStub()

    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()
    bridge.execute_execution_plan.side_effect = RuntimeError("permanent failure")

    service = DirectExecutionService(
        masr_router=router,
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=session_factory,
        execution_authority_resolver=resolver,
    )
    project = make_project()
    execution_id = await service.start_research_execution(
        project,
        authority_reference=ExecutionAuthorityReference(
            authority_id="exhausted-retries-authority", authority_version="1"
        ),
        organization_id=ORG_ID,
    )

    execution = service.active_executions[execution_id]
    for _ in range(200):
        if execution.status in ("completed", "failed"):
            break
        await asyncio.sleep(0.02)

    assert execution.status == "failed"

    async with session_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            run_id, organization_id=ORG_ID
        )
        assert row is not None
        assert row.status == RunStatus.FAILED.value

    await service.close()
