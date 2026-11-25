"""The pilot reads a finished run's cost and wall time from Postgres."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from src.core.contracts import Run, RunStatus
from src.evals.cost_pilot import RunSample
from src.evals.pilot_runner import database_run_reader
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository

pytestmark = [pytest.mark.integration]

ORG_ID = "00000000-0000-0000-0000-0000000000f2"
T0 = datetime(2026, 11, 25, tzinfo=UTC)


@pytest.mark.asyncio
async def test_reader_returns_the_terminal_usage_and_wall_time(
    test_engine: AsyncEngine,
) -> None:
    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    run = Run(
        run_id="pilot-reader-run",
        tenant_id=ORG_ID,
        workflow_definition_id="evaluation-trial",
        workflow_definition_version="1",
        routing_policy_id="evaluation-trial",
        routing_policy_version="1",
        idempotency_key="pilot-reader-key",
        requested_by="evaluation-harness",
        status=RunStatus.CREATED,
        created_at=T0,
        updated_at=T0,
    )
    usage = {
        "tokens_used": 1200,
        "cost_usd": "0.0312",
        "tool_invocations": 0,
        "unknown_usage_calls": 0,
    }
    async with session_factory() as session:
        await RunLifecycleRepository(session).create_run(run, organization_id=ORG_ID)
        events = RunEventRepository(session)
        for event_id, kind, seconds, payload in (
            ("e1", "run.admitted", 0, {}),
            ("e2", "run.succeeded", 75, {"workers_used": 2, "usage": usage}),
        ):
            await events.append_event(
                run_id=run.run_id,
                organization_id=ORG_ID,
                event_id=event_id,
                aggregate_type="run",
                aggregate_id=run.run_id,
                event_type=kind,
                event_type_version="1",
                occurred_at=T0 + timedelta(seconds=seconds),
                producer="test",
                deduplication_key=event_id,
                payload=payload,
            )
        await session.commit()

    read = database_run_reader(session_factory, organization_id=ORG_ID)

    assert await read(run.run_id, "research") == RunSample(
        domain="research",
        outcome="succeeded",
        ledger_cost_usd=Decimal("0.0312"),
        wall_seconds=75.0,
        claims=0,
    )
