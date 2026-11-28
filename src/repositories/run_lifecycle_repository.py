"""Repository for the durable run, task, and attempt lifecycle.

Writes take the canonical Pydantic contracts (``Run``, ``Task``, ``Attempt``)
that already validated the transition — this repository records their result,
it never re-implements the status machine. Runs, tasks, and attempts are
mutable rows (unlike the append-only event and config-snapshot tables): a
transition updates the existing row in place.

Every write enforces the accepted tenant identity contract from
``src.repositories.tenant_scope``: a run is written with the caller's org
context directly; a task or attempt copies ``organization_id`` from its
already-persisted parent run rather than re-deriving it from ambient context,
so a child row can never disagree with the run that owns it.
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, TypeVar

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import Attempt, Run, Task
from src.core.contracts.states import (
    ATTEMPT_TRANSITIONS,
    RUN_TRANSITIONS,
    TASK_TRANSITIONS,
    TERMINAL_RUN_STATUSES,
    AttemptStatus,
    InvalidTransitionError,
    RunStatus,
    TaskStatus,
)
from src.models.db.run_lifecycle import AgentRun, AgentRunTask, AgentTaskAttempt
from src.repositories.model_call_reservation_repository import (
    OwnerBudget,
    OwnerLostError,
)
from src.repositories.tenant_scope import (
    TenantMismatchError,
    enforce_tenant_identity,
    normalize_organization_id,
)

OrganizationId = uuid.UUID | str | None

_TERMINAL_RUN_VALUES = [status.value for status in TERMINAL_RUN_STATUSES]
_ACTIVE_RUN_VALUES = tuple(
    status.value for status in RunStatus if status not in TERMINAL_RUN_STATUSES
)

RUN_CONCURRENCY_CAP_EXCEEDED = "run_concurrency_cap_exceeded"
RUN_DAILY_CAP_EXCEEDED = "run_daily_cap_exceeded"


class RunCapExceededError(RuntimeError):
    """An organization's concurrent or daily run admission cap was exceeded."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class StaleFenceError(RuntimeError):
    """The writer's lease ended or its fencing token is no longer current."""


_S = TypeVar("_S", bound=StrEnum)


def _ensure_reachable(
    entity: str,
    current: str,
    target: _S,
    status_type: type[_S],
    transitions: Mapping[_S, frozenset[_S]],
) -> None:
    """Refuse a write the durable row cannot legally accept.

    The contract validated the writer's copy; this validates the row, which
    another process may have moved on (a cancellation request, a
    reconciliation). Writing the same status again is allowed so a replayed
    commit stays idempotent.
    """
    if target.value != current and target not in transitions[status_type(current)]:
        raise InvalidTransitionError(
            f"{entity} is {current!r} in the durable record; it cannot become "
            f"{target.value!r}"
        )


class RunLifecycleRepository:
    """Durable persistence for ``agent_runs``, ``agent_run_tasks``, and
    ``agent_task_attempts``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- runs ----------------------------------------------------------

    async def create_run(
        self, run: Run, *, organization_id: OrganizationId
    ) -> AgentRun:
        """Insert a new run row from an admitted ``Run`` contract.

        Args:
            run: The validated run contract, in whatever status it was
                admitted in (typically ``CREATED``).
            organization_id: The authenticated tenant boundary. Must equal
                ``run.tenant_id`` once normalized.

        Returns:
            The persisted row.

        Raises:
            MissingOrganizationContextError: No org context was supplied.
            TenantMismatchError: ``run.tenant_id`` disagrees with
                ``organization_id``.
        """
        normalized_organization_id = normalize_organization_id(organization_id)
        enforce_tenant_identity(
            tenant_id=run.tenant_id, organization_id=normalized_organization_id
        )
        row = AgentRun(
            run_id=run.run_id,
            tenant_id=run.tenant_id,
            organization_id=normalized_organization_id,
            workflow_definition_id=run.workflow_definition_id,
            workflow_definition_version=run.workflow_definition_version,
            routing_policy_id=run.routing_policy_id,
            routing_policy_version=run.routing_policy_version,
            idempotency_key=run.idempotency_key,
            requested_by=run.requested_by,
            status=run.status.value,
            started_at=run.started_at,
            completed_at=run.completed_at,
            status_reason=run.status_reason,
            cancellation_requested_at=run.cancellation_requested_at,
            cancellation_reason=run.cancellation_reason,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def enforce_admission_caps(
        self,
        organization_id: OrganizationId,
        *,
        max_concurrent_per_org: int,
        max_daily_per_org: int,
        now: datetime | None = None,
    ) -> None:
        """Refuse admission when the org exceeds its concurrent or 24h run cap.

        Must run after ``create_run`` has flushed the caller's row so a same-key
        duplicate hits the unique constraint instead of being refused as over
        cap; comparisons therefore use ``>``. On PostgreSQL a transaction-scoped
        advisory lock serializes distinct admissions for the same organization,
        and under PostgreSQL's default ``READ COMMITTED`` isolation level the
        post-lock count observes any winning admission that committed while this
        transaction waited.
        """
        if max_concurrent_per_org < 1:
            raise ValueError("max_concurrent_per_org must be >= 1")
        if max_daily_per_org < 1:
            raise ValueError("max_daily_per_org must be >= 1")
        if now is not None and now.tzinfo is None:
            raise ValueError("now must be timezone-aware")

        normalized_organization_id = normalize_organization_id(organization_id)
        is_postgres = self.session.get_bind().dialect.name == "postgresql"

        if is_postgres:
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"run-admission:{normalized_organization_id}"},
            )

        window_start: Any
        if now is not None:
            window_start = now - timedelta(hours=24)
        elif is_postgres:
            window_start = func.now() - timedelta(hours=24)
        else:
            window_start = datetime.now(UTC) - timedelta(hours=24)

        active_predicate = AgentRun.status.in_(_ACTIVE_RUN_VALUES)
        recent_predicate = AgentRun.created_at >= window_start

        active_count, daily_count = (
            await self.session.execute(
                select(
                    func.count().filter(active_predicate),
                    func.count().filter(recent_predicate),
                ).where(
                    AgentRun.organization_id == normalized_organization_id,
                    or_(active_predicate, recent_predicate),
                )
            )
        ).one()

        if active_count > max_concurrent_per_org:
            raise RunCapExceededError(
                RUN_CONCURRENCY_CAP_EXCEEDED,
                f"organization {normalized_organization_id} exceeded concurrent "
                f"run cap ({max_concurrent_per_org})",
            )
        if daily_count > max_daily_per_org:
            raise RunCapExceededError(
                RUN_DAILY_CAP_EXCEEDED,
                f"organization {normalized_organization_id} exceeded daily "
                f"run cap ({max_daily_per_org})",
            )

    async def record_run_transition(
        self,
        run: Run,
        *,
        organization_id: OrganizationId,
        fencing_token: int | None = None,
    ) -> AgentRun:
        """Persist a run contract's current state onto its existing row.

        Args:
            run: The run contract after ``transition_to``/
                ``request_cancellation`` was applied.
            organization_id: The authenticated tenant boundary. Must equal
                ``run.tenant_id`` once normalized and must match the row's
                stored organization.

        Returns:
            The updated row.

        Raises:
            MissingOrganizationContextError: No org context was supplied.
            TenantMismatchError: ``run.tenant_id`` disagrees with
                ``organization_id``, or the row belongs to a different org.
            ValueError: No row exists for ``run.run_id``.
            StaleFenceError: ``fencing_token`` is given and is no longer the
                run's live lease.
            InvalidTransitionError: The stored status cannot reach
                ``run.status`` (another process moved the run first).
        """
        normalized_organization_id = normalize_organization_id(organization_id)
        enforce_tenant_identity(
            tenant_id=run.tenant_id, organization_id=normalized_organization_id
        )
        row = await self._get_run_row(run.run_id)
        if row is None:
            raise ValueError(f"run {run.run_id!r} does not exist")
        if row.organization_id != normalized_organization_id:
            raise TenantMismatchError(
                f"run {run.run_id!r} does not belong to organization "
                f"{normalized_organization_id}"
            )
        await self._lock_for_write(row, row.run_id, fencing_token)
        _ensure_reachable("run", row.status, run.status, RunStatus, RUN_TRANSITIONS)
        row.status = run.status.value
        row.started_at = run.started_at
        row.completed_at = run.completed_at
        row.status_reason = run.status_reason
        row.cancellation_requested_at = run.cancellation_requested_at
        row.cancellation_reason = run.cancellation_reason
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def get_run(
        self, run_id: str, *, organization_id: OrganizationId = None
    ) -> AgentRun | None:
        """Fetch one run row, optionally scoped to an organization."""
        return await self._get_run_row(run_id, organization_id=organization_id)

    async def list_non_terminal_runs(
        self, *, organization_id: OrganizationId = None
    ) -> list[AgentRun]:
        """Return every run not yet in a terminal status, oldest first.

        Passing ``organization_id=None`` scans every tenant — the shape a
        boot-time recovery sweep needs, since it runs before any authenticated
        request context exists. Passing a value scopes the scan to one tenant.
        """
        terminal_values = [status.value for status in TERMINAL_RUN_STATUSES]
        query = (
            select(AgentRun)
            .where(AgentRun.status.notin_(terminal_values))
            .order_by(AgentRun.created_at)
        )
        if organization_id is not None:
            query = query.where(
                AgentRun.organization_id == normalize_organization_id(organization_id)
            )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def _get_run_row(
        self, run_id: str, *, organization_id: OrganizationId = None
    ) -> AgentRun | None:
        query = select(AgentRun).where(AgentRun.run_id == run_id)
        if organization_id is not None:
            query = query.where(
                AgentRun.organization_id == normalize_organization_id(organization_id)
            )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    # --- ownership lease -------------------------------------------------

    async def acquire_lease(
        self,
        run_id: str,
        *,
        owner: str,
        ttl_seconds: float,
        organization_id: OrganizationId = None,
    ) -> int | None:
        """Take a run's lease if it has none or its lease has expired.

        One conditional UPDATE on database time, so two owners cannot both win.
        Every acquisition raises the fencing token, which is what stops the
        previous holder's late writes.

        Returns:
            The new fencing token, or ``None`` when another owner's lease is
            live or the run is terminal.
        """
        stmt = (
            update(AgentRun)
            .where(
                AgentRun.run_id == run_id,
                AgentRun.status.notin_(_TERMINAL_RUN_VALUES),
                or_(
                    AgentRun.owner_instance_id.is_(None),
                    AgentRun.lease_expires_at <= func.clock_timestamp(),
                ),
            )
            .values(
                owner_instance_id=owner,
                fencing_token=AgentRun.fencing_token + 1,
                lease_expires_at=func.clock_timestamp()
                + timedelta(seconds=ttl_seconds),
            )
            .returning(AgentRun.fencing_token)
            .execution_options(synchronize_session=False)
        )
        if organization_id is not None:
            stmt = stmt.where(
                AgentRun.organization_id == normalize_organization_id(organization_id)
            )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def renew_lease(
        self, run_id: str, *, owner: str, fencing_token: int, ttl_seconds: float
    ) -> bool:
        """Extend the owner's lease; ``False`` means it was lost and the owner must stop.

        An expired lease can still be renewed while nobody has taken it over:
        a takeover raises the token in one locked transaction, so exactly one
        of the owner's renewal and the reconciler's takeover wins. That is what
        lets an owner that was cut off from the database for longer than the
        lease resume as soon as it is back.
        """
        result = await self.session.execute(
            update(AgentRun)
            .where(
                AgentRun.run_id == run_id,
                AgentRun.owner_instance_id == owner,
                AgentRun.fencing_token == fencing_token,
                AgentRun.status.notin_(_TERMINAL_RUN_VALUES),
            )
            .values(
                lease_expires_at=func.clock_timestamp() + timedelta(seconds=ttl_seconds)
            )
            .returning(AgentRun.id)
            .execution_options(synchronize_session=False)
        )
        return result.first() is not None

    async def release_lease(
        self, run_id: str, *, owner: str, fencing_token: int
    ) -> bool:
        """Expire the owner's lease now so reconciliation need not wait it out."""
        result = await self.session.execute(
            update(AgentRun)
            .where(
                AgentRun.run_id == run_id,
                AgentRun.owner_instance_id == owner,
                AgentRun.fencing_token == fencing_token,
                AgentRun.status.notin_(_TERMINAL_RUN_VALUES),
            )
            .values(lease_expires_at=func.clock_timestamp())
            .returning(AgentRun.id)
            .execution_options(synchronize_session=False)
        )
        return result.first() is not None

    async def cancellation_requested(self, run_id: str) -> bool:
        """Whether a cancellation was requested on the durable run, by any process."""
        requested = await self.session.scalar(
            select(AgentRun.cancellation_requested_at).where(AgentRun.run_id == run_id)
        )
        return requested is not None

    async def list_expired_runs(self, *, limit: int = 100) -> list[AgentRun]:
        """Non-terminal runs whose owner's lease has expired, oldest lease first.

        Runs with no owner at all are not listed: nothing can say that their
        executor is gone. Leases use Postgres time, so on SQLite (the
        development mode) no run is ever expired.
        """
        if self.session.get_bind().dialect.name != "postgresql":
            return []
        result = await self.session.execute(
            select(AgentRun)
            .where(
                AgentRun.status.notin_(_TERMINAL_RUN_VALUES),
                AgentRun.owner_instance_id.is_not(None),
                AgentRun.lease_expires_at <= func.clock_timestamp(),
            )
            .order_by(AgentRun.lease_expires_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def take_over_expired_run(self, run_id: str) -> AgentRun:
        """Lock a run whose owner expired and fence that owner out.

        Raises:
            StaleFenceError: The lease was renewed, released to another owner,
                or the run finished since it was listed. A live lease wins.
        """
        row = (
            await self.session.execute(
                select(AgentRun)
                .where(
                    AgentRun.run_id == run_id,
                    AgentRun.status.notin_(_TERMINAL_RUN_VALUES),
                    AgentRun.owner_instance_id.is_not(None),
                    AgentRun.lease_expires_at <= func.clock_timestamp(),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise StaleFenceError(f"run {run_id!r} has no expired lease to take over")
        row.fencing_token += 1
        await self.session.flush()
        return row

    async def require_fence(self, run_id: str, fencing_token: int) -> None:
        """Lock the run and prove ``fencing_token`` is still its owner's.

        Expiry is deliberately not part of the fence: a takeover raises the
        token, so a stale writer is one whose token no longer matches, and an
        owner whose lease merely lapsed (a database outage) keeps its right to
        write until someone actually takes the run over. The row lock
        serializes this writer against a reconciler: one of them commits first
        and the other then sees the changed row.

        Raises:
            StaleFenceError: The run was taken over, or never had this owner.
        """
        held = await self.session.execute(
            select(AgentRun.id)
            .where(
                AgentRun.run_id == run_id,
                AgentRun.fencing_token == fencing_token,
                AgentRun.owner_instance_id.is_not(None),
            )
            .with_for_update()
        )
        if held.first() is None:
            raise StaleFenceError(
                f"fencing token {fencing_token} no longer owns run {run_id!r}"
            )

    async def _lock_for_write(
        self, row: Any, run_id: str, fencing_token: int | None
    ) -> None:
        """Take the run lock first (fence-checked when fenced), then the row's.

        The run is always locked before its children so writers and
        reconcilers cannot deadlock.
        """
        if fencing_token is not None:
            await self.require_fence(run_id, fencing_token)
        await self.session.refresh(row, with_for_update=True)

    # --- tasks -----------------------------------------------------------

    async def create_task(
        self, task: Task, *, organization_id: OrganizationId
    ) -> AgentRunTask:
        """Insert a new task row, copying its tenant boundary from the run.

        Args:
            task: The validated task contract.
            organization_id: The authenticated tenant boundary; must match
                the parent run's stored organization.

        Returns:
            The persisted row.

        Raises:
            MissingOrganizationContextError: No org context was supplied.
            TenantMismatchError: The org context disagrees with the parent
                run's organization.
            ValueError: The parent run does not exist.
        """
        normalized_organization_id = normalize_organization_id(organization_id)
        parent_run = await self._get_run_row(task.run_id)
        if parent_run is None:
            raise ValueError(f"run {task.run_id!r} does not exist")
        if parent_run.organization_id != normalized_organization_id:
            raise TenantMismatchError(
                f"run {task.run_id!r} does not belong to organization "
                f"{normalized_organization_id}"
            )
        row = AgentRunTask(
            task_id=task.task_id,
            run_id=task.run_id,
            organization_id=parent_run.organization_id,
            task_key=task.task_key,
            task_type=task.task_type,
            objective=task.objective,
            idempotency_key=task.idempotency_key,
            dependency_ids=list(task.dependency_ids),
            assigned_worker_type=task.assigned_worker_type,
            input=dict(task.input),
            status=task.status.value,
            started_at=task.started_at,
            completed_at=task.completed_at,
            status_reason=task.status_reason,
            cancellation_requested_at=task.cancellation_requested_at,
            cancellation_reason=task.cancellation_reason,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def record_task_transition(
        self,
        task: Task,
        *,
        organization_id: OrganizationId,
        fencing_token: int | None = None,
    ) -> AgentRunTask:
        """Persist a task contract's current state onto its existing row."""
        normalized_organization_id = normalize_organization_id(organization_id)
        row = await self._get_task_row(task.task_id)
        if row is None:
            raise ValueError(f"task {task.task_id!r} does not exist")
        if row.organization_id != normalized_organization_id:
            raise TenantMismatchError(
                f"task {task.task_id!r} does not belong to organization "
                f"{normalized_organization_id}"
            )
        await self._lock_for_write(row, row.run_id, fencing_token)
        _ensure_reachable("task", row.status, task.status, TaskStatus, TASK_TRANSITIONS)
        row.status = task.status.value
        row.started_at = task.started_at
        row.completed_at = task.completed_at
        row.status_reason = task.status_reason
        row.cancellation_requested_at = task.cancellation_requested_at
        row.cancellation_reason = task.cancellation_reason
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def get_tasks_for_run(
        self, run_id: str, *, organization_id: OrganizationId = None
    ) -> list[AgentRunTask]:
        """Return every task for a run, in creation order."""
        query = (
            select(AgentRunTask)
            .where(AgentRunTask.run_id == run_id)
            .order_by(AgentRunTask.created_at)
        )
        if organization_id is not None:
            query = query.where(
                AgentRunTask.organization_id
                == normalize_organization_id(organization_id)
            )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def _get_task_row(
        self, task_id: str, *, organization_id: OrganizationId = None
    ) -> AgentRunTask | None:
        query = select(AgentRunTask).where(AgentRunTask.task_id == task_id)
        if organization_id is not None:
            query = query.where(
                AgentRunTask.organization_id
                == normalize_organization_id(organization_id)
            )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    # --- attempts --------------------------------------------------------

    async def create_attempt(
        self, attempt: Attempt, *, run_id: str, organization_id: OrganizationId
    ) -> AgentTaskAttempt:
        """Insert a new attempt row, copying its tenant boundary from the run.

        Args:
            attempt: The validated attempt contract.
            run_id: The owning run — the ``Attempt`` contract has no run
                reference of its own, only ``task_id``.
            organization_id: The authenticated tenant boundary; must match
                the parent run's stored organization.

        Returns:
            The persisted row.

        Raises:
            MissingOrganizationContextError: No org context was supplied.
            TenantMismatchError: The org context disagrees with the parent
                run's organization.
            ValueError: The parent run does not exist.
        """
        normalized_organization_id = normalize_organization_id(organization_id)
        parent_run = await self._get_run_row(run_id)
        if parent_run is None:
            raise ValueError(f"run {run_id!r} does not exist")
        if parent_run.organization_id != normalized_organization_id:
            raise TenantMismatchError(
                f"run {run_id!r} does not belong to organization "
                f"{normalized_organization_id}"
            )
        row = AgentTaskAttempt(
            attempt_id=attempt.attempt_id,
            task_id=attempt.task_id,
            run_id=run_id,
            organization_id=parent_run.organization_id,
            ordinal=attempt.ordinal,
            idempotency_key=attempt.idempotency_key,
            executor_id=attempt.executor_id,
            status=attempt.status.value,
            started_at=attempt.started_at,
            completed_at=attempt.completed_at,
            status_reason=attempt.status_reason,
            cancellation_requested_at=attempt.cancellation_requested_at,
            cancellation_reason=attempt.cancellation_reason,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def record_attempt_transition(
        self,
        attempt: Attempt,
        *,
        organization_id: OrganizationId,
        fencing_token: int | None = None,
    ) -> AgentTaskAttempt:
        """Persist an attempt contract's current state onto its existing row."""
        normalized_organization_id = normalize_organization_id(organization_id)
        row = await self._get_attempt_row(attempt.attempt_id)
        if row is None:
            raise ValueError(f"attempt {attempt.attempt_id!r} does not exist")
        if row.organization_id != normalized_organization_id:
            raise TenantMismatchError(
                f"attempt {attempt.attempt_id!r} does not belong to organization "
                f"{normalized_organization_id}"
            )
        await self._lock_for_write(row, row.run_id, fencing_token)
        _ensure_reachable(
            "attempt", row.status, attempt.status, AttemptStatus, ATTEMPT_TRANSITIONS
        )
        row.status = attempt.status.value
        row.started_at = attempt.started_at
        row.completed_at = attempt.completed_at
        row.status_reason = attempt.status_reason
        row.cancellation_requested_at = attempt.cancellation_requested_at
        row.cancellation_reason = attempt.cancellation_reason
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def write_journaled_result(
        self,
        attempt_id: str,
        *,
        organization_id: OrganizationId,
        result: dict[str, Any],
        fencing_token: int | None = None,
    ) -> AgentTaskAttempt:
        """Record the nondeterministic result recovery must replay.

        Args:
            attempt_id: The attempt the result belongs to.
            organization_id: The authenticated tenant boundary; must match
                the attempt's stored organization.
            result: The journaled payload — a provider response, a tool
                result, a generated identifier.

        Returns:
            The updated row.
        """
        normalized_organization_id = normalize_organization_id(organization_id)
        row = await self._get_attempt_row(attempt_id)
        if row is None:
            raise ValueError(f"attempt {attempt_id!r} does not exist")
        if row.organization_id != normalized_organization_id:
            raise TenantMismatchError(
                f"attempt {attempt_id!r} does not belong to organization "
                f"{normalized_organization_id}"
            )
        await self._lock_for_write(row, row.run_id, fencing_token)
        row.journaled_result = result
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def get_attempts_for_task(
        self, task_id: str, *, organization_id: OrganizationId = None
    ) -> Sequence[AgentTaskAttempt]:
        """Return every attempt for a task, ordered by ordinal."""
        query = (
            select(AgentTaskAttempt)
            .where(AgentTaskAttempt.task_id == task_id)
            .order_by(AgentTaskAttempt.ordinal)
        )
        if organization_id is not None:
            query = query.where(
                AgentTaskAttempt.organization_id
                == normalize_organization_id(organization_id)
            )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def _get_attempt_row(
        self, attempt_id: str, *, organization_id: OrganizationId = None
    ) -> AgentTaskAttempt | None:
        query = select(AgentTaskAttempt).where(
            AgentTaskAttempt.attempt_id == attempt_id
        )
        if organization_id is not None:
            query = query.where(
                AgentTaskAttempt.organization_id
                == normalize_organization_id(organization_id)
            )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()


class ResearchRunJournalOwner:
    """The ``agent_runs`` row as the owner of a run's model-call journal.

    Live means the row is non-terminal and its lease has not expired, so a call
    made after the lease was lost, taken over or reconciled is refused before it
    is reserved.
    """

    kind = "research_run"

    def __init__(self, run_id: str, *, max_tokens: int, max_cost_usd: Decimal) -> None:
        self.owner_id = run_id
        self._max_tokens = max_tokens
        self._max_cost_usd = max_cost_usd

    async def lock(
        self, session: AsyncSession, organization_id: uuid.UUID
    ) -> OwnerBudget:
        row = (
            await session.execute(
                select(
                    AgentRun.fencing_token,
                    (
                        AgentRun.status.notin_(_TERMINAL_RUN_VALUES)
                        & AgentRun.lease_expires_at.is_not(None)
                        & (AgentRun.lease_expires_at > func.clock_timestamp())
                    ).label("live"),
                )
                .where(
                    AgentRun.run_id == self.owner_id,
                    AgentRun.organization_id == organization_id,
                )
                .with_for_update()
            )
        ).first()
        if row is None:
            raise OwnerLostError(f"run {self.owner_id!r} does not exist")
        return OwnerBudget(
            max_tokens=self._max_tokens,
            max_cost_usd=self._max_cost_usd,
            fencing_token=row.fencing_token,
            live=bool(row.live),
        )


__all__ = [
    "RUN_CONCURRENCY_CAP_EXCEEDED",
    "RUN_DAILY_CAP_EXCEEDED",
    "ResearchRunJournalOwner",
    "RunCapExceededError",
    "RunLifecycleRepository",
    "StaleFenceError",
]
