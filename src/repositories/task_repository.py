"""
Task repository for agent task management.

Provides specialized operations for agent task execution and monitoring.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db.agent_task import AgentTask, TaskStatus
from src.repositories.base import BaseRepository

if TYPE_CHECKING:
    pass


class TaskRepository(BaseRepository[AgentTask]):
    """
    Repository for agent task operations.

    Manages task execution, dependencies, and monitoring.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize task repository."""
        super().__init__(AgentTask, session)

    async def get_by_project(
        self,
        project_id: UUID,
        status: TaskStatus | None = None,
        agent_type: str | None = None,
        limit: int | None = None,
        organization_id: str | UUID | None = None,
    ) -> list[AgentTask]:
        """
        Get all tasks for a project.

        Args:
            project_id: Project ID
            status: Filter by status
            agent_type: Filter by agent type
            limit: Maximum results
            organization_id: Tenant organization boundary

        Returns:
            List of tasks
        """
        filters: dict[str, Any] = {"project_id": project_id}

        if status:
            filters["status"] = status
        if agent_type:
            filters["agent_type"] = agent_type

        return await self.get_many(
            filters=filters,
            limit=limit,
            order_by="created_at",
            order_desc=False,
            organization_id=organization_id,
        )

    async def get_pending_tasks(
        self,
        limit: int = 10,
        agent_type: str | None = None,
        organization_id: str | UUID | None = None,
    ) -> list[AgentTask]:
        """
        Get pending tasks ready for execution.

        Args:
            limit: Maximum tasks to return
            agent_type: Filter by agent type
            organization_id: Tenant organization boundary

        Returns:
            List of pending tasks
        """
        query = self.build_query().where(
            AgentTask.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED])
        )
        query = self.apply_organization_scope(query, organization_id)

        if agent_type:
            query = query.where(AgentTask.agent_type == agent_type)

        # Order by priority and creation time
        query = query.order_by(
            AgentTask.priority.desc(), AgentTask.created_at.asc()
        ).limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_failed_tasks(
        self,
        max_retries: int = 3,
        since_hours: int = 24,
        organization_id: str | UUID | None = None,
    ) -> list[AgentTask]:
        """
        Get failed tasks that can be retried.

        Args:
            max_retries: Maximum retry count
            since_hours: Hours to look back
            organization_id: Tenant organization boundary

        Returns:
            List of retryable failed tasks
        """
        since = datetime.now(UTC) - timedelta(hours=since_hours)

        query = self.build_query().where(
            and_(
                AgentTask.status == TaskStatus.FAILED,
                AgentTask.retry_count < max_retries,
                AgentTask.updated_at >= since,
            )
        )
        query = self.apply_organization_scope(query, organization_id)

        query = query.order_by(AgentTask.retry_count.asc())

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update_task_status(
        self,
        task_id: UUID,
        status: TaskStatus,
        output: dict[str, Any] | None = None,
        error: str | None = None,
        organization_id: str | UUID | None = None,
    ) -> AgentTask | None:
        """
        Update task execution status.

        Args:
            task_id: Task ID
            status: New status
            output: Task output data
            error: Error message if failed
            organization_id: Tenant organization boundary

        Returns:
            Updated task
        """
        task = await self.get(task_id, organization_id=organization_id)

        if not task:
            return None

        # Update status
        task.status = status

        # Handle status-specific updates
        if status == TaskStatus.IN_PROGRESS:
            task.start()
        elif status == TaskStatus.COMPLETED:
            if output:
                task.complete(output)
            else:
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now(UTC)
        elif status == TaskStatus.FAILED:
            task.fail(error or "Unknown error")
        elif status == TaskStatus.RETRYING:
            task.retry()

        await self.session.flush()
        await self.session.refresh(task)
        return task

    async def get_task_metrics(
        self,
        project_id: UUID,
        organization_id: str | UUID | None = None,
    ) -> dict[str, Any]:
        """
        Get task execution metrics for a project.

        Args:
            project_id: Project ID
            organization_id: Tenant organization boundary

        Returns:
            Task metrics
        """
        # Get task counts by status
        status_counts = {}
        for status in TaskStatus:
            count_query = select(func.count(AgentTask.id)).where(
                and_(
                    AgentTask.project_id == project_id,
                    AgentTask.status == status,
                    AgentTask.deleted_at.is_(None),
                )
            )
            count_query = self.apply_organization_scope(count_query, organization_id)
            result = await self.session.execute(count_query)
            status_counts[status.value] = result.scalar() or 0

        # Get average execution time
        avg_time_query = select(func.avg(AgentTask.execution_time_ms)).where(
            and_(
                AgentTask.project_id == project_id,
                AgentTask.status == TaskStatus.COMPLETED,
                AgentTask.execution_time_ms.isnot(None),
                AgentTask.deleted_at.is_(None),
            )
        )
        avg_time_query = self.apply_organization_scope(
            avg_time_query, organization_id
        )
        result = await self.session.execute(avg_time_query)
        avg_execution_time = result.scalar() or 0

        # Get agent type distribution
        agent_query = (
            select(AgentTask.agent_type, func.count(AgentTask.id).label("count"))
            .where(
                and_(AgentTask.project_id == project_id, AgentTask.deleted_at.is_(None))
            )
            .group_by(AgentTask.agent_type)
        )
        agent_query = self.apply_organization_scope(agent_query, organization_id)

        result = await self.session.execute(agent_query)
        agent_distribution = {row.agent_type: row.count for row in result}

        # Calculate success rate
        total = sum(status_counts.values())
        success_rate = (
            status_counts.get(TaskStatus.COMPLETED.value, 0) / total * 100
            if total > 0
            else 0
        )

        return {
            "total_tasks": total,
            "status_distribution": status_counts,
            "average_execution_time_ms": float(avg_execution_time),
            "agent_distribution": agent_distribution,
            "success_rate": success_rate,
            "retry_rate": (
                status_counts.get(TaskStatus.RETRYING.value, 0) / total * 100
                if total > 0
                else 0
            ),
        }

    async def get_dependencies(self, task_id: UUID) -> list[AgentTask]:
        """
        Get task dependencies.

        Args:
            task_id: Task ID

        Returns:
            List of dependency tasks
        """
        task = await self.get(task_id)

        if not task or not task.depends_on:
            return []

        # Get all dependency tasks
        query = select(AgentTask).where(
            and_(AgentTask.id.in_(task.depends_on), AgentTask.deleted_at.is_(None))
        )

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def mark_for_retry(
        self,
        task_id: UUID,
        organization_id: str | UUID | None = None,
    ) -> AgentTask | None:
        """
        Mark a failed task for retry.

        Args:
            task_id: Task ID
            organization_id: Tenant organization boundary

        Returns:
            Updated task
        """
        task = await self.get(task_id, organization_id=organization_id)

        if not task or task.status != TaskStatus.FAILED:
            return None

        task.retry()
        await self.session.flush()
        await self.session.refresh(task)
        return task

    async def batch_update_status(
        self,
        task_ids: list[UUID],
        status: TaskStatus,
        organization_id: str | UUID | None = None,
    ) -> int:
        """
        Update status for multiple tasks.

        Args:
            task_ids: List of task IDs
            status: New status
            organization_id: Tenant organization boundary

        Returns:
            Number of updated tasks
        """
        stmt = (
            update(AgentTask)
            .where(and_(AgentTask.id.in_(task_ids), AgentTask.deleted_at.is_(None)))
            .values(status=status, updated_at=datetime.now(UTC))
        )
        if organization_id is not None:
            stmt = stmt.where(AgentTask.organization_id == organization_id)

        result = await self.session.execute(stmt)
        await self.session.flush()
        rowcount = getattr(result, "rowcount", None)
        if rowcount is None:
            raise RuntimeError("SQLAlchemy Result missing rowcount attribute")
        return int(rowcount)

    async def get_ready_tasks(
        self,
        project_id: UUID,
        organization_id: str | UUID | None = None,
    ) -> list[AgentTask]:
        """
        Get tasks ready to execute (dependencies met).

        Args:
            project_id: Project ID
            organization_id: Tenant organization boundary

        Returns:
            List of ready tasks
        """
        # Get all pending tasks
        pending_tasks = await self.get_by_project(
            project_id,
            status=TaskStatus.PENDING,
            organization_id=organization_id,
        )

        # Get completed task IDs
        completed_query = select(AgentTask.id).where(
            and_(
                AgentTask.project_id == project_id,
                AgentTask.status == TaskStatus.COMPLETED,
                AgentTask.deleted_at.is_(None),
            )
        )
        completed_query = self.apply_organization_scope(
            completed_query, organization_id
        )
        result = await self.session.execute(completed_query)
        completed_ids = set(result.scalars().all())

        # Filter tasks with satisfied dependencies
        ready_tasks = []
        for task in pending_tasks:
            if task.can_start(completed_ids):
                ready_tasks.append(task)

        return ready_tasks

    async def cleanup_stale_tasks(self, hours_old: int = 24) -> int:
        """
        Mark stale in-progress tasks as failed.

        Args:
            hours_old: Hours since last update

        Returns:
            Number of tasks marked as failed
        """
        cutoff = datetime.now(UTC) - timedelta(hours=hours_old)

        stmt = (
            update(AgentTask)
            .where(
                and_(
                    AgentTask.status == TaskStatus.IN_PROGRESS,
                    AgentTask.updated_at < cutoff,
                    AgentTask.deleted_at.is_(None),
                )
            )
            .values(
                status=TaskStatus.FAILED,
                error_message="Task timed out",
                updated_at=datetime.now(UTC),
            )
        )

        result = await self.session.execute(stmt)
        await self.session.flush()
        rowcount = getattr(result, "rowcount", None)
        if rowcount is None:
            raise RuntimeError("SQLAlchemy Result missing rowcount attribute")
        return int(rowcount)


__all__ = ["TaskRepository"]
