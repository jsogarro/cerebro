"""
Repository for managing generated reports.

This module provides data access operations for generated reports,
following the repository pattern with functional programming principles.
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import and_, desc, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.db.generated_report import GeneratedReport, ReportFormat
from src.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class ReportRepository(BaseRepository[GeneratedReport]):
    """Repository for managing generated reports."""
    
    def __init__(self, session: AsyncSession):
        super().__init__(session, GeneratedReport)
    
    async def create_report(
        self,
        title: str,
        report_type: str,
        query: str,
        user_id: Optional[UUID] = None,
        project_id: Optional[UUID] = None,
        **kwargs
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
    
    async def get_by_workflow_id(self, workflow_id: str) -> Optional[GeneratedReport]:
        """Get report by workflow ID."""
        return await self.get_by_field("workflow_id", workflow_id)
    
    async def get_by_project_id(
        self,
        project_id: UUID,
        limit: Optional[int] = None
    ) -> List[GeneratedReport]:
        """Get all reports for a project."""
        query = self._build_query().filter(GeneratedReport.project_id == project_id)
        query = query.order_by(desc(GeneratedReport.created_at))
        
        if limit:
            query = query.limit(limit)
        
        result = await self.session.execute(query)
        return result.scalars().all()
    
    async def get_by_user_id(
        self,
        user_id: UUID,
        limit: Optional[int] = None,
        status_filter: Optional[str] = None
    ) -> List[GeneratedReport]:
        """Get all reports for a user."""
        query = self._build_query().filter(GeneratedReport.user_id == user_id)
        
        if status_filter:
            query = query.filter(GeneratedReport.generation_status == status_filter)
        
        query = query.order_by(desc(GeneratedReport.created_at))
        
        if limit:
            query = query.limit(limit)
        
        result = await self.session.execute(query)
        return result.scalars().all()
    
    async def get_public_reports(
        self,
        limit: int = 50,
        offset: int = 0
    ) -> List[GeneratedReport]:
        """Get public reports."""
        query = self._build_query().filter(
            and_(
                GeneratedReport.is_public.is_(True),
                GeneratedReport.generation_status == "completed"
            )
        )
        query = query.order_by(desc(GeneratedReport.created_at))
        query = query.offset(offset).limit(limit)
        
        result = await self.session.execute(query)
        return result.scalars().all()
    
    async def search_reports(
        self,
        search_term: str,
        user_id: Optional[UUID] = None,
        report_type: Optional[str] = None,
        min_quality_score: Optional[float] = None,
        limit: int = 50,
        offset: int = 0
    ) -> Tuple[List[GeneratedReport], int]:
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
        query = self._build_query().filter(search_filter)
        
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
        total_count = count_result.scalar()
        
        # Get results with pagination
        query = query.order_by(desc(GeneratedReport.created_at))
        query = query.offset(offset).limit(limit)
        
        result = await self.session.execute(query)
        reports = result.scalars().all()
        
        return reports, total_count
    
    async def get_reports_by_status(
        self,
        status: str,
        limit: Optional[int] = None
    ) -> List[GeneratedReport]:
        """Get reports by generation status."""
        query = self._build_query().filter(GeneratedReport.generation_status == status)
        query = query.order_by(desc(GeneratedReport.created_at))
        
        if limit:
            query = query.limit(limit)
        
        result = await self.session.execute(query)
        return result.scalars().all()
    
    async def get_pending_reports(self, limit: int = 10) -> List[GeneratedReport]:
        """Get reports pending generation."""
        return await self.get_reports_by_status("pending", limit)
    
    async def get_failed_reports(
        self,
        since: Optional[datetime] = None,
        limit: int = 50
    ) -> List[GeneratedReport]:
        """Get failed reports, optionally filtered by date."""
        query = self._build_query().filter(GeneratedReport.generation_status == "failed")
        
        if since:
            query = query.filter(GeneratedReport.created_at >= since)
        
        query = query.order_by(desc(GeneratedReport.created_at))
        query = query.limit(limit)
        
        result = await self.session.execute(query)
        return result.scalars().all()
    
    async def get_report_statistics(
        self,
        user_id: Optional[UUID] = None,
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Get report generation statistics.
        
        Args:
            user_id: Optional user ID to filter by
            days: Number of days to look back
            
        Returns:
            Dictionary with statistics
        """
        since_date = datetime.utcnow() - timedelta(days=days)
        
        base_query = self.session.query(GeneratedReport).filter(
            GeneratedReport.created_at >= since_date
        )
        
        if user_id:
            base_query = base_query.filter(GeneratedReport.user_id == user_id)
        
        # Total reports
        total_result = await self.session.execute(
            base_query.with_only_columns(func.count(GeneratedReport.id))
        )
        total_reports = total_result.scalar()
        
        # Reports by status
        status_query = base_query.with_only_columns(
            GeneratedReport.generation_status,
            func.count(GeneratedReport.id)
        ).group_by(GeneratedReport.generation_status)
        
        status_result = await self.session.execute(status_query)
        status_counts = dict(status_result.all())
        
        # Reports by type
        type_query = base_query.filter(
            GeneratedReport.generation_status == "completed"
        ).with_only_columns(
            GeneratedReport.report_type,
            func.count(GeneratedReport.id)
        ).group_by(GeneratedReport.report_type)
        
        type_result = await self.session.execute(type_query)
        type_counts = dict(type_result.all())
        
        # Average metrics for completed reports
        metrics_query = base_query.filter(
            GeneratedReport.generation_status == "completed"
        ).with_only_columns(
            func.avg(GeneratedReport.quality_score),
            func.avg(GeneratedReport.confidence_score),
            func.avg(GeneratedReport.generation_time_seconds),
            func.avg(GeneratedReport.word_count),
            func.sum(GeneratedReport.access_count)
        )
        
        metrics_result = await self.session.execute(metrics_query)
        avg_quality, avg_confidence, avg_time, avg_words, total_access = metrics_result.one()
        
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
        error_message: Optional[str] = None
    ) -> Optional[GeneratedReport]:
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
                report.generation_completed_at = datetime.utcnow()
        
        await self.update(report)
        return report
    
    async def mark_report_completed(
        self,
        report_id: UUID,
        formats: List[str],
        file_sizes: Dict[str, int],
        generation_time: Optional[float] = None,
        storage_path: Optional[str] = None
    ) -> Optional[GeneratedReport]:
        """Mark report as completed with generation details."""
        report = await self.get(report_id)
        if not report:
            return None
        
        report.mark_generation_completed(formats, file_sizes, generation_time)
        
        if storage_path:
            report.storage_path = storage_path
        
        await self.update(report)
        return report
    
    async def increment_access_count(self, report_id: UUID) -> Optional[GeneratedReport]:
        """Increment access count for a report."""
        report = await self.get(report_id)
        if not report:
            return None
        
        report.update_access_stats()
        await self.update(report)
        return report
    
    async def cleanup_old_reports(
        self,
        days_old: int = 90,
        keep_public: bool = True,
        dry_run: bool = True
    ) -> Tuple[int, List[str]]:
        """
        Clean up old reports and their files.
        
        Args:
            days_old: Delete reports older than this many days
            keep_public: Whether to keep public reports
            dry_run: If True, don't actually delete anything
            
        Returns:
            Tuple of (deleted_count, deleted_ids)
        """
        cutoff_date = datetime.utcnow() - timedelta(days=days_old)
        
        query = self._build_query().filter(GeneratedReport.created_at < cutoff_date)
        
        if keep_public:
            query = query.filter(GeneratedReport.is_public.is_(False))
        
        result = await self.session.execute(query)
        old_reports = result.scalars().all()
        
        deleted_ids = []
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
    
    async def get_report_with_formats(self, report_id: UUID) -> Optional[GeneratedReport]:
        """Get report with all format files loaded."""
        query = self._build_query().options(
            selectinload(GeneratedReport.formats)
        ).filter(GeneratedReport.id == report_id)
        
        result = await self.session.execute(query)
        return result.scalar_one_or_none()


class ReportFormatRepository(BaseRepository[ReportFormat]):
    """Repository for managing report format files."""
    
    def __init__(self, session: AsyncSession):
        super().__init__(session, ReportFormat)
    
    async def create_format(
        self,
        report_id: UUID,
        format_type: str,
        mime_type: str,
        content: bytes,
        file_path: Optional[str] = None,
        **kwargs
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
        
        await self.update(report_format)
        return report_format
    
    async def get_by_report_and_format(
        self,
        report_id: UUID,
        format_type: str
    ) -> Optional[ReportFormat]:
        """Get specific format for a report."""
        query = self._build_query().filter(
            and_(
                ReportFormat.report_id == report_id,
                ReportFormat.format_type == format_type
            )
        )
        
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
    
    async def get_formats_for_report(self, report_id: UUID) -> List[ReportFormat]:
        """Get all formats for a report."""
        query = self._build_query().filter(ReportFormat.report_id == report_id)
        query = query.order_by(ReportFormat.format_type)
        
        result = await self.session.execute(query)
        return result.scalars().all()
    
    async def verify_format_integrity(self, format_id: UUID) -> bool:
        """Verify the integrity of a format file."""
        format_obj = await self.get(format_id)
        if not format_obj:
            return False
        
        return format_obj.verify_integrity()
    
    async def cleanup_orphaned_formats(self, dry_run: bool = True) -> Tuple[int, List[str]]:
        """Clean up format records without associated reports."""
        # Find formats where the report no longer exists
        query = self.session.query(ReportFormat).outerjoin(GeneratedReport).filter(
            GeneratedReport.id.is_(None)
        )
        
        result = await self.session.execute(query)
        orphaned_formats = result.scalars().all()
        
        deleted_ids = []
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
    "ReportRepository",
    "ReportFormatRepository",
]