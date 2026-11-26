"""Guards shared by every long-running document job.

Admission, status moves, the deadline and the stale rule live here once, so each
mutation test exists once. Quotas are read from the Postgres ``usage_ledger``
because production runs several workers and a per-process counter would multiply
every limit by the worker count. A job's status only ever moves by
compare-and-set, so a row already failed by a read cannot be resurrected by the
task that was still running it.
"""

import asyncio
import functools
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, TypeVar

from sqlalchemy import Table, func, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.middleware.tenant_context import set_postgres_tenant_context
from src.models.db.job_constants import (
    ACTIVE_STATUSES,
    JOB_STATUSES,
    RUN_JOB_REASONS,
)
from src.models.db.uploaded_document import UsageLedger

__all__ = [
    "JOB_STATUSES",
    "MODEL_ADMIT_LOCK",
    "MODEL_JOB_KINDS",
    "RUN_JOB_REASONS",
    "STALE_GRACE_S",
    "JobLimits",
    "Outcome",
    "QuotaExceeded",
    "SessionFactory",
    "admit",
    "cancel_all",
    "flip_if_stale",
    "is_stale",
    "run_job",
    "stale_cutoff",
    "transition",
]

logger = get_logger()

STALE_GRACE_S = 120
MODEL_ADMIT_LOCK = "model"
MODEL_JOB_KINDS = ("critique", "revision")
_WINDOW = timedelta(hours=24)

_T = TypeVar("_T")
SessionFactory = Callable[[], Any]
Outcome = tuple[str, Mapping[str, Any]]


@dataclass(frozen=True)
class JobLimits:
    """``None`` means no limit of that kind; each caller sets only what it enforces."""

    per_user_daily: int | None = None
    per_org_daily_units: int | None = None
    global_daily_units: int | None = None
    global_daily_count: int | None = None
    per_org_concurrent: int | None = None


class QuotaExceeded(Exception):  # noqa: N818 - name fixed by the packet contract
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _JobRow(Protocol):
    id: uuid.UUID
    status: str
    created_at: datetime


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def stale_cutoff(now: datetime, deadline_s: float) -> datetime:
    return now - timedelta(seconds=deadline_s + STALE_GRACE_S)


def is_stale(
    status: str, created_at: datetime, deadline_s: float, now: datetime
) -> bool:
    return status in ACTIVE_STATUSES and _aware(created_at) < stale_cutoff(
        now, deadline_s
    )


async def transition(
    session: AsyncSession,
    table: Table,
    job_id: uuid.UUID,
    *,
    expected: str,
    new: str,
    **fields: Any,
) -> bool:
    """Move a job ``expected`` to ``new``; False when it was not in ``expected``."""
    result = await session.execute(
        update(table)
        .where(table.c.id == job_id, table.c.status == expected)
        .values(status=new, **fields)
    )
    return bool(result.rowcount == 1)  # type: ignore[attr-defined]


async def flip_if_stale(
    session: AsyncSession, table: Table, row: _JobRow, deadline_s: float
) -> bool:
    """Fail a queued or running row past its deadline plus grace; True if this call did."""
    now = datetime.now(UTC)
    if not is_stale(row.status, row.created_at, deadline_s, now):
        return False
    return await transition(
        session,
        table,
        row.id,
        expected=row.status,
        new="failed",
        status_reason="deadline_exceeded",
        completed_at=now,
    )


async def _used(session: AsyncSession, *conditions: Any) -> tuple[int, int]:
    """Rows and units in the ledger matching ``conditions``."""
    row = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(UsageLedger.units), 0)).where(
                *conditions
            )
        )
    ).one()
    return int(row[0]), int(row[1])


@dataclass(frozen=True)
class _Request:
    kind: str
    organization_id: uuid.UUID
    user_id: uuid.UUID
    units: int
    budget_kinds: tuple[str, ...]


async def _ledger_breach(
    session: AsyncSession, limits: JobLimits, request: _Request, now: datetime
) -> str | None:
    """The code of the first ledger limit the request would exceed, if any."""
    since = UsageLedger.created_at >= now - _WINDOW
    kinds = UsageLedger.kind.in_(request.budget_kinds)
    mine = UsageLedger.kind == request.kind
    org = UsageLedger.organization_id == request.organization_id
    if limits.per_user_daily is not None:
        count, _ = await _used(
            session, since, mine, UsageLedger.user_id == request.user_id
        )
        if count >= limits.per_user_daily:
            return "user_daily"
    if limits.per_org_daily_units is not None:
        _, used = await _used(session, since, kinds, org)
        if used + request.units > limits.per_org_daily_units:
            return "org_daily_budget"
    if limits.global_daily_units is not None:
        _, used = await _used(session, since, kinds)
        if used + request.units > limits.global_daily_units:
            return "global_daily_budget"
    if limits.global_daily_count is not None:
        count, _ = await _used(session, since, mine)
        if count >= limits.global_daily_count:
            return "global_daily_count"
    return None


async def admit(
    session: AsyncSession,
    *,
    kind: str,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    units: int,
    limits: JobLimits,
    count_active: Callable[[AsyncSession, uuid.UUID, datetime], Awaitable[int]],
    lock: str | None = None,
    budget_kinds: tuple[str, ...] | None = None,
) -> None:
    """Check every limit, then record ``units`` in the ledger; raise ``QuotaExceeded``.

    Runs in the caller's transaction before the job row exists. The check comes
    first so a refused request writes nothing, even if the caller's session
    commits after catching the exception.
    """
    now = datetime.now(UTC)
    if session.get_bind().dialect.name == "postgresql":
        # ponytail: one admission lock per lock key; per-org locks if admission rate ever matters
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"admit:{lock or kind}"},
        )
    request = _Request(kind, organization_id, user_id, units, budget_kinds or (kind,))
    if code := await _ledger_breach(session, limits, request, now):
        raise QuotaExceeded(code)
    if limits.per_org_concurrent is not None:
        active = await count_active(session, organization_id, now)
        if active >= limits.per_org_concurrent:
            raise QuotaExceeded("org_concurrent")
    session.add(
        UsageLedger(
            organization_id=organization_id,
            user_id=user_id,
            kind=kind,
            units=units,
            created_at=now,
        )
    )
    await session.flush()


@dataclass(frozen=True)
class _Job:
    session_factory: SessionFactory
    table: Table
    id: uuid.UUID
    organization_id: uuid.UUID


async def _fail(job: _Job, *, expected: str, reason: str) -> None:
    """Best effort: a row this cannot fail is failed by the stale rule on its next read."""
    try:
        async with job.session_factory() as session:
            await set_postgres_tenant_context(session, str(job.organization_id))
            await transition(
                session,
                job.table,
                job.id,
                expected=expected,
                new="failed",
                status_reason=reason,
                completed_at=datetime.now(UTC),
            )
            await session.commit()
    except (SQLAlchemyError, OSError) as exc:
        logger.warning(
            "document_job_fail_write", job_id=str(job.id), error_type=type(exc).__name__
        )


async def _start(job: _Job) -> bool:
    async with job.session_factory() as session:
        await set_postgres_tenant_context(session, str(job.organization_id))
        started = await transition(
            session,
            job.table,
            job.id,
            expected="queued",
            new="running",
            started_at=datetime.now(UTC),
        )
        await session.commit()
    return started


async def _finish(
    job: _Job,
    result: Any,
    persist: Callable[[AsyncSession, Any], Awaitable[Outcome]],
) -> None:
    """Persist ``result`` only if the row is still running; otherwise discard it."""
    table = job.table
    async with job.session_factory() as session:
        await set_postgres_tenant_context(session, str(job.organization_id))
        locked = (
            await session.execute(
                select(table.c.id)
                .where(table.c.id == job.id, table.c.status == "running")
                .with_for_update()
            )
        ).first()
        if locked is None:
            return
        try:
            final, fields = await persist(session, result)
            if not await transition(
                session,
                table,
                job.id,
                expected="running",
                new=final,
                completed_at=datetime.now(UTC),
                **fields,
            ):
                await session.rollback()
                return
            await session.commit()
        except IntegrityError as exc:
            # The row is locked above, so a deleted parent cannot reach here: this is a
            # constraint the result violated, and the job must fail, not stay running.
            await session.rollback()
            logger.warning(
                "document_job_failed",
                job_id=str(job.id),
                reason="internal_error",
                error_type=type(exc).__name__,
            )
            await _fail(job, expected="running", reason="internal_error")


async def run_job(
    *,
    table: Table,
    job_id: uuid.UUID,
    organization_id: uuid.UUID,
    created_at: datetime,
    deadline_s: float,
    session_factory: SessionFactory,
    semaphore: asyncio.Semaphore,
    work: Callable[[], Awaitable[_T]],
    persist: Callable[[AsyncSession, _T], Awaitable[Outcome]],
) -> None:
    """Run ``work`` under the deadline and persist its result, moving status by CAS.

    ``persist`` inserts the dependent rows and returns the final status and any
    further columns to set on the job row. It runs only while the row is still
    ``running``, so a deleted or already-failed job discards its result.
    """
    job = _Job(session_factory, table, job_id, organization_id)
    fail = functools.partial(_fail, job)
    try:
        async with semaphore:
            elapsed = (datetime.now(UTC) - _aware(created_at)).total_seconds()
            if (remaining := deadline_s - elapsed) <= 0:
                await fail(expected="queued", reason="deadline_exceeded")
                return
            if not await _start(job):
                return
            try:
                async with asyncio.timeout(remaining) as deadline:
                    result = await work()
            except TimeoutError:
                # A TimeoutError that work raised itself is not the deadline.
                reason = "deadline_exceeded" if deadline.expired() else "internal_error"
                logger.warning("document_job_failed", job_id=str(job_id), reason=reason)
                await fail(expected="running", reason=reason)
                return
            await _finish(job, result, persist)
    except (asyncio.CancelledError, Exception) as exc:
        cancelled = isinstance(exc, asyncio.CancelledError)
        reason = "interrupted" if cancelled else "internal_error"
        logger.warning(
            "document_job_failed",
            job_id=str(job_id),
            reason=reason,
            error_type=type(exc).__name__,
        )
        # The row is queued until _start commits and running after; the CAS that does not
        # match is a no-op, so trying both covers a cancellation landing between them.
        await fail(expected="running", reason=reason)
        await fail(expected="queued", reason=reason)
        if cancelled:
            raise


async def cancel_all(tasks: set[asyncio.Task[None]]) -> None:
    """Cancel every scheduled job and wait until each has recorded its interruption."""
    for task in tuple(tasks):
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
