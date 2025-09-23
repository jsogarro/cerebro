"""
Checkpoint repository for workflow state management.

Provides operations for workflow checkpoint storage and recovery.
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, select

from src.models.db.workflow_checkpoint import WorkflowCheckpoint
from src.repositories.base import BaseRepository


class CheckpointRepository(BaseRepository[WorkflowCheckpoint]):
    """
    Repository for workflow checkpoint operations.

    Manages checkpoint storage, retrieval, and cleanup for workflow recovery.
    """

    def __init__(self, session):
        """Initialize checkpoint repository."""
        super().__init__(WorkflowCheckpoint, session)

    async def get_latest(self, workflow_id: str) -> WorkflowCheckpoint | None:
        """
        Get the most recent checkpoint for a workflow.

        Args:
            workflow_id: Workflow identifier

        Returns:
            Latest checkpoint or None
        """
        query = (
            self.build_query()
            .where(WorkflowCheckpoint.workflow_id == workflow_id)
            .order_by(WorkflowCheckpoint.created_at.desc())
            .limit(1)
        )

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_phase(
        self, workflow_id: str, phase: str
    ) -> WorkflowCheckpoint | None:
        """
        Get checkpoint for a specific workflow phase.

        Args:
            workflow_id: Workflow identifier
            phase: Workflow phase

        Returns:
            Checkpoint for phase or None
        """
        query = (
            self.build_query()
            .where(
                and_(
                    WorkflowCheckpoint.workflow_id == workflow_id,
                    WorkflowCheckpoint.phase == phase,
                )
            )
            .order_by(WorkflowCheckpoint.created_at.desc())
            .limit(1)
        )

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def cleanup_old(self, workflow_id: str, keep_count: int = 5) -> int:
        """
        Remove old checkpoints, keeping only the most recent ones.

        Args:
            workflow_id: Workflow identifier
            keep_count: Number of checkpoints to keep

        Returns:
            Number of checkpoints deleted
        """
        # Get checkpoints to keep
        keep_query = (
            select(WorkflowCheckpoint.id)
            .where(
                and_(
                    WorkflowCheckpoint.workflow_id == workflow_id,
                    WorkflowCheckpoint.deleted_at.is_(None),
                )
            )
            .order_by(WorkflowCheckpoint.created_at.desc())
            .limit(keep_count)
        )

        result = await self.session.execute(keep_query)
        keep_ids = list(result.scalars().all())

        # Delete older checkpoints
        delete_stmt = delete(WorkflowCheckpoint).where(
            and_(
                WorkflowCheckpoint.workflow_id == workflow_id,
                WorkflowCheckpoint.id.notin_(keep_ids),
                WorkflowCheckpoint.deleted_at.is_(None),
            )
        )

        result = await self.session.execute(delete_stmt)
        await self.session.flush()

        return result.rowcount

    async def get_recovery_point(
        self, project_id: UUID
    ) -> WorkflowCheckpoint | None:
        """
        Find the best recovery checkpoint for a project.

        Args:
            project_id: Project ID

        Returns:
            Best recovery checkpoint or None
        """
        # Get the most recent recoverable checkpoint
        query = (
            self.build_query()
            .where(
                and_(
                    WorkflowCheckpoint.project_id == project_id,
                    WorkflowCheckpoint.is_recoverable == True,
                )
            )
            .order_by(WorkflowCheckpoint.created_at.desc())
            .limit(1)
        )

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

        Returns:
            Created checkpoint
        """
        checkpoint = await self.create(
            workflow_id=workflow_id,
            project_id=project_id,
            checkpoint_data=checkpoint_data,
            phase=phase,
            checkpoint_type=checkpoint_type,
            execution_metrics=execution_metrics,
            is_recoverable=True,
        )

        return checkpoint

    async def list_checkpoints(
        self, workflow_id: str, limit: int = 10, phase: str | None = None
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
        query = self.build_query().where(WorkflowCheckpoint.workflow_id == workflow_id)

        if phase:
            query = query.where(WorkflowCheckpoint.phase == phase)

        query = query.order_by(WorkflowCheckpoint.created_at.desc()).limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def restore_from_checkpoint(
        self, checkpoint_id: UUID
    ) -> dict[str, Any] | None:
        """
        Restore workflow state from checkpoint.

        Args:
            checkpoint_id: Checkpoint ID

        Returns:
            Checkpoint data for restoration
        """
        checkpoint = await self.get(checkpoint_id)

        if not checkpoint or not checkpoint.is_recoverable:
            return None

        # Return checkpoint data with metadata
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
        )

        checkpoint.mark_as_error_checkpoint(error_info)
        await self.session.flush()
        await self.session.refresh(checkpoint)

        return checkpoint

    async def get_checkpoint_statistics(
        self, workflow_id: str | None = None, project_id: UUID | None = None
    ) -> dict[str, Any]:
        """
        Get checkpoint statistics.

        Args:
            workflow_id: Filter by workflow
            project_id: Filter by project

        Returns:
            Statistics dictionary
        """
        base_query = select(WorkflowCheckpoint).where(
            WorkflowCheckpoint.deleted_at.is_(None)
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
        type_query = select(
            WorkflowCheckpoint.checkpoint_type,
            func.count(WorkflowCheckpoint.id).label("count"),
        ).where(WorkflowCheckpoint.deleted_at.is_(None))

        if workflow_id:
            type_query = type_query.where(WorkflowCheckpoint.workflow_id == workflow_id)
        if project_id:
            type_query = type_query.where(WorkflowCheckpoint.project_id == project_id)

        type_query = type_query.group_by(WorkflowCheckpoint.checkpoint_type)

        result = await self.session.execute(type_query)
        type_distribution = {row.checkpoint_type: row.count for row in result}

        # Checkpoints by phase
        phase_query = select(
            WorkflowCheckpoint.phase, func.count(WorkflowCheckpoint.id).label("count")
        ).where(WorkflowCheckpoint.deleted_at.is_(None))

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
        recoverable_query = select(func.count(WorkflowCheckpoint.id)).where(
            and_(
                WorkflowCheckpoint.deleted_at.is_(None),
                WorkflowCheckpoint.is_recoverable == True,
            )
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
        cutoff = datetime.utcnow() - timedelta(days=days_old)

        # Keep at least one checkpoint per workflow
        # Get the latest checkpoint for each workflow
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
            deleted_count += result.rowcount

        await self.session.flush()
        return deleted_count


__all__ = ["CheckpointRepository"]
