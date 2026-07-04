"""
Result repository for research findings management.

Provides specialized operations for research results and findings.
"""

from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import String, and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db.research_result import ResearchResult, ResultType
from src.repositories.base import BaseRepository


class ResultRepository(BaseRepository[ResearchResult]):
    """
    Repository for research result operations.

    Manages findings, sources, citations, and analysis results.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Initialize result repository."""
        super().__init__(ResearchResult, session)

    async def get_by_project(
        self,
        project_id: UUID,
        result_type: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ResearchResult]:
        """
        Get results for a project.

        Args:
            project_id: Project ID
            result_type: Filter by result type
            limit: Maximum results
            offset: Skip results

        Returns:
            List of results
        """
        filters: dict[str, Any] = {"project_id": project_id}

        if result_type:
            filters["result_type"] = result_type

        return await self.get_many(
            filters=filters,
            limit=limit,
            offset=offset,
            order_by="confidence_score",
            order_desc=True,
        )

    async def get_by_agent(
        self, project_id: UUID, agent_type: str
    ) -> list[ResearchResult]:
        """
        Get results produced by a specific agent.

        Args:
            project_id: Project ID
            agent_type: Agent type

        Returns:
            List of results from agent
        """
        query = (
            self.build_query()
            .where(
                and_(
                    ResearchResult.project_id == project_id,
                    ResearchResult.agent_type == agent_type,
                )
            )
            .order_by(ResearchResult.created_at.desc())
        )

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def bulk_create(self, results: list[dict[str, Any]]) -> list[ResearchResult]:
        """
        Efficiently create multiple results.

        Args:
            results: List of result data

        Returns:
            List of created results
        """
        if not results:
            return []

        # Use PostgreSQL's INSERT ... RETURNING for efficiency
        stmt = insert(ResearchResult).values(results).returning(ResearchResult)

        result = await self.session.execute(stmt)
        created_results = list(result.scalars().all())

        await self.session.flush()
        return created_results

    async def get_high_confidence(
        self, project_id: UUID, min_confidence: float = 0.7
    ) -> list[ResearchResult]:
        """
        Get high-confidence results.

        Args:
            project_id: Project ID
            min_confidence: Minimum confidence score

        Returns:
            List of high-confidence results
        """
        query = (
            self.build_query()
            .where(
                and_(
                    ResearchResult.project_id == project_id,
                    ResearchResult.confidence_score >= min_confidence,
                )
            )
            .order_by(ResearchResult.confidence_score.desc())
        )

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def aggregate_by_type(
        self, project_id: UUID
    ) -> dict[str, list[ResearchResult]]:
        """
        Group results by type.

        Args:
            project_id: Project ID

        Returns:
            Dictionary of results grouped by type
        """
        results = await self.get_by_project(project_id)

        grouped: defaultdict[str, list[ResearchResult]] = defaultdict(list)
        for result in results:
            grouped[result.result_type].append(result)

        return dict(grouped)

    async def search_content(
        self,
        search_term: str,
        project_id: UUID | None = None,
        result_types: list[str] | None = None,
        limit: int = 50,
    ) -> list[ResearchResult]:
        """
        Search in result content.

        Args:
            search_term: Search term
            project_id: Filter by project
            result_types: Filter by result types
            limit: Maximum results

        Returns:
            List of matching results
        """
        query = self.build_query()

        # Search in JSON content (PostgreSQL specific)
        # This searches for the term in any JSON field
        query = query.where(
            func.cast(ResearchResult.content, String).ilike(f"%{search_term}%")
        )

        if project_id:
            query = query.where(ResearchResult.project_id == project_id)

        if result_types:
            query = query.where(ResearchResult.result_type.in_(result_types))

        query = query.order_by(ResearchResult.confidence_score.desc()).limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_citations(self, project_id: UUID) -> list[ResearchResult]:
        """
        Get all citations for a project.

        Args:
            project_id: Project ID

        Returns:
            List of citation results
        """
        return await self.get_by_project(
            project_id, result_type=ResultType.CITATION.value
        )

    async def get_sources(
        self, project_id: UUID, unique: bool = True
    ) -> list[ResearchResult]:
        """
        Get all sources for a project.

        Args:
            project_id: Project ID
            unique: Return only unique sources

        Returns:
            List of source results
        """
        query = self.build_query().where(
            and_(
                ResearchResult.project_id == project_id,
                ResearchResult.result_type == ResultType.SOURCE.value,
            )
        )

        if unique:
            # Use DISTINCT ON for PostgreSQL
            query = query.distinct(ResearchResult.source_id)

        query = query.order_by(
            ResearchResult.source_id, ResearchResult.confidence_score.desc()
        )

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def merge_duplicates(self, project_id: UUID) -> int:
        """
        Merge duplicate results based on content similarity.

        Args:
            project_id: Project ID

        Returns:
            Number of duplicates merged
        """
        # Get all results for project
        results = await self.get_by_project(project_id)

        # Group by source_id and result_type
        duplicates = defaultdict(list)
        for result in results:
            if result.source_id:
                key = (result.source_id, result.result_type)
                duplicates[key].append(result)

        merged_count = 0

        # Merge duplicates by keeping highest confidence score
        for _key, duplicate_list in duplicates.items():
            if len(duplicate_list) > 1:
                # Sort by confidence score
                duplicate_list.sort(key=lambda x: x.confidence_score or 0, reverse=True)

                # Keep the first (highest confidence) and delete others
                keep = duplicate_list[0]
                for duplicate in duplicate_list[1:]:
                    # Merge metadata
                    if duplicate.result_metadata:
                        keep.add_metadata("merged_from", str(duplicate.id))

                    # Delete duplicate
                    await self.delete(duplicate.id, soft=True)
                    merged_count += 1

        if merged_count > 0:
            await self.session.flush()

        return merged_count

    async def get_statistics(self, project_id: UUID) -> dict[str, Any]:
        """
        Get result statistics for a project.

        Args:
            project_id: Project ID

        Returns:
            Statistics dictionary
        """
        # Total results
        total_query = select(func.count(ResearchResult.id)).where(
            and_(
                ResearchResult.project_id == project_id,
                ResearchResult.deleted_at.is_(None),
            )
        )
        result = await self.session.execute(total_query)
        total = result.scalar() or 0

        # Results by type
        type_query = (
            select(
                ResearchResult.result_type, func.count(ResearchResult.id).label("count")
            )
            .where(
                and_(
                    ResearchResult.project_id == project_id,
                    ResearchResult.deleted_at.is_(None),
                )
            )
            .group_by(ResearchResult.result_type)
        )

        result = await self.session.execute(type_query)
        type_distribution = {row.result_type: row.count for row in result}

        # Average confidence score
        avg_conf_query = select(func.avg(ResearchResult.confidence_score)).where(
            and_(
                ResearchResult.project_id == project_id,
                ResearchResult.confidence_score.isnot(None),
                ResearchResult.deleted_at.is_(None),
            )
        )
        result = await self.session.execute(avg_conf_query)
        avg_confidence = result.scalar() or 0.0

        # High confidence results
        high_conf_query = select(func.count(ResearchResult.id)).where(
            and_(
                ResearchResult.project_id == project_id,
                ResearchResult.confidence_score >= 0.7,
                ResearchResult.deleted_at.is_(None),
            )
        )
        result = await self.session.execute(high_conf_query)
        high_confidence_count = result.scalar() or 0

        # Unique sources
        unique_sources_query = select(
            func.count(func.distinct(ResearchResult.source_id))
        ).where(
            and_(
                ResearchResult.project_id == project_id,
                ResearchResult.source_id.isnot(None),
                ResearchResult.deleted_at.is_(None),
            )
        )
        result = await self.session.execute(unique_sources_query)
        unique_sources = result.scalar() or 0

        # Agent contribution
        agent_query = (
            select(
                ResearchResult.agent_type, func.count(ResearchResult.id).label("count")
            )
            .where(
                and_(
                    ResearchResult.project_id == project_id,
                    ResearchResult.agent_type.isnot(None),
                    ResearchResult.deleted_at.is_(None),
                )
            )
            .group_by(ResearchResult.agent_type)
        )

        result = await self.session.execute(agent_query)
        agent_contribution = {row.agent_type: row.count for row in result}

        return {
            "total_results": total,
            "type_distribution": type_distribution,
            "average_confidence": float(avg_confidence),
            "high_confidence_count": high_confidence_count,
            "high_confidence_percentage": (
                high_confidence_count / total * 100 if total > 0 else 0
            ),
            "unique_sources": unique_sources,
            "agent_contribution": agent_contribution,
        }

    async def update_confidence_scores(self, updates: list[dict[str, Any]]) -> int:
        """
        Batch update confidence scores.

        Args:
            updates: List of dicts with 'id' and 'confidence_score'

        Returns:
            Number of updated results
        """
        updated_count = 0

        for update_data in updates:
            result_id = update_data.get("id")
            confidence = update_data.get("confidence_score")

            if result_id and confidence is not None:
                result = await self.get(result_id)
                if result:
                    result.set_confidence(confidence)
                    updated_count += 1

        if updated_count > 0:
            await self.session.flush()

        return updated_count


__all__ = ["ResultRepository"]
