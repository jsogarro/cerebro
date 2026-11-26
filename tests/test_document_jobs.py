"""The shared job guards: status moves, deadlines, discard-on-delete, admission rules."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.models.db.base import Base
from src.models.db.document_critique import (
    DOCUMENT_CRITIQUES,
    CritiqueFinding,
    DocumentCritique,
)
from src.models.db.uploaded_document import UsageLedger
from src.services import document_jobs as jobs
from src.services.document_jobs import (
    MODEL_JOB_KINDS,
    STALE_GRACE_S,
    JobLimits,
    QuotaExceeded,
    admit,
    cancel_all,
    flip_if_stale,
    is_stale,
    run_job,
    stale_cutoff,
    transition,
)

TABLE = DOCUMENT_CRITIQUES
ORG, USER = uuid.uuid4(), uuid.uuid4()
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)

Factory = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture(name="factory")
async def factory_fixture(tmp_path: Any) -> AsyncIterator[Factory]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/jobs.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def insert_job(
    factory: Factory,
    *,
    status: str = "queued",
    created_at: datetime | None = None,
    org: uuid.UUID = ORG,
) -> uuid.UUID:
    job_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            DocumentCritique(
                id=job_id,
                document_id=uuid.uuid4(),
                organization_id=org,
                user_id=USER,
                status=status,
                model_id="m",
                temperature=0.2,
                prompt_versions={},
                consent_text_version="v",
                consent_text_sha256="0" * 64,
                consent_recorded_at=NOW,
                reserved_units=1,
                role_outcomes={},
                created_at=created_at or datetime.now(UTC),
            )
        )
        await session.commit()
    return job_id


async def row_of(factory: Factory, job_id: uuid.UUID) -> DocumentCritique | None:
    async with factory() as session:
        return await session.get(DocumentCritique, job_id)


async def finished(session: AsyncSession, _result: Any) -> tuple[str, dict[str, Any]]:
    return "succeeded", {"role_outcomes": {"methods": {"status": "succeeded"}}}


def start(
    factory: Factory,
    job_id: uuid.UUID,
    work: Callable[[], Awaitable[Any]],
    *,
    persist: Callable[[AsyncSession, Any], Awaitable[tuple[str, dict[str, Any]]]] = (
        finished
    ),
    deadline_s: float = 30,
    created_at: datetime | None = None,
) -> Awaitable[None]:
    return run_job(
        table=TABLE,
        job_id=job_id,
        organization_id=ORG,
        created_at=created_at or datetime.now(UTC),
        deadline_s=deadline_s,
        session_factory=factory,
        semaphore=asyncio.Semaphore(1),
        work=work,
        persist=persist,
    )


class Spy:
    def __init__(self, result: Any = "ok") -> None:
        self.calls = 0
        self.result = result

    async def __call__(self) -> Any:
        self.calls += 1
        return self.result


# ---- 1. compare-and-set ------------------------------------------------------


async def test_transition_changes_nothing_when_expected_does_not_match(
    factory: Factory,
) -> None:
    job_id = await insert_job(factory, status="failed")
    async with factory() as session:
        moved = await transition(
            session, TABLE, job_id, expected="queued", new="running"
        )
        await session.commit()
    assert moved is False
    assert (await row_of(factory, job_id)).status == "failed"  # type: ignore[union-attr]


async def test_transition_moves_and_sets_fields_when_expected_matches(
    factory: Factory,
) -> None:
    job_id = await insert_job(factory)
    async with factory() as session:
        moved = await transition(
            session, TABLE, job_id, expected="queued", new="running", started_at=NOW
        )
        await session.commit()
    row = await row_of(factory, job_id)
    assert moved is True and row.status == "running"  # type: ignore[union-attr]
    assert row.started_at is not None  # type: ignore[union-attr]


async def test_run_job_on_a_row_already_failed_never_calls_work(
    factory: Factory,
) -> None:
    job_id = await insert_job(factory, status="failed")
    work = Spy()
    await start(factory, job_id, work)
    assert work.calls == 0
    assert (await row_of(factory, job_id)).status == "failed"  # type: ignore[union-attr]


async def test_run_job_success_persists_and_finishes_the_row(factory: Factory) -> None:
    job_id = await insert_job(factory)
    await start(factory, job_id, Spy())
    row = await row_of(factory, job_id)
    assert row.status == "succeeded" and row.completed_at is not None  # type: ignore[union-attr]
    assert row.started_at is not None  # type: ignore[union-attr]
    assert row.role_outcomes == {"methods": {"status": "succeeded"}}  # type: ignore[union-attr]


# ---- 2. deadline --------------------------------------------------------------


async def test_work_that_never_finishes_fails_with_deadline_exceeded(
    factory: Factory,
) -> None:
    job_id = await insert_job(factory)
    never = asyncio.Event()

    async def hang() -> None:
        await never.wait()

    await asyncio.wait_for(start(factory, job_id, hang, deadline_s=0.05), 5)
    row = await row_of(factory, job_id)
    assert (row.status, row.status_reason) == ("failed", "deadline_exceeded")  # type: ignore[union-attr]


async def test_a_row_older_than_its_deadline_fails_without_calling_work(
    factory: Factory,
) -> None:
    old = datetime.now(UTC) - timedelta(seconds=100)
    job_id = await insert_job(factory, created_at=old)
    work = Spy()
    await start(factory, job_id, work, deadline_s=30, created_at=old)
    row = await row_of(factory, job_id)
    assert work.calls == 0
    assert (row.status, row.status_reason) == ("failed", "deadline_exceeded")  # type: ignore[union-attr]


async def test_a_timeout_raised_by_work_itself_is_an_internal_error(
    factory: Factory,
) -> None:
    job_id = await insert_job(factory)

    async def provider_timeout() -> None:
        raise TimeoutError

    await start(factory, job_id, provider_timeout)
    assert (await row_of(factory, job_id)).status_reason == "internal_error"  # type: ignore[union-attr]


# ---- 3. stale rule ------------------------------------------------------------


@pytest.mark.parametrize("status", ["queued", "running"])
def test_a_row_older_than_deadline_plus_grace_is_stale(status: str) -> None:
    created = NOW - timedelta(seconds=30 + STALE_GRACE_S + 1)
    assert is_stale(status, created, 30, NOW)
    assert not is_stale(status, created + timedelta(seconds=2), 30, NOW)


@pytest.mark.parametrize("status", ["succeeded", "partial", "failed"])
def test_a_finished_row_is_never_stale(status: str) -> None:
    assert not is_stale(status, NOW - timedelta(days=9), 30, NOW)


def test_stale_cutoff_is_deadline_plus_grace_before_now() -> None:
    assert stale_cutoff(NOW, 900) == NOW - timedelta(seconds=900 + STALE_GRACE_S)


def test_a_naive_created_at_is_read_as_utc() -> None:
    naive = (NOW - timedelta(days=1)).replace(tzinfo=None)
    assert is_stale("running", naive, 30, NOW)


async def test_flip_if_stale_fails_a_stale_row_once(factory: Factory) -> None:
    old = datetime.now(UTC) - timedelta(seconds=30 + STALE_GRACE_S + 5)
    job_id = await insert_job(factory, status="running", created_at=old)
    async with factory() as session:
        row = await session.get(DocumentCritique, job_id)
        assert row is not None
        flipped = await flip_if_stale(session, TABLE, row, 30)
        await session.commit()
    assert flipped is True
    after = await row_of(factory, job_id)
    assert (after.status, after.status_reason) == ("failed", "deadline_exceeded")  # type: ignore[union-attr]
    first_completed = after.completed_at  # type: ignore[union-attr]
    async with factory() as session:
        row = await session.get(DocumentCritique, job_id)
        assert row is not None
        assert await flip_if_stale(session, TABLE, row, 30) is False
        await session.commit()
    assert (await row_of(factory, job_id)).completed_at == first_completed  # type: ignore[union-attr]


async def test_flip_if_stale_leaves_a_fresh_row_alone(factory: Factory) -> None:
    job_id = await insert_job(factory, status="running")
    async with factory() as session:
        row = await session.get(DocumentCritique, job_id)
        assert row is not None
        assert await flip_if_stale(session, TABLE, row, 30) is False
    assert (await row_of(factory, job_id)).status == "running"  # type: ignore[union-attr]


# ---- 4. discard on delete -----------------------------------------------------


async def test_a_deleted_row_discards_the_result_without_persisting(
    factory: Factory,
) -> None:
    job_id = await insert_job(factory)
    release = asyncio.Event()
    persisted = Spy()

    async def work() -> str:
        await release.wait()
        return "late"

    async def persist(session: AsyncSession, result: Any) -> tuple[str, dict[str, Any]]:
        await persisted()
        return "succeeded", {}

    task = asyncio.create_task(start(factory, job_id, work, persist=persist))
    for _ in range(100):
        row = await row_of(factory, job_id)
        if row is not None and row.status == "running":
            break
        await asyncio.sleep(0.01)
    async with factory() as session:
        await session.delete(await session.get(DocumentCritique, job_id))
        await session.commit()
    release.set()
    await asyncio.wait_for(task, 5)
    assert persisted.calls == 0
    async with factory() as session:
        count = await session.scalar(select(func.count()).select_from(CritiqueFinding))
    assert count == 0


async def test_an_integrity_error_inside_persist_discards_without_raising(
    factory: Factory,
) -> None:
    job_id = await insert_job(factory)

    async def persist(session: AsyncSession, result: Any) -> tuple[str, dict[str, Any]]:
        raise IntegrityError("insert", {}, Exception("fk"))

    await start(factory, job_id, Spy(), persist=persist)
    assert (await row_of(factory, job_id)).status == "running"  # type: ignore[union-attr]


# ---- 5b. terminal reasons -----------------------------------------------------


async def test_an_unexpected_exception_is_an_internal_error_and_never_logged_by_text(
    factory: Factory,
) -> None:
    job_id = await insert_job(factory)

    async def boom() -> None:
        raise ValueError("SENTINEL")

    with structlog.testing.capture_logs() as captured:
        await start(factory, job_id, boom)
    row = await row_of(factory, job_id)
    assert (row.status, row.status_reason) == ("failed", "internal_error")  # type: ignore[union-attr]
    blob = repr(captured)
    assert "ValueError" in blob and "SENTINEL" not in blob
    assert "exc_info" not in blob


async def test_an_exception_inside_persist_leaves_no_partial_rows_and_no_text_in_logs(
    factory: Factory,
) -> None:
    job_id = await insert_job(factory)

    async def persist(session: AsyncSession, result: Any) -> tuple[str, dict[str, Any]]:
        session.add(_finding(job_id))
        await session.flush()
        raise RuntimeError("SENTINEL")

    with structlog.testing.capture_logs() as captured:
        await start(factory, job_id, Spy(), persist=persist)
    assert (await row_of(factory, job_id)).status_reason == "internal_error"  # type: ignore[union-attr]
    async with factory() as session:
        count = await session.scalar(select(func.count()).select_from(CritiqueFinding))
    assert count == 0
    assert "SENTINEL" not in repr(captured)


async def test_a_job_cancelled_while_waiting_for_a_slot_is_failed_as_interrupted(
    factory: Factory,
) -> None:
    job_id = await insert_job(factory)
    held = asyncio.Semaphore(0)
    work = Spy()
    task = asyncio.create_task(
        run_job(
            table=TABLE,
            job_id=job_id,
            organization_id=ORG,
            created_at=datetime.now(UTC),
            deadline_s=30,
            session_factory=factory,
            semaphore=held,
            work=work,
            persist=finished,
        )
    )
    await asyncio.sleep(0.05)
    await asyncio.wait_for(cancel_all({task}), 5)
    row = await row_of(factory, job_id)
    assert (row.status, row.status_reason) == ("failed", "interrupted")  # type: ignore[union-attr]
    assert work.calls == 0


async def test_an_error_while_starting_fails_the_queued_row(
    factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = await insert_job(factory)

    async def broken(job: Any) -> bool:
        raise OSError("connection reset")

    monkeypatch.setattr(jobs, "_start", broken)
    await start(factory, job_id, Spy())
    row = await row_of(factory, job_id)
    assert (row.status, row.status_reason) == ("failed", "internal_error")  # type: ignore[union-attr]


async def test_cancel_all_interrupts_every_running_job_and_waits_for_the_writes(
    factory: Factory,
) -> None:
    ids = [await insert_job(factory) for _ in range(2)]
    entered = [asyncio.Event() for _ in ids]

    def blocker(event: asyncio.Event) -> Callable[[], Awaitable[None]]:
        async def block() -> None:
            event.set()
            await asyncio.Event().wait()

        return block

    tasks = {
        asyncio.create_task(start(factory, job_id, blocker(event)))
        for job_id, event in zip(ids, entered, strict=True)
    }
    for event in entered:
        await asyncio.wait_for(event.wait(), 5)
    await asyncio.wait_for(cancel_all(tasks), 5)
    for job_id in ids:
        row = await row_of(factory, job_id)
        assert (row.status, row.status_reason) == ("failed", "interrupted")  # type: ignore[union-attr]


def _finding(critique_id: uuid.UUID) -> CritiqueFinding:
    return CritiqueFinding(
        critique_id=critique_id,
        organization_id=ORG,
        user_id=USER,
        role="methods",
        category="design",
        severity="major",
        quote_enc="fernet:x",
        raw_start=0,
        raw_end=1,
        page=1,
        match_kind="exact",
        problem_enc="fernet:x",
        fix_enc="fernet:x",
    )


async def test_cancellation_fails_the_row_as_interrupted_and_reraises(
    factory: Factory,
) -> None:
    job_id = await insert_job(factory)
    entered = asyncio.Event()

    async def block() -> None:
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(start(factory, job_id, block))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = await row_of(factory, job_id)
    assert (row.status, row.status_reason) == ("failed", "interrupted")  # type: ignore[union-attr]


# ---- admission rules (single connection; the races are in the integration suite) --


async def seed_ledger(
    factory: Factory,
    kind: str,
    *,
    n: int = 1,
    units: int = 1,
    org: uuid.UUID = ORG,
    user: uuid.UUID = USER,
    age: timedelta = timedelta(0),
) -> None:
    async with factory() as session:
        for _ in range(n):
            session.add(
                UsageLedger(
                    organization_id=org,
                    user_id=user,
                    kind=kind,
                    units=units,
                    created_at=datetime.now(UTC) - age,
                )
            )
        await session.commit()


async def ledger_count(factory: Factory) -> int:
    async with factory() as session:
        return int(
            await session.scalar(select(func.count()).select_from(UsageLedger)) or 0
        )


async def no_active(session: AsyncSession, org: uuid.UUID, now: datetime) -> int:
    return 0


async def try_admit(
    factory: Factory,
    limits: JobLimits,
    *,
    units: int = 10,
    kind: str = "critique",
    count_active: Callable[..., Awaitable[int]] = no_active,
    **kw: Any,
) -> str | None:
    async with factory() as session:
        try:
            await admit(
                session,
                kind=kind,
                organization_id=ORG,
                user_id=USER,
                units=units,
                limits=limits,
                count_active=count_active,
                **kw,
            )
        except QuotaExceeded as exc:
            await session.commit()
            return exc.code
        await session.commit()
        return None


async def test_admit_inserts_one_ledger_row_with_the_units(factory: Factory) -> None:
    assert await try_admit(factory, JobLimits(per_user_daily=5)) is None
    async with factory() as session:
        row = (await session.execute(select(UsageLedger))).scalar_one()
    assert (row.kind, row.units, row.organization_id, row.user_id) == (
        "critique",
        10,
        ORG,
        USER,
    )


async def test_per_user_daily_counts_only_this_users_rows_of_this_kind_in_24h(
    factory: Factory,
) -> None:
    other = uuid.uuid4()
    await seed_ledger(factory, "critique", n=2, user=other)
    await seed_ledger(factory, "revision", n=2)
    await seed_ledger(factory, "critique", n=2, age=timedelta(hours=25))
    await seed_ledger(factory, "critique", n=1)
    limits = JobLimits(per_user_daily=2)
    assert await try_admit(factory, limits) is None
    assert await try_admit(factory, limits) == "user_daily"


async def test_a_refused_admission_leaves_the_ledger_unchanged(
    factory: Factory,
) -> None:
    await seed_ledger(factory, "critique", n=3)
    before = await ledger_count(factory)
    assert await try_admit(factory, JobLimits(per_user_daily=3)) == "user_daily"
    assert await ledger_count(factory) == before


async def test_org_daily_units_include_every_budget_kind(factory: Factory) -> None:
    await seed_ledger(factory, "revision", units=95)
    limits = JobLimits(per_org_daily_units=100)
    assert (
        await try_admit(factory, limits, units=10, budget_kinds=("critique",)) is None
    )
    assert (
        await try_admit(factory, limits, units=10, budget_kinds=MODEL_JOB_KINDS)
        == "org_daily_budget"
    )


async def test_revision_rows_never_count_against_a_critiques_per_user_limit(
    factory: Factory,
) -> None:
    await seed_ledger(factory, "revision", n=9)
    limits = JobLimits(per_user_daily=1, per_org_daily_units=10**9)
    assert await try_admit(factory, limits, budget_kinds=MODEL_JOB_KINDS) is None


async def test_global_daily_units_span_orgs(factory: Factory) -> None:
    await seed_ledger(
        factory, "critique", units=95, org=uuid.uuid4(), user=uuid.uuid4()
    )
    assert (
        await try_admit(factory, JobLimits(global_daily_units=100), units=10)
        == "global_daily_budget"
    )


async def test_global_daily_count_spans_orgs(factory: Factory) -> None:
    await seed_ledger(factory, "critique", org=uuid.uuid4(), user=uuid.uuid4())
    assert (
        await try_admit(factory, JobLimits(global_daily_count=1))
        == "global_daily_count"
    )


async def test_org_concurrent_uses_the_callers_counter_with_now(
    factory: Factory,
) -> None:
    seen: list[tuple[uuid.UUID, datetime]] = []

    async def two_active(session: AsyncSession, org: uuid.UUID, now: datetime) -> int:
        seen.append((org, now))
        return 2

    assert (
        await try_admit(
            factory, JobLimits(per_org_concurrent=2), count_active=two_active
        )
        == "org_concurrent"
    )
    assert seen and seen[0][0] == ORG and seen[0][1].tzinfo is not None


async def test_a_limit_left_none_is_never_counted(factory: Factory) -> None:
    await seed_ledger(factory, "critique", n=50, units=10**6)
    calls = Spy(0)

    async def count_active(session: AsyncSession, org: uuid.UUID, now: datetime) -> int:
        return int(await calls())

    assert await try_admit(factory, JobLimits(), count_active=count_active) is None
    assert calls.calls == 0
