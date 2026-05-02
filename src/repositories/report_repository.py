"""
Repository for managing generated reports.

This module provides data access operations for generated reports,
following the repository pattern with functional programming principles.
"""

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, desc, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.db.generated_report import GeneratedReport, ReportFormat
from src.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class ReportRepository(BaseRepository[GeneratedReport]):
    """Repository for managing generated reports."""
    
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(GeneratedReport, session)
    
    async def create_report(
        self,
        title: str,
        report_type: str,
        query: str,
        user_id: UUID | None = None,
        project_id: UUID | None = None,
        **kwargs: Any
    ) -> GeneratedReport:
        """
        Create a new generated report record.
        
        Args:
            title: Report title
            report_type: Type of report (comprehensive, executive_summary, etc.)
            query: Research question/query
            user_id: Optional user ID
            project_id: Optional project ID
            **kwargs: Additional report fields
            
        Returns:
            Created GeneratedReport instance
        """
        report_data = {
            'title': title,
            'report_type': report_type,
            'query': query,
            'user_id': user_id,
            'project_id': project_id,
            **kwargs
        }
        
        return await self.create(**report_data)
    
    async def get_by_workflow_id(self, workflow_id: str) -> GeneratedReport | None:
        """Get report by workflow ID."""
        filters: dict[str, Any] = {"workflow_id": workflow_id}
        results = await self.get_many(filters=filters, limit=1)
        return results[0] if results else None
    
    async def get_by_project_id(
        self,
        project_id: UUID,
        limit: int | None = None
    ) -> list[GeneratedReport]:
        """Get all reports for a project."""
        query = self.build_query().filter(GeneratedReport.project_id == project_id)
        query = query.order_by(desc(GeneratedReport.created_at))

        if limit:
            query = query.limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())
    
    async def get_by_user_id(
        self,
        user_id: UUID,
        limit: int | None = None,
        status_filter: str | None = None
    ) -> list[GeneratedReport]:
        """Get all reports for a user."""
        query = self.build_query().filter(GeneratedReport.user_id == user_id)
        
        if status_filter:
            query = query.filter(GeneratedReport.generation_status == status_filter)
        
        query = query.order_by(desc(GeneratedReport.created_at))
        
        if limit:
            query = query.limit(limit)
        
        result = await self.session.execute(query)
        return list(result.scalars().all())
    
    async def get_public_reports(
        self,
        limit: int = 50,
        offset: int = 0
    ) -> list[GeneratedReport]:
        """Get public reports."""
        query = self.build_query().filter(
            and_(
                GeneratedReport.is_public.is_(True),
                GeneratedReport.generation_status == "completed"
            )
        )
        query = query.order_by(desc(GeneratedReport.created_at))
        query = query.offset(offset).limit(limit)
        
        result = await self.session.execute(query)
        return list(result.scalars().all())
    
    async def search_reports(
        self,
        search_term: str,
        user_id: UUID | None = None,
        report_type: str | None = None,
        min_quality_score: float | None = None,
        limit: int = 50,
        offset: int = 0
    ) -> tuple[list[GeneratedReport], int]:
        """
        Search reports by various criteria.

        Args:
            search_term: Search term to match against title, query, or content
            user_id: Optional user ID filter
            report_type: Optional report type filter
            min_quality_score: Optional minimum quality score filter
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            Tuple of (reports, total_count)
        """
        # Build search filter
        search_filter = or_(
            GeneratedReport.title.ilike(f"%{search_term}%"),
            GeneratedReport.query.ilike(f"%{search_term}%"),
            GeneratedReport.content_preview.ilike(f"%{search_term}%")
        )

        # Build base query
        query = self.build_query().filter(search_filter)

        # Add additional filters
        if user_id:
            query = query.filter(GeneratedReport.user_id == user_id)

        if report_type:
            query = query.filter(GeneratedReport.report_type == report_type)

        if min_quality_score is not None:
            query = query.filter(GeneratedReport.quality_score >= min_quality_score)

        # Only include completed reports
        query = query.filter(GeneratedReport.generation_status == "completed")

        # Get total count
        count_query = query.with_only_columns(func.count(GeneratedReport.id))
        count_result = await self.session.execute(count_query)
        total_count = count_result.scalar() or 0

        # Get results with pagination
        query = query.order_by(desc(GeneratedReport.created_at))
        query = query.offset(offset).limit(limit)

        result = await self.session.execute(query)
        reports = list(result.scalars().all())

        return reports, total_count
    
    async def get_reports_by_status(
        self,
        status: str,
        limit: int | None = None
    ) -> list[GeneratedReport]:
        """Get reports by generation status."""
        query = self.build_query().filter(GeneratedReport.generation_status == status)
        query = query.order_by(desc(GeneratedReport.created_at))
        
        if limit:
            query = query.limit(limit)
        
        result = await self.session.execute(query)
        return list(result.scalars().all())
    
    async def get_pending_reports(self, limit: int = 10) -> list[GeneratedReport]:
        """Get reports pending generation."""
        return await self.get_reports_by_status("pending", limit)
    
    async def get_failed_reports(
        self,
        since: datetime | None = None,
        limit: int = 50
    ) -> list[GeneratedReport]:
        """Get failed reports, optionally filtered by date."""
        query = self.build_query().filter(GeneratedReport.generation_status == "failed")
        
        if since:
            query = query.filter(GeneratedReport.created_at >= since)
        
        query = query.order_by(desc(GeneratedReport.created_at))
        query = query.limit(limit)
        
        result = await self.session.execute(query)
        return list(result.scalars().all())
    
    async def get_report_statistics(
        self,
        user_id: UUID | None = None,
        days: int = 30
    ) -> dict[str, Any]:
        """
        Get report generation statistics.
        
        Args:
            user_id: Optional user ID to filter by
            days: Number of days to look back
            
        Returns:
            Dictionary with statistics
        """
        since_date = datetime.now(UTC) - timedelta(days=days)
        
        from sqlalchemy import select

        base_filter = GeneratedReport.created_at >= since_date

        # Total reports
        total_query = select(func.count(GeneratedReport.id)).where(base_filter)
        if user_id:
            total_query = total_query.where(GeneratedReport.user_id == user_id)
        total_result = await self.session.execute(total_query)
        total_reports = total_result.scalar() or 0

        # Reports by status
        status_query = select(
            GeneratedReport.generation_status,
            func.count(GeneratedReport.id)
        ).where(base_filter).group_by(GeneratedReport.generation_status)
        if user_id:
            status_query = status_query.where(GeneratedReport.user_id == user_id)
        status_result = await self.session.execute(status_query)
        status_counts: dict[str, int] = {row[0]: row[1] for row in status_result.all()}

        # Reports by type
        type_query = select(
            GeneratedReport.report_type,
            func.count(GeneratedReport.id)
        ).where(
            and_(base_filter, GeneratedReport.generation_status == "completed")
        ).group_by(GeneratedReport.report_type)
        if user_id:
            type_query = type_query.where(GeneratedReport.user_id == user_id)
        type_result = await self.session.execute(type_query)
        type_counts: dict[str, int] = {row[0]: row[1] for row in type_result.all()}
        
        # Average metrics for completed reports
        metrics_query = select(
            func.avg(GeneratedReport.quality_score),
            func.avg(GeneratedReport.confidence_score),
            func.avg(GeneratedReport.generation_time_seconds),
            func.avg(GeneratedReport.word_count),
            func.sum(GeneratedReport.access_count)
        ).where(
            and_(base_filter, GeneratedReport.generation_status == "completed")
        )
        if user_id:
            metrics_query = metrics_query.where(GeneratedReport.user_id == user_id)

        metrics_result = await self.session.execute(metrics_query)
        row = metrics_result.one()
        avg_quality = row[0]
        avg_confidence = row[1]
        avg_time = row[2]
        avg_words = row[3]
        total_access = row[4]
        
        return {
            'total_reports': total_reports,
            'status_counts': status_counts,
            'type_counts': type_counts,
            'average_quality_score': float(avg_quality) if avg_quality else 0.0,
            'average_confidence_score': float(avg_confidence) if avg_confidence else 0.0,
            'average_generation_time': float(avg_time) if avg_time else 0.0,
            'average_word_count': int(avg_words) if avg_words else 0,
            'total_access_count': int(total_access) if total_access else 0,
            'period_days': days,
        }
    
    async def update_report_status(
        self,
        report_id: UUID,
        status: str,
        error_message: str | None = None
    ) -> GeneratedReport | None:
        """Update report generation status."""
        report = await self.get(report_id)
        if not report:
            return None
        
        if status == "generating":
            report.mark_generation_started()
        elif status == "failed" and error_message:
            report.mark_generation_failed(error_message)
        else:
            report.generation_status = status
            if status == "completed":
                report.generation_completed_at = datetime.now(UTC)

        await self.update(report.id, {"generation_status": report.generation_status})
        return report
    
    async def mark_report_completed(
        self,
        report_id: UUID,
        formats: list[str],
        file_sizes: dict[str, int],
        generation_time: float | None = None,
        storage_path: str | None = None
    ) -> GeneratedReport | None:
        """Mark report as completed with generation details."""
        report = await self.get(report_id)
        if not report:
            return None
        
        report.mark_generation_completed(formats, file_sizes, generation_time)

        update_data: dict[str, Any] = {
            "generation_status": report.generation_status,
            "generation_completed_at": report.generation_completed_at
        }
        if storage_path:
            report.storage_path = storage_path
            update_data["storage_path"] = storage_path

        await self.update(report.id, update_data)
        return report
    
    async def increment_access_count(self, report_id: UUID) -> GeneratedReport | None:
        """Increment access count for a report."""
        report = await self.get(report_id)
        if not report:
            return None
        
        report.update_access_stats()
        await self.update(report.id, {
            "access_count": report.access_count,
            "last_accessed_at": report.last_accessed_at
        })
        return report
    
    async def cleanup_old_reports(
        self,
        days_old: int = 90,
        keep_public: bool = True,
        dry_run: bool = True
    ) -> tuple[int, list[str]]:
        """
        Clean up old reports and their files.
        
        Args:
            days_old: Delete reports older than this many days
            keep_public: Whether to keep public reports
            dry_run: If True, don't actually delete anything
            
        Returns:
            Tuple of (deleted_count, deleted_ids)
        """
        cutoff_date = datetime.now(UTC) - timedelta(days=days_old)
        
        query = self.build_query().filter(GeneratedReport.created_at < cutoff_date)
        
        if keep_public:
            query = query.filter(GeneratedReport.is_public.is_(False))
        
        result = await self.session.execute(query)
        old_reports = list(result.scalars().all())

        deleted_ids: list[str] = []
        deleted_count = 0
        
        for report in old_reports:
            if not dry_run:
                # Delete associated files
                if report.storage_path and os.path.exists(report.storage_path):
                    try:
                        import shutil
                        shutil.rmtree(report.storage_path)
                        logger.info(f"Deleted files for report {report.id}")
                    except Exception as e:
                        logger.error(f"Failed to delete files for report {report.id}: {e}")
                
                # Delete database record
                await self.delete(report.id)
            
            deleted_ids.append(str(report.id))
            deleted_count += 1
        
        if not dry_run:
            await self.session.commit()
        
        return deleted_count, deleted_ids
    
    async def get_report_with_formats(self, report_id: UUID) -> GeneratedReport | None:
        """Get report with all format files loaded."""
        formats_attr = cast(Any, GeneratedReport.formats)
        query = self.build_query().options(
            selectinload(formats_attr)
        ).filter(GeneratedReport.id == report_id)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()


class ReportFormatRepository(BaseRepository[ReportFormat]):
    """Repository for managing report format files."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(ReportFormat, session)
    
    async def create_format(
        self,
        report_id: UUID,
        format_type: str,
        mime_type: str,
        content: bytes,
        file_path: str | None = None,
        **kwargs: Any
    ) -> ReportFormat:
        """Create a new report format record."""
        format_data = {
            'report_id': report_id,
            'format_type': format_type,
            'mime_type': mime_type,
            'file_path': file_path,
            **kwargs
        }

        report_format = await self.create(**format_data)
        report_format.set_content(content)

        update_data: dict[str, Any] = {}
        if report_format.content_text:
            update_data["content_text"] = report_format.content_text
        if report_format.content_binary:
            update_data["content_binary"] = report_format.content_binary
        await self.update(report_format.id, update_data)
        return report_format
    
    async def get_by_report_and_format(
        self,
        report_id: UUID,
        format_type: str
    ) -> ReportFormat | None:
        """Get specific format for a report."""
        query = self.build_query().filter(
            and_(
                ReportFormat.report_id == report_id,
                ReportFormat.format_type == format_type
            )
        )
        
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
    
    async def get_formats_for_report(self, report_id: UUID) -> list[ReportFormat]:
        """Get all formats for a report."""
        query = self.build_query().filter(ReportFormat.report_id == report_id)
        query = query.order_by(ReportFormat.format_type)

        result = await self.session.execute(query)
        return list(result.scalars().all())
    
    async def verify_format_integrity(self, format_id: UUID) -> bool:
        """Verify the integrity of a format file."""
        format_obj = await self.get(format_id)
        if not format_obj:
            return False
        
        return format_obj.verify_integrity()
    
    async def cleanup_orphaned_formats(self, dry_run: bool = True) -> tuple[int, list[str]]:
        """Clean up format records without associated reports."""
        from sqlalchemy import select

        # Find formats where the report no longer exists
        query = select(ReportFormat).outerjoin(
            GeneratedReport, ReportFormat.report_id == GeneratedReport.id
        ).where(GeneratedReport.id.is_(None))

        result = await self.session.execute(query)
        orphaned_formats = list(result.scalars().all())

        deleted_ids: list[str] = []
        deleted_count = 0
        
        for format_obj in orphaned_formats:
            if not dry_run:
                # Delete file if it exists
                if format_obj.file_path and os.path.exists(format_obj.file_path):
                    try:
                        os.unlink(format_obj.file_path)
                    except Exception as e:
                        logger.error(f"Failed to delete format file {format_obj.file_path}: {e}")
                
                await self.delete(format_obj.id)
            
            deleted_ids.append(str(format_obj.id))
            deleted_count += 1
        
        if not dry_run:
            await self.session.commit()
        
        return deleted_count, deleted_ids


__all__ = [
    "ReportFormatRepository",
    "ReportRepository",
]