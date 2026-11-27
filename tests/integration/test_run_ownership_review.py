"""Ownership edge cases found in review: late writers, racing reconcilers, outages.

Real Postgres; the same helpers as ``test_run_execution_ownership``.
"""

import asyncio
import uuid
from collections.abc import Callable

import pytest
from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.api.services.direct_execution_service import (
    DirectExecutionService,
    ExecutionStatus,
)
from src.core.contracts import RunStatus
from src.core.observability import outbox_rows, run_reconciliation_failures_total
from src.models.db.research_project import ProjectStatus, ResearchProject
from src.models.db.run_lifecycle import AgentRun
from src.repositories.run_lifecycle_repository import RunLifecycleRepository
from tests.integration.crash_fixtures import ORG_ID
from tests.integration.test_run_execution_ownership import (  # noqa: F401 - fixtures
    _event_types,
    _expire,
    _row,
    _running,
    _service,
    _sql,
    factory_fixture,
)

pytestmark = [pytest.mark.integration]


def _failures() -> float:
    return run_reconciliation_failures_total._value.get()


class _Flaky:
    """A session factory whose database can be switched off."""

    def __init__(self, factory: Callable[[], AsyncSession]) -> None:
        self._factory, self.down = factory, False

    def __call__(self) -> AsyncSession:
        if self.down:
            raise OSError("database down")
        return self._factory()


async def _listed(factory, service: DirectExecutionService) -> ExecutionStatus:
    async with factory() as session:
        repo = RunLifecycleRepository(session)
        rebuilt = await service._rehydrate_execution(
            repo, (await repo.list_expired_runs())[0]
        )
    assert rebuilt is not None
    return rebuilt


# --- 1: a late owner cannot publish output through a checkpoint -------------


async def test_a_worker_that_lost_its_lease_cannot_checkpoint_its_output(
    factory,
) -> None:
    service = _service(factory)
    status = await _running(service, "rev-ckpt")
    async with factory() as session:
        project = ResearchProject(
            title="p",
            query='{"text": "q", "domains": ["ai"]}',
            domains=["ai"],
            status=ProjectStatus.IN_PROGRESS,
            user_id=uuid.uuid4(),
            organization_id=uuid.UUID(ORG_ID),
            project_metadata={},
        )
        session.add(project)
        await session.flush()
        status.project_id = str(project.id)
        await session.commit()
    status.final_output = {"secret": "stale output"}

    async def checkpoints(phase: str) -> int:
        result = await _sql(
            factory,
            "SELECT count(*) FROM workflow_checkpoints WHERE phase = :p AND run_id = :r",
            p=phase,
            r="rev-ckpt",
        )
        return result.scalar_one()

    await service._checkpoint(status, "masr_routing")
    assert await checkpoints("masr_routing") == 1

    await _expire(factory, "rev-ckpt")
    assert await _service(factory).reconcile_expired_runs() == 1
    await service._checkpoint(status, "completed")

    assert await checkpoints("completed") == 0


# --- 2: losing a takeover race is not a failure ------------------------------


async def test_losing_a_takeover_race_is_a_no_op_not_a_failure(factory) -> None:
    await _running(_service(factory), "rev-race-seq")
    await _expire(factory, "rev-race-seq")
    first, second = _service(factory), _service(factory)
    first_view, second_view = (
        await _listed(factory, first),
        await _listed(factory, second),
    )
    failures = _failures()

    assert await first._reconcile_interrupted(first_view) == 1
    assert await second._reconcile_interrupted(second_view) == 0

    assert _failures() == failures
    assert not second._pending_terminal_outcomes
    types = await _event_types(factory, "rev-race-seq")
    assert types.count("run.interrupted") == 1


async def test_two_reconcilers_racing_record_one_interruption(factory) -> None:
    await _running(_service(factory), "rev-race-par")
    await _expire(factory, "rev-race-par")
    a, b = _service(factory), _service(factory)
    failures = _failures()

    results = await asyncio.gather(
        a.reconcile_expired_runs(), b.reconcile_expired_runs()
    )

    assert sorted(results) == [0, 1]
    assert _failures() == failures
    assert not a._pending_terminal_outcomes and not b._pending_terminal_outcomes
    assert (await _event_types(factory, "rev-race-par")).count("run.interrupted") == 1


# --- 3: background loops survive a bug --------------------------------------


async def test_the_reconciler_loop_restarts_after_an_unexpected_error(
    factory, monkeypatch
) -> None:
    service = _service(factory)
    service.expired_run_scan_seconds = 0.02
    service.loop_restart_seconds = 0.02
    calls: list[int] = []

    async def flaky() -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("bug")
        return 0

    monkeypatch.setattr(service, "reconcile_expired_runs", flaky)
    service.start_expired_run_reconciliation()
    for _ in range(100):
        if len(calls) >= 3:
            break
        await asyncio.sleep(0.05)
    await service.close()

    assert len(calls) >= 3


async def test_work_stops_when_its_heartbeat_crashes(factory, monkeypatch) -> None:
    service = _service(factory)
    service.cancel_poll_seconds = 0.02
    status = await _running(service, "rev-beat-crash")
    work = asyncio.create_task(asyncio.sleep(60))
    service._workflow_tasks[status.execution_id] = work

    async def boom(self, run_id: str) -> bool:
        raise RuntimeError("bug")

    monkeypatch.setattr(RunLifecycleRepository, "cancellation_requested", boom)
    service._start_lease_heartbeat(status, work)
    await asyncio.wait([work], timeout=5)

    assert work.cancelled() and status._lease_lost


# --- 5: a parked success outlives a lease that expired -------------------------


async def _park_success(factory, run_id: str, **tuning: float):
    """A running run whose success the database refused: parked, db healthy again."""
    from src.api.services.direct_execution_service import DurableWriteOutcome
    from src.core.contracts import AttemptStatus, TaskStatus

    flaky = _Flaky(factory)
    service = _service(flaky)
    service.lease_ttl_seconds = 30.0
    service.durable_write_retry_seconds = 0.0
    service.durable_reconcile_interval_seconds = 3600.0
    for name, value in tuning.items():
        setattr(service, name, value)
    status = await _running(service, run_id)
    status.final_output = {"ok": True}
    flaky.down = True
    outcome = await service._persist_transition(
        status,
        run_target=RunStatus.SUCCEEDED,
        task_target=TaskStatus.SUCCEEDED,
        attempt_target=AttemptStatus.SUCCEEDED,
        event_type="run.succeeded",
        payload={},
    )
    flaky.down = False
    assert outcome is DurableWriteOutcome.UNAVAILABLE
    return service, status


async def _lease(factory, run_id: str):
    return (
        await _sql(
            factory,
            "SELECT lease_expires_at FROM agent_runs WHERE run_id = :r",
            r=run_id,
        )
    ).scalar_one()


async def test_a_parked_success_lands_even_if_its_lease_expired_and_cannot_be_renewed(
    factory, monkeypatch
) -> None:
    service, _ = await _park_success(factory, "rev-parked")
    await _expire(factory, "rev-parked")

    async def no_renewal(_status) -> None:
        return None

    monkeypatch.setattr(service, "_renew_lease_now", no_renewal)
    assert await service.reconcile_pending_terminal_transitions() == 1
    await service.close()

    assert (await _row(factory, "rev-parked")).status == RunStatus.SUCCEEDED.value


async def test_a_parked_outcome_renews_its_lease_before_each_retry(factory) -> None:
    service, _ = await _park_success(factory, "rev-parked-renew")
    await _expire(factory, "rev-parked-renew")

    assert await service.reconcile_pending_terminal_transitions() == 1
    await service.close()

    result = await _sql(
        factory,
        "SELECT lease_expires_at > clock_timestamp() FROM agent_runs WHERE run_id = :r",
        r="rev-parked-renew",
    )
    assert result.scalar_one() is True


async def test_the_lease_is_renewed_while_a_parked_outcome_outlives_its_workflow(
    factory,
) -> None:
    service, status = await _park_success(
        factory, "rev-parked-beat", cancel_poll_seconds=0.02, lease_renew_seconds=0.0
    )
    finish = asyncio.Event()
    work = asyncio.create_task(finish.wait())
    service._workflow_tasks[status.execution_id] = work
    beat = service._start_lease_heartbeat(status, work)
    finish.set()
    await work

    seen = [await _lease(factory, "rev-parked-beat")]
    for _ in range(250):
        latest = await _lease(factory, "rev-parked-beat")
        if latest > seen[-1]:
            seen.append(latest)
        if len(seen) >= 3:
            break
        await asyncio.sleep(0.02)
    assert len(seen) >= 3 and not beat.done()

    service._pending_terminal_outcomes.clear()
    await asyncio.wait_for(beat, 5)
    await service.close()


async def test_a_parked_outcome_is_dropped_when_its_own_window_ends(factory) -> None:
    service, _ = await _park_success(
        factory, "rev-parked-window", durable_reconcile_max_seconds=0.0
    )

    assert await service.reconcile_pending_terminal_transitions() == 0

    assert not service._pending_terminal_outcomes
    assert (await _row(factory, "rev-parked-window")).status == RunStatus.RUNNING.value
    await service.close()


async def test_a_scan_does_not_fail_this_processs_own_parked_success(factory) -> None:
    service, _ = await _park_success(factory, "rev-parked-scan")
    await _expire(factory, "rev-parked-scan")

    assert await service.reconcile_expired_runs() == 0
    assert (await _row(factory, "rev-parked-scan")).status == RunStatus.RUNNING.value

    assert await service.reconcile_pending_terminal_transitions() == 1
    await service.close()
    assert (await _row(factory, "rev-parked-scan")).status == RunStatus.SUCCEEDED.value


# --- 6: SQLite has no leases and no failure -----------------------------------


async def test_sqlite_has_no_expired_runs_and_no_reconciliation_failure() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(AgentRun.__table__.create)

    def make() -> AsyncSession:
        return AsyncSession(engine, expire_on_commit=False)

    failures = _failures()
    async with make() as session:
        assert await RunLifecycleRepository(session).list_expired_runs() == []
    assert await _service(make).reconcile_expired_runs() == 0
    assert _failures() == failures
    await engine.dispose()


# --- 8: counters ------------------------------------------------------------


async def test_a_reconciliation_that_cannot_commit_is_counted_and_retried(
    factory,
) -> None:
    flaky = _Flaky(factory)
    service = _service(flaky)
    service.durable_write_retry_seconds = 0.01
    await _running(service, "rev-unavail")
    await _expire(factory, "rev-unavail")
    reconciler = _service(flaky)
    reconciler.durable_write_retry_seconds = 0.01
    rebuilt = await _listed(factory, reconciler)
    failures = _failures()

    flaky.down = True
    assert await reconciler._reconcile_interrupted(rebuilt) == 0
    assert _failures() == failures + 1

    assert await reconciler.reconcile_expired_runs() == 0  # scan with the db down
    assert _failures() == failures + 2
    flaky.down = False
    await reconciler.close()


async def test_the_backlog_gauge_matches_the_outbox_table(factory) -> None:
    from unittest.mock import Mock

    from src.api.services.outbox_relay import OutboxRelay

    await _running(_service(factory), "rev-backlog")
    relay = OutboxRelay(session_factory=factory, event_publisher=Mock())
    async with factory() as session:
        await relay._export_backlog(session)
    for status in ("pending", "in_flight", "delivered", "failed"):
        expected = (
            await _sql(
                factory,
                "SELECT count(*) FROM agent_run_event_outbox WHERE status = :s",
                s=status,
            )
        ).scalar_one()
        assert outbox_rows.labels(status=status)._value.get() == expected


# --- shutdown releases leases ------------------------------------------------


async def test_a_graceful_shutdown_releases_its_leases_for_reconciliation(
    factory,
) -> None:
    service = _service(factory)
    await _running(service, "rev-close")
    assert await _service(factory).reconcile_expired_runs() == 0

    await service.close()

    assert await _service(factory).reconcile_expired_runs() == 1
    assert (await _row(factory, "rev-close")).status == RunStatus.FAILED.value


async def test_a_recorded_cancellation_is_timed(factory) -> None:
    def observations() -> float:
        return REGISTRY.get_sample_value("cerebro_run_cancellation_delay_seconds_count")

    service = _service(factory)
    status = await _running(service, "rev-timed")
    await service._persist_cancellation(status)
    status.status = "cancelled"
    before = observations()

    await service._record_requested_cancellation(status)

    assert observations() == before + 1
