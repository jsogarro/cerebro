"""
Checkpoint repository for workflow state management.

Provides operations for workflow checkpoint storage and recovery.

A checkpoint's tenant is its project's organization, so every read and the
cleanup take an organization and join through the project to enforce it. A run
named on a checkpoint must belong to that same organization.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import Delete, Select, and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db.research_project import ResearchProject
from src.models.db.run_lifecycle import AgentRun
from src.models.db.workflow_checkpoint import WorkflowCheckpoint
from src.repositories.base import BaseRepository
from src.repositories.tenant_scope import normalize_organization_id

_QueryT = TypeVar("_QueryT", bound="Select[Any] | Select[Any, Any] | Delete")


class CheckpointIdentityError(ValueError):
    """The project, run and organization offered for a checkpoint disagree."""


def _scoped(query: _QueryT, organization_id: UUID | str) -> _QueryT:
    """Restrict a checkpoint query to the projects of one organization."""
    return query.where(
        WorkflowCheckpoint.project_id.in_(
            select(ResearchProject.id).where(
                ResearchProject.organization_id
                == normalize_organization_id(organization_id)
            )
        )
    )  # type: ignore[return-value]


class CheckpointRepository(BaseRepository[WorkflowCheckpoint]):
    """
    Repository for workflow checkpoint operations.

    Manages checkpoint storage, retrieval, and cleanup for workflow recovery.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize checkpoint repository."""
        super().__init__(WorkflowCheckpoint, session)

    async def get_latest(
        self, workflow_id: str, *, organization_id: UUID | str
    ) -> WorkflowCheckpoint | None:
        """
        Get the most recent checkpoint for a workflow.

        Args:
            workflow_id: Workflow identifier

        Returns:
            Latest checkpoint or None
        """
        query = _scoped(
            self.build_query().where(WorkflowCheckpoint.workflow_id == workflow_id),
            organization_id,
        )
        query = query.order_by(WorkflowCheckpoint.created_at.desc()).limit(1)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_phase(
        self, workflow_id: str, phase: str, *, organization_id: UUID | str
    ) -> WorkflowCheckpoint | None:
        """
        Get checkpoint for a specific workflow phase.

        Args:
            workflow_id: Workflow identifier
            phase: Workflow phase

        Returns:
            Checkpoint for phase or None
        """
        query = _scoped(
            self.build_query().where(
                and_(
                    WorkflowCheckpoint.workflow_id == workflow_id,
                    WorkflowCheckpoint.phase == phase,
                )
            ),
            organization_id,
        )
        query = query.order_by(WorkflowCheckpoint.created_at.desc()).limit(1)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def cleanup_old(
        self, workflow_id: str, keep_count: int = 5, *, organization_id: UUID | str
    ) -> int:
        """
        Remove old checkpoints, keeping only the most recent ones.

        Args:
            workflow_id: Workflow identifier
            keep_count: Number of checkpoints to keep

        Returns:
            Number of checkpoints deleted
        """
        keep_query = (
            _scoped(
                select(WorkflowCheckpoint.id).where(
                    and_(
                        WorkflowCheckpoint.workflow_id == workflow_id,
                        WorkflowCheckpoint.deleted_at.is_(None),
                    )
                ),
                organization_id,
            )
            .order_by(WorkflowCheckpoint.created_at.desc())
            .limit(keep_count)
        )

        result = await self.session.execute(keep_query)
        keep_ids = list(result.scalars().all())

        # Delete older checkpoints
        delete_stmt = _scoped(
            delete(WorkflowCheckpoint).where(
                and_(
                    WorkflowCheckpoint.workflow_id == workflow_id,
                    WorkflowCheckpoint.id.notin_(keep_ids),
                    WorkflowCheckpoint.deleted_at.is_(None),
                )
            ),
            organization_id,
        )

        result = await self.session.execute(delete_stmt)
        await self.session.flush()

        rowcount = getattr(result, "rowcount", None)
        if rowcount is None:
            raise RuntimeError("SQLAlchemy Result missing rowcount attribute")
        return int(rowcount)

    async def get_recovery_point(
        self,
        project_id: UUID,
        *,
        organization_id: UUID | str,
        run_id: str | None = None,
    ) -> WorkflowCheckpoint | None:
        """
        Find the best recovery checkpoint for a project.

        Args:
            project_id: Project ID
            organization_id: Tenant the caller acts for
            run_id: Restrict to this run's checkpoints; ``None`` also admits
                checkpoints written before runs were linked

        Returns:
            Best recovery checkpoint or None
        """
        query = _scoped(
            self.build_query().where(
                and_(
                    WorkflowCheckpoint.project_id == project_id,
                    WorkflowCheckpoint.is_recoverable,
                )
            ),
            organization_id,
        )
        if run_id is not None:
            query = query.where(WorkflowCheckpoint.run_id == run_id)
        query = query.order_by(WorkflowCheckpoint.created_at.desc()).limit(1)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create_checkpoint(
        self,
        workflow_id: str,
        project_id: UUID,
        checkpoint_data: dict[str, Any],
        phase: str,
        checkpoint_type: str = "automatic",
        execution_metrics: dict[str, Any] | None = None,
        run_id: str | None = None,
        *,
        organization_id: UUID | str,
    ) -> WorkflowCheckpoint:
        """
        Create a new checkpoint.

        Args:
            workflow_id: Workflow identifier
            project_id: Project ID
            checkpoint_data: State data to save
            phase: Current workflow phase
            checkpoint_type: Type of checkpoint
            execution_metrics: Performance metrics
            run_id: Durable run the checkpoint belongs to, if any
            organization_id: Tenant the caller acts for

        Returns:
            Created checkpoint

        Raises:
            CheckpointIdentityError: the project or run is not the caller's
                organization's, or the run does not exist
        """
        org = normalize_organization_id(organization_id)
        project_org = await self.session.scalar(
            select(ResearchProject.organization_id).where(
                ResearchProject.id == project_id
            )
        )
        if project_org != org:
            raise CheckpointIdentityError(
                "project is not in the checkpointing organization"
            )
        recovery_metadata: dict[str, Any] | None = None
        if run_id is not None:
            run = await self.session.scalar(
                select(AgentRun).where(AgentRun.run_id == run_id)
            )
            if run is None or run.organization_id != org:
                raise CheckpointIdentityError(
                    "run is not in the checkpointing organization"
                )
            # Pins come from the durable run so a caller cannot assert them.
            recovery_metadata = {
                "identity": {
                    "organization_id": str(org),
                    "run_id": run_id,
                    "workflow_definition_id": run.workflow_definition_id,
                    "workflow_definition_version": run.workflow_definition_version,
                    "routing_policy_id": run.routing_policy_id,
                    "routing_policy_version": run.routing_policy_version,
                }
            }
        return await self.create(
            workflow_id=workflow_id,
            project_id=project_id,
            checkpoint_data=checkpoint_data,
            phase=phase,
            checkpoint_type=checkpoint_type,
            execution_metrics=execution_metrics,
            run_id=run_id,
            recovery_metadata=recovery_metadata,
            is_recoverable=True,
        )

    async def list_checkpoints(
        self,
        workflow_id: str,
        limit: int = 10,
        phase: str | None = None,
        *,
        organization_id: UUID | str,
    ) -> list[WorkflowCheckpoint]:
        """
        List recent checkpoints for a workflow.

        Args:
            workflow_id: Workflow identifier
            limit: Maximum checkpoints to return
            phase: Filter by phase

        Returns:
            List of checkpoints
        """
        query = _scoped(
            self.build_query().where(WorkflowCheckpoint.workflow_id == workflow_id),
            organization_id,
        )

        if phase:
            query = query.where(WorkflowCheckpoint.phase == phase)

        query = query.order_by(WorkflowCheckpoint.created_at.desc()).limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def restore_from_checkpoint(
        self, checkpoint_id: UUID, *, organization_id: UUID | str
    ) -> dict[str, Any] | None:
        """
        Restore workflow state from checkpoint.

        Args:
            checkpoint_id: Checkpoint ID

        Returns:
            Checkpoint data for restoration
        """
        result = await self.session.execute(
            _scoped(
                self.build_query().where(WorkflowCheckpoint.id == checkpoint_id),
                organization_id,
            )
        )
        checkpoint = result.scalar_one_or_none()

        if not checkpoint or not checkpoint.is_recoverable:
            return None

        return {
            "checkpoint_id": str(checkpoint.id),
            "workflow_id": checkpoint.workflow_id,
            "project_id": str(checkpoint.project_id),
            "phase": checkpoint.phase,
            "checkpoint_data": checkpoint.checkpoint_data,
            "recovery_metadata": checkpoint.recovery_metadata,
            "created_at": checkpoint.created_at.isoformat(),
        }

    async def mark_error_checkpoint(
        self,
        workflow_id: str,
        project_id: UUID,
        checkpoint_data: dict[str, Any],
        phase: str,
        error_info: dict[str, Any],
        *,
        organization_id: UUID | str,
        run_id: str | None = None,
    ) -> WorkflowCheckpoint:
        """
        Create an error checkpoint for recovery.

        Args:
            workflow_id: Workflow identifier
            project_id: Project ID
            checkpoint_data: State at error
            phase: Phase where error occurred
            error_info: Error details

        Returns:
            Error checkpoint
        """
        checkpoint = await self.create_checkpoint(
            workflow_id=workflow_id,
            project_id=project_id,
            checkpoint_data=checkpoint_data,
            phase=phase,
            checkpoint_type="error",
            run_id=run_id,
            organization_id=organization_id,
        )

        checkpoint.mark_as_error_checkpoint(error_info)
        await self.session.flush()
        await self.session.refresh(checkpoint)

        return checkpoint

    async def get_checkpoint_statistics(
        self,
        workflow_id: str | None = None,
        project_id: UUID | None = None,
        *,
        organization_id: UUID | str,
    ) -> dict[str, Any]:
        """
        Get checkpoint statistics.

        Args:
            workflow_id: Filter by workflow
            project_id: Filter by project

        Returns:
            Statistics dictionary
        """
        base_query = _scoped(
            select(WorkflowCheckpoint).where(WorkflowCheckpoint.deleted_at.is_(None)),
            organization_id,
        )

        if workflow_id:
            base_query = base_query.where(WorkflowCheckpoint.workflow_id == workflow_id)
        if project_id:
            base_query = base_query.where(WorkflowCheckpoint.project_id == project_id)

        # Total checkpoints
        total_query = select(func.count(WorkflowCheckpoint.id)).select_from(
            base_query.subquery()
        )
        result = await self.session.execute(total_query)
        total = result.scalar() or 0

        # Checkpoints by type
        type_query = _scoped(
            select(
                WorkflowCheckpoint.checkpoint_type,
                func.count(WorkflowCheckpoint.id).label("count"),
            ).where(WorkflowCheckpoint.deleted_at.is_(None)),
            organization_id,
        )

        if workflow_id:
            type_query = type_query.where(WorkflowCheckpoint.workflow_id == workflow_id)
        if project_id:
            type_query = type_query.where(WorkflowCheckpoint.project_id == project_id)

        type_query = type_query.group_by(WorkflowCheckpoint.checkpoint_type)

        result = await self.session.execute(type_query)
        type_distribution = {row.checkpoint_type: row.count for row in result}

        # Checkpoints by phase
        phase_query = _scoped(
            select(
                WorkflowCheckpoint.phase,
                func.count(WorkflowCheckpoint.id).label("count"),
            ).where(WorkflowCheckpoint.deleted_at.is_(None)),
            organization_id,
        )

        if workflow_id:
            phase_query = phase_query.where(
                WorkflowCheckpoint.workflow_id == workflow_id
            )
        if project_id:
            phase_query = phase_query.where(WorkflowCheckpoint.project_id == project_id)

        phase_query = phase_query.group_by(WorkflowCheckpoint.phase)

        result = await self.session.execute(phase_query)
        phase_distribution = {row.phase: row.count for row in result}

        # Recoverable checkpoints
        recoverable_query = _scoped(
            select(func.count(WorkflowCheckpoint.id)).where(
                and_(
                    WorkflowCheckpoint.deleted_at.is_(None),
                    WorkflowCheckpoint.is_recoverable,
                )
            ),
            organization_id,
        )

        if workflow_id:
            recoverable_query = recoverable_query.where(
                WorkflowCheckpoint.workflow_id == workflow_id
            )
        if project_id:
            recoverable_query = recoverable_query.where(
                WorkflowCheckpoint.project_id == project_id
            )

        result = await self.session.execute(recoverable_query)
        recoverable = result.scalar() or 0

        return {
            "total_checkpoints": total,
            "type_distribution": type_distribution,
            "phase_distribution": phase_distribution,
            "recoverable_checkpoints": recoverable,
            "error_checkpoints": type_distribution.get("error", 0),
            "automatic_checkpoints": type_distribution.get("automatic", 0),
            "manual_checkpoints": type_distribution.get("manual", 0),
        }

    async def cleanup_expired(self, days_old: int = 30) -> int:
        """
        Clean up old checkpoints across all workflows.

        Args:
            days_old: Age in days

        Returns:
            Number of checkpoints deleted
        """
        cutoff = datetime.now(UTC) - timedelta(days=days_old)

        # Keep at least one checkpoint per workflow
        latest_query = (
            select(
                WorkflowCheckpoint.workflow_id,
                func.max(WorkflowCheckpoint.created_at).label("latest"),
            )
            .where(WorkflowCheckpoint.deleted_at.is_(None))
            .group_by(WorkflowCheckpoint.workflow_id)
        )

        latest_result = await self.session.execute(latest_query)
        latest_checkpoints = {row.workflow_id: row.latest for row in latest_result}

        # Delete old checkpoints but keep the latest for each workflow
        deleted_count = 0
        for workflow_id, latest_date in latest_checkpoints.items():
            delete_stmt = delete(WorkflowCheckpoint).where(
                and_(
                    WorkflowCheckpoint.workflow_id == workflow_id,
                    WorkflowCheckpoint.created_at < cutoff,
                    WorkflowCheckpoint.created_at != latest_date,
                    WorkflowCheckpoint.deleted_at.is_(None),
                )
            )

            result = await self.session.execute(delete_stmt)
            rowcount = getattr(result, "rowcount", None)
            if rowcount is None:
                raise RuntimeError("SQLAlchemy Result missing rowcount attribute")
            deleted_count += int(rowcount)

        await self.session.flush()
        return deleted_count


__all__ = ["CheckpointRepository"]
