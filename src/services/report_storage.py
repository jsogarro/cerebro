"""
Report storage service for managing generated report files.

This service handles the storage and retrieval of generated reports,
following functional programming principles with pure data operations.
"""

import hashlib
import logging
import os
import shutil
from datetime import datetime
from typing import Any
from uuid import UUID

from src.models.db.generated_report import GeneratedReport, ReportFormat
from src.models.report import Report, ReportOutput
from src.models.report import ReportFormat as ReportFormatEnum
from src.repositories.report_repository import ReportFormatRepository, ReportRepository
from src.services.report_config import ReportSettings

logger = logging.getLogger(__name__)


class ReportStorageError(Exception):
    """Exception raised during report storage operations."""
    pass


class ReportStorageService:
    """Service for managing report storage and retrieval."""
    
    def __init__(
        self,
        report_repo: ReportRepository,
        format_repo: ReportFormatRepository,
        settings: ReportSettings | None = None
    ):
        """Initialize report storage service."""
        self.report_repo = report_repo
        self.format_repo = format_repo
        self.settings = settings or ReportSettings()
        
        # Ensure storage directory exists
        self._ensure_storage_directory()
    
    def _ensure_storage_directory(self) -> None:
        """Ensure the storage directory exists."""
        try:
            os.makedirs(self.settings.report_storage_path, exist_ok=True)
        except OSError as e:
            logger.error(f"Failed to create storage directory: {e}")
            raise ReportStorageError(f"Storage setup failed: {e}")
    
    async def store_report(
        self,
        report: Report,
        outputs: dict[ReportFormatEnum, ReportOutput],
        user_id: UUID | None = None,
        project_id: UUID | None = None
    ) -> GeneratedReport:
        """
        Store a generated report with all its format outputs.
        
        Args:
            report: Generated report object
            outputs: Dictionary of format outputs
            user_id: Optional user ID
            project_id: Optional project ID
            
        Returns:
            Stored GeneratedReport database record
        """
        try:
            logger.info(f"Storing report: {report.id}")
            
            # Create report directory
            report_dir = self._create_report_directory(report.id)
            
            # Create database record
            db_report = await self._create_report_record(
                report, user_id, project_id, report_dir
            )
            
            # Store format files
            file_sizes = {}
            formats_generated = []
            
            for format_enum, output in outputs.items():
                try:
                    await self._store_format_file(db_report.id, format_enum, output, report_dir)
                    file_sizes[format_enum.value] = len(output.content) if isinstance(output.content, (str, bytes)) else 0
                    formats_generated.append(format_enum.value)
                except Exception as e:
                    logger.error(f"Failed to store format {format_enum}: {e}")
                    # Continue with other formats
            
            # Update report with completion details
            await self.report_repo.mark_report_completed(
                db_report.id,
                formats_generated,
                file_sizes,
                storage_path=report_dir
            )
            
            logger.info(f"Report {report.id} stored successfully with {len(formats_generated)} formats")
            return db_report
            
        except Exception as e:
            logger.error(f"Failed to store report {report.id}: {e}")
            raise ReportStorageError(f"Storage failed: {e}")
    
    def _create_report_directory(self, report_id: str) -> str:
        """Create directory for storing report files."""
        report_dir = os.path.join(self.settings.report_storage_path, report_id)
        os.makedirs(report_dir, exist_ok=True)
        return report_dir
    
    async def _create_report_record(
        self,
        report: Report,
        user_id: UUID | None,
        project_id: UUID | None,
        storage_path: str
    ) -> GeneratedReport:
        """Create database record for the report."""
        return await self.report_repo.create_report(
            title=report.title,
            report_type=report.configuration.type.value,
            query=report.query,
            user_id=user_id,
            project_id=project_id,
            workflow_id=report.metadata.workflow_id,
            domains=report.domains,
            configuration=report.configuration.dict(),
            quality_score=report.metadata.quality_score,
            confidence_score=report.metadata.confidence_score,
            total_sources=report.metadata.total_sources,
            total_citations=report.metadata.total_citations,
            word_count=report.metadata.word_count,
            page_count=report.metadata.page_count,
            agents_used=report.metadata.agents_used,
            storage_path=storage_path,
            executive_summary=report.executive_summary,
            content_preview=self._extract_content_preview(report),
            key_findings=self._extract_key_findings(report),
            generation_status="completed",
            generation_time_seconds=report.metadata.generation_time_seconds,
        )
    
    async def _store_format_file(
        self,
        report_id: UUID,
        format_enum: ReportFormatEnum,
        output: ReportOutput,
        report_dir: str
    ) -> ReportFormat:
        """Store a single format file."""
        format_type = format_enum.value
        
        # Determine file extension
        extensions = {
            'html': '.html',
            'pdf': '.pdf',
            'latex': '.tex',
            'docx': '.docx',
            'markdown': '.md',
            'json': '.json'
        }
        extension = extensions.get(format_type, f'.{format_type}')
        
        # Create file path
        file_path = os.path.join(report_dir, f"report{extension}")
        
        # Write file
        content_bytes = self._prepare_content_for_storage(output.content, output.encoding)
        
        with open(file_path, 'wb') as f:
            f.write(content_bytes)
        
        # Calculate file hash
        file_hash = hashlib.sha256(content_bytes).hexdigest()
        
        # Create database record
        return await self.format_repo.create_format(
            report_id=report_id,
            format_type=format_type,
            mime_type=output.mime_type,
            content=content_bytes,
            file_path=file_path,
            file_extension=extension,
            encoding=output.encoding,
            file_size=len(content_bytes),
            file_hash=file_hash,
            generation_time_ms=0,  # Would be populated if we tracked per-format timing
        )
    
    def _prepare_content_for_storage(self, content: Any, encoding: str) -> bytes:
        """Prepare content for file storage."""
        if isinstance(content, bytes):
            return content
        elif isinstance(content, str):
            return content.encode(encoding if encoding != "binary" else "utf-8")
        else:
            # Convert other types to string first
            return str(content).encode(encoding if encoding != "binary" else "utf-8")
    
    def _extract_content_preview(self, report: Report, max_length: int = 500) -> str:
        """Extract content preview from report sections."""
        preview_parts = []
        current_length = 0
        
        # Start with executive summary if available
        if report.executive_summary:
            summary_preview = report.executive_summary[:max_length]
            if len(summary_preview) >= max_length:
                return summary_preview + "..."
            preview_parts.append(summary_preview)
            current_length += len(summary_preview)
        
        # Add content from sections
        for section in report.sections:
            if current_length >= max_length:
                break
            
            remaining = max_length - current_length
            section_preview = section.content[:remaining]
            preview_parts.append(section_preview)
            current_length += len(section_preview)
        
        preview = " ".join(preview_parts)
        return preview[:max_length] + ("..." if len(preview) > max_length else "")
    
    def _extract_key_findings(self, report: Report) -> list[str]:
        """Extract key findings from report."""
        findings = []
        
        # Look for findings in sections
        for section in report.sections:
            if "finding" in section.title.lower() or "key" in section.title.lower():
                # Extract bullet points or numbered items
                lines = section.content.split('\n')
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith(('•', '-', '*', '1.', '2.', '3.', '4.', '5.')):
                        # Clean up the finding text
                        finding = stripped.lstrip('•-*123456789. ')
                        if finding and len(finding) > 10:  # Ignore very short items
                            findings.append(finding[:200])  # Limit finding length
                        
                        if len(findings) >= 10:  # Limit number of findings
                            break
            
            if len(findings) >= 10:
                break
        
        return findings
    
    async def retrieve_report(self, report_id: UUID) -> GeneratedReport | None:
        """Retrieve a report with all format information."""
        return await self.report_repo.get_report_with_formats(report_id)
    
    async def retrieve_report_content(
        self,
        report_id: UUID,
        format_type: str
    ) -> tuple[bytes, str] | None:
        """
        Retrieve content for a specific report format.
        
        Args:
            report_id: Report ID
            format_type: Format type (html, pdf, etc.)
            
        Returns:
            Tuple of (content_bytes, mime_type) or None if not found
        """
        report_format = await self.format_repo.get_by_report_and_format(
            report_id, format_type
        )
        
        if not report_format:
            return None
        
        try:
            content = report_format.get_content()
            return content, report_format.mime_type
        except Exception as e:
            logger.error(f"Failed to retrieve content for {report_id}/{format_type}: {e}")
            return None
    
    async def list_user_reports(
        self,
        user_id: UUID,
        limit: int = 50,
        status_filter: str | None = None
    ) -> list[GeneratedReport]:
        """List reports for a user."""
        return await self.report_repo.get_by_user_id(user_id, limit, status_filter)
    
    async def list_project_reports(
        self,
        project_id: UUID,
        limit: int | None = None
    ) -> list[GeneratedReport]:
        """List reports for a project."""
        return await self.report_repo.get_by_project_id(project_id, limit)
    
    async def search_reports(
        self,
        search_term: str,
        user_id: UUID | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 50,
        offset: int = 0
    ) -> tuple[list[GeneratedReport], int]:
        """Search reports with various filters."""
        filters = filters or {}
        
        return await self.report_repo.search_reports(
            search_term=search_term,
            user_id=user_id,
            report_type=filters.get('report_type'),
            min_quality_score=filters.get('min_quality_score'),
            limit=limit,
            offset=offset
        )
    
    async def delete_report(
        self,
        report_id: UUID,
        delete_files: bool = True
    ) -> bool:
        """
        Delete a report and optionally its files.
        
        Args:
            report_id: Report ID to delete
            delete_files: Whether to delete associated files
            
        Returns:
            True if deletion successful
        """
        try:
            report = await self.report_repo.get(report_id)
            if not report:
                return False
            
            # Delete files if requested
            if delete_files and report.storage_path:
                if os.path.exists(report.storage_path):
                    shutil.rmtree(report.storage_path)
                    logger.info(f"Deleted files for report {report_id}")
            
            # Delete database records (formats will be cascade deleted)
            await self.report_repo.delete(report_id)
            
            logger.info(f"Deleted report {report_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete report {report_id}: {e}")
            raise ReportStorageError(f"Deletion failed: {e}")
    
    async def update_report_access(self, report_id: UUID) -> None:
        """Update access statistics for a report."""
        await self.report_repo.increment_access_count(report_id)
    
    async def get_storage_statistics(self) -> dict[str, Any]:
        """Get storage usage statistics."""
        stats = await self.report_repo.get_report_statistics()
        
        # Add file system statistics
        try:
            storage_usage = self._calculate_storage_usage()
            stats.update(storage_usage)
        except Exception as e:
            logger.error(f"Failed to calculate storage usage: {e}")
            stats.update({
                'total_storage_bytes': 0,
                'total_storage_mb': 0,
                'storage_error': str(e)
            })
        
        return stats
    
    def _calculate_storage_usage(self) -> dict[str, Any]:
        """Calculate total storage usage."""
        total_size = 0
        total_files = 0
        
        if os.path.exists(self.settings.report_storage_path):
            for root, dirs, files in os.walk(self.settings.report_storage_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    try:
                        size = os.path.getsize(file_path)
                        total_size += size
                        total_files += 1
                    except OSError:
                        continue
        
        return {
            'total_storage_bytes': total_size,
            'total_storage_mb': round(total_size / (1024 * 1024), 2),
            'total_storage_gb': round(total_size / (1024 * 1024 * 1024), 3),
            'total_files': total_files,
            'storage_path': self.settings.report_storage_path,
        }
    
    async def cleanup_old_reports(
        self,
        days_old: int = 90,
        keep_public: bool = True,
        dry_run: bool = True
    ) -> dict[str, Any]:
        """
        Clean up old reports and return statistics.
        
        Args:
            days_old: Delete reports older than this many days
            keep_public: Whether to keep public reports
            dry_run: If True, don't actually delete anything
            
        Returns:
            Cleanup statistics
        """
        deleted_count, deleted_ids = await self.report_repo.cleanup_old_reports(
            days_old, keep_public, dry_run
        )
        
        # Also cleanup orphaned format files
        format_deleted_count, format_deleted_ids = await self.format_repo.cleanup_orphaned_formats(dry_run)
        
        return {
            'reports_deleted': deleted_count,
            'report_ids_deleted': deleted_ids,
            'format_files_deleted': format_deleted_count,
            'format_ids_deleted': format_deleted_ids,
            'dry_run': dry_run,
            'cleanup_date': datetime.utcnow().isoformat(),
        }
    
    async def verify_report_integrity(self, report_id: UUID) -> dict[str, Any]:
        """Verify the integrity of a report and its files."""
        report = await self.retrieve_report(report_id)
        if not report:
            return {'status': 'error', 'message': 'Report not found'}
        
        integrity_results: dict[str, Any] = {
            'report_id': str(report_id),
            'status': 'ok',
            'formats_checked': 0,
            'formats_valid': 0,
            'formats_invalid': [],
            'missing_files': [],
            'errors': []
        }
        
        # Check each format
        for format_obj in report.formats:
            integrity_results['formats_checked'] += 1
            
            try:
                # Check if file exists
                if format_obj.file_path and not os.path.exists(format_obj.file_path):
                    integrity_results['missing_files'].append(format_obj.format_type)
                    continue
                
                # Verify hash if available
                if format_obj.file_hash:
                    is_valid = await self.format_repo.verify_format_integrity(format_obj.id)
                    if is_valid:
                        integrity_results['formats_valid'] += 1
                    else:
                        integrity_results['formats_invalid'].append(format_obj.format_type)
                else:
                    # No hash to verify, assume valid if file exists
                    integrity_results['formats_valid'] += 1
                    
            except Exception as e:
                integrity_results['errors'].append(f"{format_obj.format_type}: {e!s}")
        
        # Determine overall status
        if integrity_results['missing_files'] or integrity_results['formats_invalid'] or integrity_results['errors']:
            integrity_results['status'] = 'degraded'
        
        if integrity_results['formats_valid'] == 0:
            integrity_results['status'] = 'failed'
        
        return integrity_results


def create_report_storage_service(
    report_repo: ReportRepository,
    format_repo: ReportFormatRepository,
    settings: ReportSettings | None = None
) -> ReportStorageService:
    """Factory function to create a report storage service."""
    return ReportStorageService(report_repo, format_repo, settings)


__all__ = [
    "ReportStorageError",
    "ReportStorageService",
    "create_report_storage_service",
]