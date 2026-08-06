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
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.contracts import Attempt, Run, Task
from src.core.contracts.states import TERMINAL_RUN_STATUSES
from src.models.db.run_lifecycle import AgentRun, AgentRunTask, AgentTaskAttempt
from src.repositories.tenant_scope import (
    TenantMismatchError,
    enforce_tenant_identity,
    normalize_organization_id,
)

OrganizationId = uuid.UUID | str | None


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

    async def record_run_transition(
        self, run: Run, *, organization_id: OrganizationId
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
        self, task: Task, *, organization_id: OrganizationId
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
        self, attempt: Attempt, *, organization_id: OrganizationId
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


__all__ = ["RunLifecycleRepository"]
