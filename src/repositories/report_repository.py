"""
Repository for managing generated reports.

This module provides data access operations for generated reports,
following the repository pattern with functional programming principles.
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select
from structlog import get_logger

from src.models.db.generated_report import GeneratedReport, ReportFormat
from src.repositories.base import BaseRepository

logger = get_logger()


def _normalize_uuid(value: UUID | str, field_name: str) -> UUID:
    """Normalize an identifier before using it in a tenant predicate."""
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field_name}") from exc


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
        organization_id: UUID | str | None = None,
        **kwargs: Any,
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
        if organization_id is None:
            raise ValueError("organization_id is required for report records")

        report_data = {
            "title": title,
            "report_type": report_type,
            "query": query,
            "user_id": user_id,
            "project_id": project_id,
            "organization_id": _normalize_uuid(organization_id, "organization_id"),
            **kwargs,
        }

        return await self.create(**report_data)

    def _scoped_query(
        self,
        *,
        organization_id: UUID | str,
        user_id: UUID | str | None = None,
    ) -> Select[tuple[GeneratedReport]]:
        """Build a report query with mandatory tenant isolation."""
        organization_uuid = _normalize_uuid(organization_id, "organization_id")
        query = select(GeneratedReport).where(
            GeneratedReport.organization_id == organization_uuid,
            GeneratedReport.deleted_at.is_(None),
        )
        if user_id is not None:
            query = query.where(
                GeneratedReport.user_id == _normalize_uuid(user_id, "user_id")
            )
        return query

    async def _get_scoped_report(
        self,
        report_id: UUID | str,
        *,
        organization_id: UUID | str,
        user_id: UUID | str | None = None,
    ) -> GeneratedReport | None:
        """Return a report only when it belongs to the supplied tenant scope."""
        query = self._scoped_query(
            organization_id=organization_id,
            user_id=user_id,
        ).where(GeneratedReport.id == _normalize_uuid(report_id, "report_id"))
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_workflow_id(
        self,
        workflow_id: str,
        *,
        organization_id: UUID | str,
        user_id: UUID | str | None = None,
    ) -> GeneratedReport | None:
        """Get report by workflow ID."""
        query = (
            self._scoped_query(
                organization_id=organization_id,
                user_id=user_id,
            )
            .where(GeneratedReport.workflow_id == workflow_id)
            .limit(1)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_project_id(
        self,
        project_id: UUID,
        limit: int | None = None,
        *,
        organization_id: UUID | str,
        user_id: UUID | str | None = None,
    ) -> list[GeneratedReport]:
        """Get all reports for a project."""
        query = self._scoped_query(
            organization_id=organization_id,
            user_id=user_id,
        ).where(GeneratedReport.project_id == project_id)
        query = query.order_by(desc(GeneratedReport.created_at))

        if limit:
            query = query.limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_by_user_id(
        self,
        user_id: UUID,
        limit: int | None = None,
        status_filter: str | None = None,
        *,
        organization_id: UUID | str,
    ) -> list[GeneratedReport]:
        """Get all reports for a user."""
        query = self._scoped_query(
            organization_id=organization_id,
            user_id=user_id,
        )

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
        offset: int = 0,
        *,
        organization_id: UUID | str,
    ) -> list[GeneratedReport]:
        """Get public reports."""
        query = self._scoped_query(organization_id=organization_id).where(
            and_(
                GeneratedReport.is_public.is_(True),
                GeneratedReport.generation_status == "completed",
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
        offset: int = 0,
        *,
        organization_id: UUID | str,
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
            GeneratedReport.content_preview.ilike(f"%{search_term}%"),
        )

        # Build base query
        query = self._scoped_query(
            organization_id=organization_id,
            user_id=user_id,
        ).where(search_filter)

        # Add additional filters
        if report_type:
            query = query.where(GeneratedReport.report_type == report_type)

        if min_quality_score is not None:
            query = query.where(GeneratedReport.quality_score >= min_quality_score)

        # Only include completed reports
        query = query.where(GeneratedReport.generation_status == "completed")

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
        limit: int | None = None,
        *,
        organization_id: UUID | str,
        user_id: UUID | str | None = None,
    ) -> list[GeneratedReport]:
        """Get reports by generation status."""
        query = self._scoped_query(
            organization_id=organization_id,
            user_id=user_id,
        ).where(GeneratedReport.generation_status == status)
        query = query.order_by(desc(GeneratedReport.created_at))

        if limit:
            query = query.limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_pending_reports(
        self,
        limit: int = 10,
        *,
        organization_id: UUID | str,
        user_id: UUID | str | None = None,
    ) -> list[GeneratedReport]:
        """Get reports pending generation."""
        return await self.get_reports_by_status(
            "pending",
            limit,
            organization_id=organization_id,
            user_id=user_id,
        )

    async def get_failed_reports(
        self,
        since: datetime | None = None,
        limit: int = 50,
        *,
        organization_id: UUID | str,
        user_id: UUID | str | None = None,
    ) -> list[GeneratedReport]:
        """Get failed reports, optionally filtered by date."""
        query = self._scoped_query(
            organization_id=organization_id,
            user_id=user_id,
        ).where(GeneratedReport.generation_status == "failed")

        if since:
            query = query.filter(GeneratedReport.created_at >= since)

        query = query.order_by(desc(GeneratedReport.created_at))
        query = query.limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_report_statistics(
        self,
        user_id: UUID | None = None,
        days: int = 30,
        *,
        organization_id: UUID | str,
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

        organization_uuid = _normalize_uuid(organization_id, "organization_id")
        base_filter = and_(
            GeneratedReport.organization_id == organization_uuid,
            GeneratedReport.deleted_at.is_(None),
            GeneratedReport.created_at >= since_date,
        )
        user_filter = (
            GeneratedReport.user_id == _normalize_uuid(user_id, "user_id")
            if user_id
            else None
        )

        # Total reports
        total_query = select(func.count(GeneratedReport.id)).where(base_filter)
        if user_filter is not None:
            total_query = total_query.where(user_filter)
        total_result = await self.session.execute(total_query)
        total_reports = total_result.scalar() or 0

        # Reports by status
        status_query = (
            select(GeneratedReport.generation_status, func.count(GeneratedReport.id))
            .where(base_filter)
            .group_by(GeneratedReport.generation_status)
        )
        if user_filter is not None:
            status_query = status_query.where(user_filter)
        status_result = await self.session.execute(status_query)
        status_counts: dict[str, int] = {row[0]: row[1] for row in status_result.all()}

        # Reports by type
        type_query = (
            select(GeneratedReport.report_type, func.count(GeneratedReport.id))
            .where(and_(base_filter, GeneratedReport.generation_status == "completed"))
            .group_by(GeneratedReport.report_type)
        )
        if user_filter is not None:
            type_query = type_query.where(user_filter)
        type_result = await self.session.execute(type_query)
        type_counts: dict[str, int] = {row[0]: row[1] for row in type_result.all()}

        # Average metrics for completed reports
        metrics_query = select(
            func.avg(GeneratedReport.quality_score),
            func.avg(GeneratedReport.confidence_score),
            func.avg(GeneratedReport.generation_time_seconds),
            func.avg(GeneratedReport.word_count),
            func.sum(GeneratedReport.access_count),
        ).where(and_(base_filter, GeneratedReport.generation_status == "completed"))
        if user_filter is not None:
            metrics_query = metrics_query.where(user_filter)

        metrics_result = await self.session.execute(metrics_query)
        row = metrics_result.one()
        avg_quality = row[0]
        avg_confidence = row[1]
        avg_time = row[2]
        avg_words = row[3]
        total_access = row[4]

        return {
            "total_reports": total_reports,
            "status_counts": status_counts,
            "type_counts": type_counts,
            "average_quality_score": float(avg_quality) if avg_quality else 0.0,
            "average_confidence_score": float(avg_confidence)
            if avg_confidence
            else 0.0,
            "average_generation_time": float(avg_time) if avg_time else 0.0,
            "average_word_count": int(avg_words) if avg_words else 0,
            "total_access_count": int(total_access) if total_access else 0,
            "period_days": days,
        }

    async def update_report_status(
        self,
        report_id: UUID,
        status: str,
        error_message: str | None = None,
        *,
        organization_id: UUID | str,
        user_id: UUID | str | None = None,
    ) -> GeneratedReport | None:
        """Update report generation status."""
        report = await self._get_scoped_report(
            report_id,
            organization_id=organization_id,
            user_id=user_id,
        )
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

        await self.session.flush()
        await self.session.refresh(report)
        return report

    async def mark_report_completed(
        self,
        report_id: UUID,
        formats: list[str],
        file_sizes: dict[str, int],
        generation_time: float | None = None,
        storage_path: str | None = None,
        *,
        organization_id: UUID | str,
        user_id: UUID | str | None = None,
    ) -> GeneratedReport | None:
        """Mark report as completed with generation details."""
        report = await self._get_scoped_report(
            report_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        if not report:
            return None

        report.mark_generation_completed(formats, file_sizes, generation_time)

        update_data: dict[str, Any] = {
            "generation_status": report.generation_status,
            "generation_completed_at": report.generation_completed_at,
        }
        if storage_path:
            report.storage_path = storage_path
            update_data["storage_path"] = storage_path

        for key, value in update_data.items():
            setattr(report, key, value)
        await self.session.flush()
        await self.session.refresh(report)
        return report

    async def increment_access_count(
        self,
        report_id: UUID,
        *,
        organization_id: UUID | str,
        user_id: UUID | str | None = None,
    ) -> GeneratedReport | None:
        """Increment access count for a report."""
        report = await self._get_scoped_report(
            report_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        if not report:
            return None

        report.update_access_stats()
        await self.session.flush()
        await self.session.refresh(report)
        return report

    async def cleanup_old_reports(
        self,
        days_old: int = 90,
        keep_public: bool = True,
        dry_run: bool = True,
        *,
        organization_id: UUID | str,
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

        query = self._scoped_query(organization_id=organization_id).where(
            GeneratedReport.created_at < cutoff_date
        )

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
                        logger.error(
                            f"Failed to delete files for report {report.id}: {e}"
                        )

                # Delete database record
                await self.delete_report(
                    report.id,
                    organization_id=organization_id,
                )

            deleted_ids.append(str(report.id))
            deleted_count += 1

        if not dry_run:
            await self.session.commit()

        return deleted_count, deleted_ids

    async def get_report_with_formats(
        self,
        report_id: UUID,
        *,
        organization_id: UUID | str,
        user_id: UUID | str | None = None,
    ) -> GeneratedReport | None:
        """Get report with all format files loaded."""
        formats_attr = cast(Any, GeneratedReport.formats)
        query = (
            self._scoped_query(
                organization_id=organization_id,
                user_id=user_id,
            )
            .options(selectinload(formats_attr))
            .where(GeneratedReport.id == _normalize_uuid(report_id, "report_id"))
        )

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def update_report(
        self,
        report_id: UUID,
        data: dict[str, Any],
        *,
        organization_id: UUID | str,
        user_id: UUID | str | None = None,
    ) -> GeneratedReport | None:
        """Update a report only within its tenant and optional user scope."""
        report = await self._get_scoped_report(
            report_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        if not report:
            return None

        for key, value in data.items():
            if hasattr(report, key) and key not in {"id", "created_at"}:
                setattr(report, key, value)

        await self.session.flush()
        await self.session.refresh(report)
        return report

    async def delete_report(
        self,
        report_id: UUID,
        *,
        organization_id: UUID | str,
        user_id: UUID | str | None = None,
        deleted_by: str | None = None,
    ) -> bool:
        """Hard-delete a report only within its tenant and optional user scope."""
        report = await self._get_scoped_report(
            report_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        if not report:
            return False

        if deleted_by:
            report.updated_by = deleted_by
        await self.session.delete(report)
        await self.session.flush()
        return True


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
        *,
        organization_id: UUID | str,
        user_id: UUID | str | None = None,
        **kwargs: Any,
    ) -> ReportFormat:
        """Create a new report format record."""
        organization_uuid = _normalize_uuid(organization_id, "organization_id")
        report_uuid = _normalize_uuid(report_id, "report_id")
        report_query = select(GeneratedReport.id).where(
            GeneratedReport.id == report_uuid,
            GeneratedReport.organization_id == organization_uuid,
            GeneratedReport.deleted_at.is_(None),
        )
        if user_id is not None:
            report_query = report_query.where(
                GeneratedReport.user_id == _normalize_uuid(user_id, "user_id")
            )
        report_result = await self.session.execute(report_query)
        if report_result.scalar_one_or_none() is None:
            raise ValueError("Report does not belong to the requested tenant")

        format_data = {
            "report_id": report_uuid,
            "format_type": format_type,
            "mime_type": mime_type,
            "file_path": file_path,
            **kwargs,
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
        format_type: str,
        *,
        organization_id: UUID | str,
        user_id: UUID | str | None = None,
    ) -> ReportFormat | None:
        """Get specific format for a report."""
        query = (
            select(ReportFormat)
            .join(GeneratedReport, ReportFormat.report_id == GeneratedReport.id)
            .where(
                and_(
                    ReportFormat.report_id == _normalize_uuid(report_id, "report_id"),
                    ReportFormat.format_type == format_type,
                    ReportFormat.deleted_at.is_(None),
                    GeneratedReport.organization_id
                    == _normalize_uuid(organization_id, "organization_id"),
                    GeneratedReport.deleted_at.is_(None),
                )
            )
        )
        if user_id is not None:
            query = query.where(
                GeneratedReport.user_id == _normalize_uuid(user_id, "user_id")
            )

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get_formats_for_report(
        self,
        report_id: UUID,
        *,
        organization_id: UUID | str,
        user_id: UUID | str | None = None,
    ) -> list[ReportFormat]:
        """Get all formats for a report."""
        query = (
            select(ReportFormat)
            .join(GeneratedReport, ReportFormat.report_id == GeneratedReport.id)
            .where(
                ReportFormat.report_id == _normalize_uuid(report_id, "report_id"),
                ReportFormat.deleted_at.is_(None),
                GeneratedReport.organization_id
                == _normalize_uuid(organization_id, "organization_id"),
                GeneratedReport.deleted_at.is_(None),
            )
        )
        if user_id is not None:
            query = query.where(
                GeneratedReport.user_id == _normalize_uuid(user_id, "user_id")
            )
        query = query.order_by(ReportFormat.format_type)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def verify_format_integrity(
        self,
        format_id: UUID,
        *,
        organization_id: UUID | str,
        user_id: UUID | str | None = None,
    ) -> bool:
        """Verify the integrity of a format file."""
        query = (
            select(ReportFormat)
            .join(GeneratedReport, ReportFormat.report_id == GeneratedReport.id)
            .where(
                ReportFormat.id == _normalize_uuid(format_id, "format_id"),
                ReportFormat.deleted_at.is_(None),
                GeneratedReport.organization_id
                == _normalize_uuid(organization_id, "organization_id"),
                GeneratedReport.deleted_at.is_(None),
            )
        )
        if user_id is not None:
            query = query.where(
                GeneratedReport.user_id == _normalize_uuid(user_id, "user_id")
            )
        result = await self.session.execute(query)
        format_obj = result.scalar_one_or_none()
        if format_obj is None:
            return False

        return format_obj.verify_integrity()

    async def cleanup_orphaned_formats(
        self, dry_run: bool = True
    ) -> tuple[int, list[str]]:
        """Clean up format records without associated reports."""
        from sqlalchemy import select

        # Find formats where the report no longer exists
        query = (
            select(ReportFormat)
            .outerjoin(GeneratedReport, ReportFormat.report_id == GeneratedReport.id)
            .where(GeneratedReport.id.is_(None))
        )

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
                        logger.error(
                            f"Failed to delete format file {format_obj.file_path}: {e}"
                        )

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
