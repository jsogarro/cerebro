"""A run has one live owner; a lost owner cannot write and its run is reconciled.

Real Postgres throughout: the conditional UPDATEs, row locks and CHECKs are the
behaviour under test. "Another process" is a second service/session over the
same database, and the kill test is a real SIGKILL.
"""

import asyncio
import copy
import os
import signal
import subprocess
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.api.services.direct_execution_service import (
    DirectExecutionService,
    DurableWriteOutcome,
    ExecutionStatus,
)
from src.core.contracts import AttemptStatus, RunStatus, TaskStatus
from src.repositories.model_call_reservation_repository import (
    ModelCallReservationRepository,
    OwnerLostError,
)
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.run_lifecycle_repository import (
    ResearchRunJournalOwner,
    RunLifecycleRepository,
    StaleFenceError,
)
from src.services.model_invocation import (
    CallPrice,
    ModelInvocationContext,
    invoke_model,
)
from tests.integration.crash_fixtures import ORG_ID, make_binding, make_project

pytestmark = [pytest.mark.integration]

CHILD = Path(__file__).with_name("run_ownership_crash_child.py")
REPO_ROOT = Path(__file__).resolve().parents[2]
PRICE = CallPrice(Decimal("0.001"), "d" * 64)


@pytest.fixture(name="factory")
def factory_fixture(test_engine: AsyncEngine) -> Callable[[], AsyncSession]:
    return sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


def _service(factory: Callable[[], AsyncSession]) -> DirectExecutionService:
    bridge = AsyncMock()
    bridge.admit_execution_plan = Mock()
    return DirectExecutionService(
        masr_router=AsyncMock(),
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=factory,
    )


async def _running(service: DirectExecutionService, run_id: str) -> ExecutionStatus:
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
    service.active_executions[status.execution_id] = status
    return status


async def _sql(factory: Callable[[], AsyncSession], statement: str, **params: Any):
    async with factory() as session:
        result = await session.execute(text(statement), params)
        await session.commit()
        return result


async def _expire(factory: Callable[[], AsyncSession], run_id: str) -> None:
    await _sql(
        factory,
        "UPDATE agent_runs SET lease_expires_at = clock_timestamp() - interval '1 second' "
        "WHERE run_id = :r",
        r=run_id,
    )


async def _row(factory: Callable[[], AsyncSession], run_id: str):
    async with factory() as session:
        return await RunLifecycleRepository(session).get_run(
            run_id, organization_id=ORG_ID
        )


async def _event_types(factory: Callable[[], AsyncSession], run_id: str) -> list[str]:
    async with factory() as session:
        events = await RunEventRepository(session).read_events_after(
            run_id, after_sequence=0, limit=1000, organization_id=ORG_ID
        )
    return [e.event_type for e in events]


def _journal(
    factory: Callable[[], AsyncSession], run_id: str
) -> ModelCallReservationRepository:
    return ModelCallReservationRepository(
        factory,
        organization_id=ORG_ID,
        owner=ResearchRunJournalOwner(run_id, max_tokens=1000, max_cost_usd=Decimal(1)),
    )


async def _open_call(
    factory: Callable[[], AsyncSession],
    status: ExecutionStatus,
    call_id: str,
    *,
    sent: bool,
) -> None:
    journal = _journal(factory, status.run_id or "")
    token = status._fencing_token
    await journal.reserve(
        call_id,
        tokens=100,
        cost_usd=Decimal("0.01"),
        price_digest="d" * 64,
        fencing_token=token,
    )
    if sent:
        await journal.mark_may_have_sent(call_id, fencing_token=token)


# --- lease contention -------------------------------------------------------


async def test_two_owners_contend_for_one_lease(factory) -> None:
    service = _service(factory)
    status = await _running(service, "own-contend")
    await _sql(
        factory,
        "UPDATE agent_runs SET owner_instance_id = NULL, lease_expires_at = NULL",
    )

    async def acquire(owner: str) -> int | None:
        async with factory() as session:
            token = await RunLifecycleRepository(session).acquire_lease(
                "own-contend", owner=owner, ttl_seconds=30, organization_id=ORG_ID
            )
            await session.commit()
            return token

    results = await asyncio.gather(acquire("a"), acquire("b"))
    assert sorted(r is None for r in results) == [False, True]

    winner = "a" if results[0] is not None else "b"
    await _expire(factory, "own-contend")
    loser = "b" if winner == "a" else "a"
    token = await acquire(loser)
    assert token is not None and token > max(r for r in results if r is not None)
    async with factory() as session:
        renewed = await RunLifecycleRepository(session).renew_lease(
            "own-contend",
            owner=winner,
            fencing_token=max(r for r in results if r),
            ttl_seconds=30,
        )
    assert renewed is False
    assert status.run_id == "own-contend"


# --- fencing on every lifecycle write --------------------------------------


async def test_every_lifecycle_write_is_refused_under_a_stale_token(factory) -> None:
    service = _service(factory)
    status = await _running(service, "own-stale")
    stale = status._fencing_token
    assert stale is not None
    await _expire(factory, "own-stale")
    async with factory() as session:
        fresh = await RunLifecycleRepository(session).acquire_lease(
            "own-stale", owner="other", ttl_seconds=30, organization_id=ORG_ID
        )
        await session.commit()
    assert fresh == stale + 1

    run, task, attempt = status._run, status._task, status._attempt
    assert run and task and attempt
    now = run.updated_at
    writes = {
        "run": lambda r: r.record_run_transition(
            run.transition_to(RunStatus.FAILED, at=now, reason="x"),
            organization_id=ORG_ID,
            fencing_token=stale,
        ),
        "task": lambda r: r.record_task_transition(
            task.transition_to(TaskStatus.FAILED, at=now, reason="x"),
            organization_id=ORG_ID,
            fencing_token=stale,
        ),
        "attempt": lambda r: r.record_attempt_transition(
            attempt.transition_to(AttemptStatus.FAILED, at=now, reason="x"),
            organization_id=ORG_ID,
            fencing_token=stale,
        ),
        "journaled_result": lambda r: r.write_journaled_result(
            attempt.attempt_id,
            organization_id=ORG_ID,
            result={"final_output": {}},
            fencing_token=stale,
        ),
    }
    for name, write in writes.items():
        async with factory() as session:
            with pytest.raises(StaleFenceError):
                await write(RunLifecycleRepository(session))
        assert name

    async with factory() as session:
        with pytest.raises(StaleFenceError):
            await service._append_event(
                session,
                run=run,
                event_type="run.attempt_failed",
                payload={},
                occurred_at=now,
                fencing_token=stale,
            )
    # Control: the current token is accepted by the same write.
    async with factory() as session:
        await RunLifecycleRepository(session).write_journaled_result(
            attempt.attempt_id,
            organization_id=ORG_ID,
            result={"final_output": {}},
            fencing_token=fresh,
        )
        await session.commit()
    assert (await _row(factory, "own-stale")).status == RunStatus.RUNNING.value


async def test_a_worker_that_lost_its_lease_cannot_record_success(factory) -> None:
    worker = _service(factory)
    status = await _running(worker, "own-lost-success")
    status.final_output = {"ok": True}
    await _expire(factory, "own-lost-success")

    assert await _service(factory).reconcile_expired_runs() == 1
    outcome = await worker._persist_transition(
        status,
        run_target=RunStatus.SUCCEEDED,
        task_target=TaskStatus.SUCCEEDED,
        attempt_target=AttemptStatus.SUCCEEDED,
        event_type="run.succeeded",
        payload={},
    )

    assert outcome is DurableWriteOutcome.REJECTED
    assert (await _row(factory, "own-lost-success")).status == RunStatus.FAILED.value
    assert "run.succeeded" not in await _event_types(factory, "own-lost-success")
    assert status._run is not None and status._run.status is RunStatus.FAILED


# --- expiry -> reconciliation ----------------------------------------------


async def test_an_expired_owner_is_reconciled_once_and_its_calls_settle(
    factory,
) -> None:
    owner = _service(factory)
    status = await _running(owner, "own-expired")
    await _open_call(factory, status, "unsent", sent=False)
    await _open_call(factory, status, "sent", sent=True)
    await _expire(factory, "own-expired")

    replacement = _service(factory)
    assert await replacement.reconcile_expired_runs() == 1
    assert await replacement.reconcile_expired_runs() == 0

    row = await _row(factory, "own-expired")
    assert row.status == RunStatus.FAILED.value
    assert row.status_reason == "PROCESS_INTERRUPTED"
    async with factory() as session:
        repo = RunLifecycleRepository(session)
        tasks = await repo.get_tasks_for_run("own-expired", organization_id=ORG_ID)
        attempts = await repo.get_attempts_for_task(
            tasks[0].task_id, organization_id=ORG_ID
        )
    assert tasks[0].status == TaskStatus.FAILED.value
    assert attempts[0].status == AttemptStatus.FAILED.value
    journal = _journal(factory, "own-expired")
    assert (await journal.entry("unsent")).settlement_state == "released_unsent"
    assert (await journal.entry("sent")).settlement_state == "settled_unknown"
    types = await _event_types(factory, "own-expired")
    assert types.count("run.interrupted") == 1
    assert (await journal.totals()).uncertain_tokens == 100


async def test_reconciliation_leaves_live_and_unowned_runs_alone(factory) -> None:
    service = _service(factory)
    await _running(service, "own-live")
    await _running(service, "own-legacy")
    await _sql(
        factory,
        "UPDATE agent_runs SET owner_instance_id = NULL, lease_expires_at = NULL "
        "WHERE run_id = 'own-legacy'",
    )

    assert await _service(factory).reconcile_expired_runs() == 0
    assert (await _row(factory, "own-live")).status == RunStatus.RUNNING.value
    assert (await _row(factory, "own-legacy")).status == RunStatus.RUNNING.value


async def test_a_lease_renewed_before_takeover_wins_over_reconciliation(
    factory,
) -> None:
    owner = _service(factory)
    status = await _running(owner, "own-renewed")
    await _expire(factory, "own-renewed")
    listed = _service(factory)
    async with factory() as session:
        repo = RunLifecycleRepository(session)
        rebuilt = await listed._rehydrate_execution(
            repo, (await repo.list_expired_runs())[0]
        )
    # The owner comes back after the scan but before the takeover. An expired
    # lease cannot be renewed, so it re-acquires (token + 1) and keeps the run.
    async with factory() as session:
        await RunLifecycleRepository(session).acquire_lease(
            "own-renewed", owner=owner.owner_instance_id, ttl_seconds=30
        )
        await session.commit()

    assert rebuilt is not None
    assert await listed._reconcile_interrupted(rebuilt) == 0
    assert (await _row(factory, "own-renewed")).status == RunStatus.RUNNING.value
    assert status.run_id == "own-renewed"


# --- model calls under the lease -------------------------------------------


async def test_a_model_call_is_refused_once_the_lease_is_lost(factory) -> None:
    service = _service(factory)
    status = await _running(service, "own-call")
    sent: list[int] = []

    async def provider(_remaining: float | None) -> Any:
        sent.append(1)
        return SimpleNamespace(
            success=True,
            content="x",
            finish_reason="stop",
            total_tokens=10,
            prompt_tokens=5,
            completion_tokens=5,
            cost_estimate=0.001,
        )

    def ctx() -> ModelInvocationContext:
        return ModelInvocationContext(
            journal=_journal(factory, "own-call"),
            guard=service._ownership_guard(status),
            fencing_token=status._fencing_token,
            attempt_ref=status._attempt.attempt_id,
        )

    await invoke_model(ctx(), provider, estimated_tokens=10, price=PRICE)
    assert sent == [1]

    status._lease_lost = True
    with pytest.raises(OwnerLostError):
        await invoke_model(ctx(), provider, estimated_tokens=10, price=PRICE)
    status._lease_lost = False

    await _expire(factory, "own-call")
    with pytest.raises(OwnerLostError):
        await invoke_model(ctx(), provider, estimated_tokens=10, price=PRICE)
    assert sent == [1]


# --- cancel versus success -------------------------------------------------


async def _succeed(service: DirectExecutionService, status: ExecutionStatus):
    status.final_output = {"ok": True}
    return await service._persist_transition(
        status,
        run_target=RunStatus.SUCCEEDED,
        task_target=TaskStatus.SUCCEEDED,
        attempt_target=AttemptStatus.SUCCEEDED,
        event_type="run.succeeded",
        payload={},
    )


async def test_a_cancellation_that_commits_first_beats_the_success(factory) -> None:
    service = _service(factory)
    status = await _running(service, "own-race-cancel")
    stale_view = copy.copy(status)
    assert await service._persist_cancellation(status) is DurableWriteOutcome.RECORDED

    outcome = await _succeed(service, stale_view)

    assert outcome is DurableWriteOutcome.REJECTED
    assert (await _row(factory, "own-race-cancel")).status == RunStatus.CANCELLED.value
    types = await _event_types(factory, "own-race-cancel")
    assert types.count("run.cancelled") == 1 and "run.succeeded" not in types


async def test_a_success_that_commits_first_beats_the_cancellation(factory) -> None:
    service = _service(factory)
    status = await _running(service, "own-race-success")
    stale_view = copy.copy(status)
    assert await _succeed(service, status) is DurableWriteOutcome.RECORDED

    assert (
        await service._persist_cancellation(stale_view) is DurableWriteOutcome.REJECTED
    )

    assert (await _row(factory, "own-race-success")).status == RunStatus.SUCCEEDED.value
    assert "run.cancellation_requested" not in await _event_types(
        factory, "own-race-success"
    )


async def test_racing_cancel_and_success_end_in_exactly_one_terminal_state(
    factory,
) -> None:
    service = _service(factory)
    for n in range(6):
        run_id = f"own-race-{n}"
        status = await _running(service, run_id)
        success_view, cancel_view = copy.copy(status), copy.copy(status)
        await asyncio.gather(
            _succeed(service, success_view), service._persist_cancellation(cancel_view)
        )
        row = await _row(factory, run_id)
        if row.status == RunStatus.CANCELLING.value:
            # The success was refused after the request committed: its owner
            # finishes the cancellation (already done inside the refusal).
            pytest.fail("a refused success left the run cancelling")
        types = await _event_types(factory, run_id)
        terminal = [t for t in types if t in ("run.succeeded", "run.cancelled")]
        assert len(terminal) == 1, (row.status, types)
        assert row.status in (RunStatus.SUCCEEDED.value, RunStatus.CANCELLED.value)


async def test_cancelling_settles_the_runs_open_model_calls(factory) -> None:
    service = _service(factory)
    status = await _running(service, "own-cancel-settle")
    await _open_call(factory, status, "in-flight", sent=True)
    await service._persist_cancellation(status)
    status.status = "cancelled"

    await service._record_requested_cancellation(status)

    assert (
        await _row(factory, "own-cancel-settle")
    ).status == RunStatus.CANCELLED.value
    journal = _journal(factory, "own-cancel-settle")
    assert (await journal.entry("in-flight")).settlement_state == "settled_unknown"


# --- heartbeat --------------------------------------------------------------


async def _lease_expiry(factory, run_id: str):
    result = await _sql(
        factory, "SELECT lease_expires_at FROM agent_runs WHERE run_id = :r", r=run_id
    )
    return result.scalar_one()


async def _lease_live(factory, run_id: str) -> bool:
    result = await _sql(
        factory,
        "SELECT lease_expires_at > clock_timestamp() FROM agent_runs WHERE run_id = :r",
        r=run_id,
    )
    return bool(result.scalar_one())


async def test_the_heartbeat_keeps_a_lease_alive_and_stops_when_it_is_lost(
    factory,
) -> None:
    service = _service(factory)
    service.lease_renew_seconds = 0.0
    service.cancel_poll_seconds = 0.02
    status = await _running(service, "own-beat")
    work = asyncio.create_task(asyncio.sleep(3600))
    service._workflow_tasks[status.execution_id] = work
    beat = service._start_lease_heartbeat(status, work)

    seen = [await _lease_expiry(factory, "own-beat")]
    for _ in range(250):
        latest = await _lease_expiry(factory, "own-beat")
        if latest > seen[-1]:
            seen.append(latest)
        if len(seen) >= 3:
            break
        await asyncio.sleep(0.02)
    assert len(seen) >= 3, "the lease was not renewed twice"

    # Another owner takes the run once the lease is expired. The heartbeat may
    # renew in between, so retry until the acquisition lands.
    for _ in range(100):
        await _expire(factory, "own-beat")
        async with factory() as session:
            token = await RunLifecycleRepository(session).acquire_lease(
                "own-beat", owner="other", ttl_seconds=30
            )
            await session.commit()
        if token is not None:
            break
    assert token is not None
    await asyncio.wait_for(beat, 5)
    await asyncio.sleep(0)

    assert status._lease_lost and work.cancelled()


async def test_a_cancellation_recorded_by_another_process_stops_the_owner(
    factory,
) -> None:
    owner = _service(factory)
    owner.cancel_poll_seconds = 0.05
    status = await _running(owner, "own-remote-cancel")
    work = asyncio.create_task(asyncio.sleep(60))
    owner._workflow_tasks[status.execution_id] = work
    beat = asyncio.create_task(owner._hold_lease(status))

    other = _service(factory)
    await other.restore_active_executions()
    placeholder = other.active_executions[status.execution_id]
    assert (
        await other._persist_cancellation(placeholder) is DurableWriteOutcome.RECORDED
    )

    await asyncio.wait([work], timeout=5)
    beat.cancel()
    assert work.cancelled() and status.status == "cancelled"
    assert status._run is not None and status._run.status is RunStatus.CANCELLING
    await owner._record_requested_cancellation(status)
    assert (
        await _row(factory, "own-remote-cancel")
    ).status == RunStatus.CANCELLED.value


# --- a real kill ------------------------------------------------------------


async def test_a_killed_owner_is_reconciled_to_one_unknown_charge(
    factory, postgres_container, test_engine
) -> None:
    url = postgres_container.get_connection_url().replace("+psycopg2", "")
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env["PYTHONPATH"] = str(REPO_ROOT)
    child = subprocess.Popen(
        [sys.executable, str(CHILD), url, "own-killed"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        # Startup logs share stdout; the barrier line is the only one that matters.
        for line in iter(child.stdout.readline, ""):
            if line.strip() == "READY":
                break
        else:
            pytest.fail("child exited before reaching the barrier")
        child.send_signal(signal.SIGKILL)
    finally:
        child.kill()
        child.wait(timeout=30)

    # Alive as far as the database can tell, until the lease runs out.
    replacement = _service(factory)
    assert await replacement.reconcile_expired_runs() == 0
    await _expire(factory, "own-killed")
    assert await replacement.reconcile_expired_runs() == 1
    assert await replacement.reconcile_expired_runs() == 0

    assert (await _row(factory, "own-killed")).status == RunStatus.FAILED.value
    totals = await _journal(factory, "own-killed").totals()
    assert (totals.uncertain_tokens, totals.open_tokens) == (400, 0)
    interrupted = [
        e for e in await _event_types(factory, "own-killed") if e == "run.interrupted"
    ]
    assert len(interrupted) == 1


# --- observable counters ----------------------------------------------------


async def test_reconciling_an_expired_owner_is_counted(factory) -> None:
    from src.core.observability import run_ownership_expired_total

    before = run_ownership_expired_total._value.get()
    await _running(_service(factory), "own-counted")
    await _expire(factory, "own-counted")

    assert await _service(factory).reconcile_expired_runs() == 1

    assert run_ownership_expired_total._value.get() == before + 1


async def test_the_outbox_backlog_is_exported_by_status(factory) -> None:
    from src.api.services.outbox_relay import OutboxRelay
    from src.core.observability import outbox_rows

    await _running(_service(factory), "own-outbox")
    relay = OutboxRelay(session_factory=factory, event_publisher=Mock())
    async with factory() as session:
        await relay._export_backlog(session)

    assert outbox_rows.labels(status="pending")._value.get() >= 1
