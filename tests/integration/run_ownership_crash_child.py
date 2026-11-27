"""Child process: admit a run, start it, open a model call, then wait to be killed.

Usage: run_ownership_crash_child.py URL RUN_ID

Prints ``READY`` once the run is RUNNING under this process's lease and one
model call is marked as possibly sent, so the parent can SIGKILL it with the
durable record in exactly that state.
"""

import asyncio
import sys
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.services.direct_execution_service import (
    DirectExecutionService,
    ExecutionStatus,
)
from src.core.contracts import AttemptStatus, RunStatus, TaskStatus
from src.repositories.model_call_reservation_repository import (
    ModelCallReservationRepository,
)
from src.repositories.run_lifecycle_repository import ResearchRunJournalOwner
from tests.integration.crash_fixtures import ORG_ID, make_binding, make_project


async def main(url: str, run_id: str) -> None:
    engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()
    service = DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=factory,
    )
    project = make_project()
    status = ExecutionStatus(
        execution_id=f"exec-{run_id}", project_id=str(project.id), status="running"
    )
    await service._admit_run(status, make_binding(run_id), project)
    await service._persist_transition(
        status,
        run_target=RunStatus.RUNNING,
        task_target=TaskStatus.RUNNING,
        attempt_target=AttemptStatus.RUNNING,
        event_type="run.started",
        payload={},
    )
    journal = ModelCallReservationRepository(
        factory,
        organization_id=ORG_ID,
        owner=ResearchRunJournalOwner(run_id, max_tokens=1000, max_cost_usd=Decimal(1)),
    )
    await journal.reserve(
        "call-1",
        tokens=400,
        cost_usd=Decimal("0.1"),
        price_digest="d" * 64,
        fencing_token=status._fencing_token,
    )
    await journal.mark_may_have_sent("call-1", fencing_token=status._fencing_token)
    print("READY", flush=True)
    await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:3]))
