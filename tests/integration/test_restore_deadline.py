"""Restore fails only runs that are past their own deadline."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.api.services.direct_execution_service import (
    DirectExecutionService,
    ExecutionStatus,
)
from src.core.contracts import AttemptStatus, RunStatus, TaskStatus
from src.repositories.run_lifecycle_repository import RunLifecycleRepository
from tests.integration.crash_fixtures import ORG_ID, make_binding, make_project

pytestmark = [pytest.mark.integration]


def _service(session_factory) -> DirectExecutionService:
    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()
    return DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=session_factory,
    )


async def _running_run(service, run_id: str, deadline: datetime) -> None:
    binding = make_binding(
        run_id, authority_id=f"{run_id}-authority", deadline=deadline
    )
    status = ExecutionStatus(
        execution_id=f"exec-{run_id}",
        project_id=str(make_project().id),
        status="running",
    )
    await service._admit_run(status, binding, make_project())
    await service._persist_transition(
        status,
        run_target=RunStatus.RUNNING,
        task_target=TaskStatus.RUNNING,
        attempt_target=AttemptStatus.RUNNING,
        event_type="run.started",
        payload={},
    )


@pytest.mark.asyncio
async def test_restore_fails_a_past_deadline_run_and_spares_a_live_one(
    test_engine: AsyncEngine,
) -> None:
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    writer = _service(session_factory)
    now = datetime.now(UTC)
    await _running_run(writer, "deadline-stale", now - timedelta(hours=1))
    await _running_run(writer, "deadline-live", now + timedelta(hours=1))
    # Rows from before ownership existed carry no lease; only those fall back
    # to the deadline rule (a leased run is live until its lease expires).
    async with session_factory() as session:
        await session.execute(
            text(
                "UPDATE agent_runs SET owner_instance_id = NULL, lease_expires_at = NULL"
            )
        )
        await session.commit()

    restarted = _service(session_factory)
    await restarted.restore_active_executions()

    stale = restarted.active_executions["exec-deadline-stale"]
    live = restarted.active_executions["exec-deadline-live"]
    assert stale.status == "failed"
    assert live.status == "running"
    assert live.restored is True
    async with session_factory() as session:
        repo = RunLifecycleRepository(session)
        stale_row = await repo.get_run("deadline-stale", organization_id=ORG_ID)
        live_row = await repo.get_run("deadline-live", organization_id=ORG_ID)
    assert stale_row.status == RunStatus.FAILED.value
    assert stale_row.status_reason == "interrupted by restart"
    assert live_row.status == RunStatus.RUNNING.value

    await writer.close()
    await restarted.close()
