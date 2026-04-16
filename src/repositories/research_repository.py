"""
Research project repository.

Specialized repository for research project operations.
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.db.research_project import ProjectStatus, ResearchProject
from src.repositories.base import BaseRepository


class ResearchRepository(BaseRepository[ResearchProject]):
    """
    Repository for research project operations.

    Provides specialized queries for research projects.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize research repository."""
        super().__init__(ResearchProject, session)

    async def get_by_user(
        self,
        user_id: UUID,
        status: ProjectStatus | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ResearchProject]:
        """
        Get projects by user.

        Args:
            user_id: User ID
            status: Filter by status
            limit: Maximum results
            offset: Skip results

        Returns:
            List of projects
        """
        filters: dict[str, Any] = {"user_id": user_id}
        if status:
            filters["status"] = status

        return await self.get_many(
            filters=filters,
            limit=limit,
            offset=offset,
            order_by="created_at",
            order_desc=True,
            load_relationships=["agent_tasks", "results"],
        )

    async def get_in_progress(
        self, user_id: UUID | None = None
    ) -> list[ResearchProject]:
        """
        Get all in-progress projects.

        Args:
            user_id: Filter by user (optional)

        Returns:
            List of in-progress projects
        """
        query = self.build_query().where(
            ResearchProject.status == ProjectStatus.IN_PROGRESS
        )

        if user_id:
            query = query.where(ResearchProject.user_id == user_id)

        query = query.options(
            selectinload(ResearchProject.agent_tasks),
            selectinload(ResearchProject.results),
        )

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update_status(
        self, project_id: UUID, status: ProjectStatus, updated_by: str | None = None
    ) -> ResearchProject | None:
        """
        Update project status.

        Args:
            project_id: Project ID
            status: New status
            updated_by: User updating

        Returns:
            Updated project
        """
        return await self.update(project_id, {"status": status}, updated_by=updated_by)

    async def update_quality_score(
        self, project_id: UUID, score: float
    ) -> ResearchProject | None:
        """
        Update project quality score.

        Args:
            project_id: Project ID
            score: Quality score (0.0 to 1.0)

        Returns:
            Updated project
        """
        if not 0.0 <= score <= 1.0:
            raise ValueError("Quality score must be between 0.0 and 1.0")

        return await self.update(project_id, {"quality_score": score})

    async def get_with_results(self, project_id: UUID) -> ResearchProject | None:
        """
        Get project with all results loaded.

        Args:
            project_id: Project ID

        Returns:
            Project with results
        """
        return await self.get(
            project_id, load_relationships=["results", "agent_tasks", "checkpoints"]
        )

    async def search_projects(
        self,
        query: str,
        domains: list[str] | None = None,
        user_id: UUID | None = None,
        status: list[ProjectStatus] | None = None,
        min_quality: float | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ResearchProject]:
        """
        Search research projects.

        Args:
            query: Search query
            domains: Filter by domains
            user_id: Filter by user
            status: Filter by status list
            min_quality: Minimum quality score
            limit: Maximum results
            offset: Skip results

        Returns:
            List of matching projects
        """
        stmt = self.build_query()

        # Text search in title and query
        if query:
            stmt = stmt.where(
                or_(
                    func.lower(ResearchProject.title).contains(query.lower()),
                    func.lower(ResearchProject.query).contains(query.lower()),
                )
            )

        # Filter by domains (JSON contains)
        if domains:
            # PostgreSQL JSON containment operator
            for domain in domains:
                stmt = stmt.where(ResearchProject.domains.contains([domain]))

        # Filter by user
        if user_id:
            stmt = stmt.where(ResearchProject.user_id == user_id)

        # Filter by status
        if status:
            stmt = stmt.where(ResearchProject.status.in_(status))

        # Filter by quality score
        if min_quality is not None:
            stmt = stmt.where(ResearchProject.quality_score >= min_quality)

        # Order by relevance (quality score and recency)
        stmt = stmt.order_by(
            ResearchProject.quality_score.desc().nullslast(),
            ResearchProject.created_at.desc(),
        )

        # Pagination
        stmt = stmt.offset(offset).limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_statistics(
        self, user_id: UUID | None = None, days: int = 30
    ) -> dict[str, Any]:
        """
        Get project statistics.

        Args:
            user_id: Filter by user
            days: Number of days to look back

        Returns:
            Statistics dictionary
        """
        since = datetime.utcnow() - timedelta(days=days)

        # Base query
        base_query = select(ResearchProject).where(
            and_(
                ResearchProject.deleted_at.is_(None),
                ResearchProject.created_at >= since,
            )
        )

        if user_id:
            base_query = base_query.where(ResearchProject.user_id == user_id)

        # Get counts by status
        status_counts = {}
        for status in ProjectStatus:
            count_query = select(func.count(ResearchProject.id)).where(
                and_(
                    ResearchProject.deleted_at.is_(None),
                    ResearchProject.status == status,
                    ResearchProject.created_at >= since,
                )
            )
            if user_id:
                count_query = count_query.where(ResearchProject.user_id == user_id)

            result = await self.session.execute(count_query)
            status_counts[status.value] = result.scalar() or 0

        # Get average quality score
        avg_query = select(func.avg(ResearchProject.quality_score)).where(
            and_(
                ResearchProject.deleted_at.is_(None),
                ResearchProject.quality_score.isnot(None),
                ResearchProject.created_at >= since,
            )
        )
        if user_id:
            avg_query = avg_query.where(ResearchProject.user_id == user_id)

        result = await self.session.execute(avg_query)
        avg_quality = result.scalar() or 0.0

        # Get total count
        total_query = select(func.count(ResearchProject.id)).where(
            and_(
                ResearchProject.deleted_at.is_(None),
                ResearchProject.created_at >= since,
            )
        )
        if user_id:
            total_query = total_query.where(ResearchProject.user_id == user_id)

        result = await self.session.execute(total_query)
        total = result.scalar() or 0

        return {
            "total_projects": total,
            "status_distribution": status_counts,
            "average_quality_score": float(avg_quality),
            "period_days": days,
            "since": since.isoformat(),
        }

    async def cleanup_stale_projects(self, days_old: int = 30) -> int:
        """
        Clean up stale in-progress projects.

        Args:
            days_old: Days since last update

        Returns:
            Number of projects marked as failed
        """
        cutoff = datetime.utcnow() - timedelta(days=days_old)

        query = select(ResearchProject).where(
            and_(
                ResearchProject.deleted_at.is_(None),
                ResearchProject.status == ProjectStatus.IN_PROGRESS,
                ResearchProject.updated_at < cutoff,
            )
        )

        result = await self.session.execute(query)
        stale_projects = list(result.scalars().all())

        count = 0
        for project in stale_projects:
            await self.update(project.id, {"status": ProjectStatus.FAILED})
            count += 1

        return count


__all__ = ["ResearchRepository"]
