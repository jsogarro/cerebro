"""
Research project repository.

Specialized repository for research project operations.
"""

from datetime import UTC, datetime, timedelta
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
        user_id: str | UUID,
        status: ProjectStatus | None = None,
        limit: int | None = None,
        offset: int | None = None,
        organization_id: str | UUID | None = None,
    ) -> list[ResearchProject]:
        """
        Get projects by user.

        Args:
            user_id: User ID
            status: Filter by status
            limit: Maximum results
            offset: Skip results
            organization_id: Tenant organization boundary

        Returns:
            List of projects
        """
        filters: dict[str, Any] = {"user_id": str(user_id)}
        if status:
            filters["status"] = status

        return await self.get_many(
            filters=filters,
            limit=limit,
            offset=offset,
            order_by="created_at",
            order_desc=True,
            load_relationships=["agent_tasks", "results"],
            organization_id=organization_id,
        )

    async def get_for_user(
        self,
        project_id: UUID,
        user_id: str | UUID,
        organization_id: str | UUID,
        load_relationships: list[str] | None = None,
    ) -> ResearchProject | None:
        """
        Get a project by ID within both user and organization boundaries.

        Args:
            project_id: Project ID
            user_id: Authenticated user boundary
            organization_id: Tenant organization boundary
            load_relationships: List of relationships to eager load

        Returns:
            Project or None if outside the tenant/user boundary
        """
        query = self.build_query().where(
            ResearchProject.id == project_id,
            ResearchProject.user_id == str(user_id),
        )
        query = self.apply_organization_scope(query, organization_id)

        if load_relationships:
            for rel in load_relationships:
                query = query.options(selectinload(getattr(ResearchProject, rel)))

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_in_progress(
        self,
        user_id: UUID | None = None,
        organization_id: str | UUID | None = None,
    ) -> list[ResearchProject]:
        """
        Get all in-progress projects.

        Args:
            user_id: Filter by user (optional)
            organization_id: Tenant organization boundary

        Returns:
            List of in-progress projects
        """
        query = self.build_query().where(
            ResearchProject.status == ProjectStatus.IN_PROGRESS
        )
        query = self.apply_organization_scope(query, organization_id)

        if user_id:
            query = query.where(ResearchProject.user_id == user_id)

        query = query.options(
            selectinload(ResearchProject.agent_tasks),
            selectinload(ResearchProject.results),
        )

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update_status(
        self,
        project_id: UUID,
        status: ProjectStatus,
        updated_by: str | None = None,
        organization_id: str | UUID | None = None,
    ) -> ResearchProject | None:
        """
        Update project status.

        Args:
            project_id: Project ID
            status: New status
            updated_by: User updating
            organization_id: Tenant organization boundary

        Returns:
            Updated project
        """
        return await self.update(
            project_id,
            {"status": status},
            updated_by=updated_by,
            organization_id=organization_id,
        )

    async def update_quality_score(
        self,
        project_id: UUID,
        score: float,
        organization_id: str | UUID | None = None,
    ) -> ResearchProject | None:
        """
        Update project quality score.

        Args:
            project_id: Project ID
            score: Quality score (0.0 to 1.0)
            organization_id: Tenant organization boundary

        Returns:
            Updated project
        """
        if not 0.0 <= score <= 1.0:
            raise ValueError("Quality score must be between 0.0 and 1.0")

        return await self.update(
            project_id, {"quality_score": score}, organization_id=organization_id
        )

    async def get_with_results(
        self,
        project_id: UUID,
        organization_id: str | UUID | None = None,
    ) -> ResearchProject | None:
        """
        Get project with all results loaded.

        Args:
            project_id: Project ID
            organization_id: Tenant organization boundary

        Returns:
            Project with results
        """
        return await self.get(
            project_id,
            load_relationships=["results", "agent_tasks", "checkpoints"],
            organization_id=organization_id,
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
        organization_id: str | UUID | None = None,
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
            organization_id: Tenant organization boundary

        Returns:
            List of matching projects
        """
        stmt = self.build_query()
        stmt = self.apply_organization_scope(stmt, organization_id)

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
        self,
        user_id: UUID | None = None,
        days: int = 30,
        organization_id: str | UUID | None = None,
    ) -> dict[str, Any]:
        """
        Get project statistics.

        Args:
            user_id: Filter by user
            days: Number of days to look back
            organization_id: Tenant organization boundary

        Returns:
            Statistics dictionary
        """
        since = datetime.now(UTC) - timedelta(days=days)

        # Base query
        base_query = select(ResearchProject).where(
            and_(
                ResearchProject.deleted_at.is_(None),
                ResearchProject.created_at >= since,
            )
        )
        base_query = self.apply_organization_scope(base_query, organization_id)

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
            count_query = self.apply_organization_scope(count_query, organization_id)
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
        avg_query = self.apply_organization_scope(avg_query, organization_id)
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
        total_query = self.apply_organization_scope(total_query, organization_id)
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
        cutoff = datetime.now(UTC) - timedelta(days=days_old)

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
