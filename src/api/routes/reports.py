"""
Reports API endpoints for Research Platform.

This module provides REST API endpoints for report generation, retrieval,
and management, following functional programming principles.
"""

import io
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from structlog import get_logger

from src.models.db.generated_report import GeneratedReport
from src.models.report import (
    CitationStyle,
    ReportConfiguration,
    ReportFormat,
    ReportGenerationRequest,
    ReportType,
)
from src.repositories.report_repository import ReportFormatRepository, ReportRepository
from src.services.report_config import create_report_settings
from src.services.report_generator import ReportGenerator
from src.services.report_storage import (
    ReportStorageService,
    create_report_storage_service,
)

logger = get_logger()
router = APIRouter(prefix="/reports")


# Request/Response Models


class CreateReportRequest(BaseModel):
    """Request model for creating a report."""

    title: str = Field(..., min_length=1, max_length=200, description="Report title")
    query: str = Field(
        ..., min_length=1, max_length=1000, description="Research question"
    )
    domains: list[str] = Field(default_factory=list, description="Research domains")
    project_id: UUID | None = Field(None, description="Associated project ID")
    user_id: UUID | None = Field(None, description="User ID")

    # Report configuration
    report_type: ReportType = Field(
        default=ReportType.COMPREHENSIVE, description="Type of report"
    )
    citation_style: CitationStyle = Field(
        default=CitationStyle.APA, description="Citation style"
    )
    formats: list[ReportFormat] = Field(
        default=[ReportFormat.HTML, ReportFormat.MARKDOWN], description="Output formats"
    )

    # Optional configuration
    include_toc: bool = Field(default=True, description="Include table of contents")
    include_executive_summary: bool = Field(
        default=True, description="Include executive summary"
    )
    include_visualizations: bool = Field(
        default=True, description="Include visualizations"
    )
    include_citations: bool = Field(default=True, description="Include citations")
    include_methodology: bool = Field(
        default=True, description="Include methodology section"
    )

    # Workflow data
    workflow_data: dict[str, Any] = Field(
        default_factory=dict, description="Research workflow data"
    )

    # Settings
    save_to_storage: bool = Field(default=True, description="Save report to storage")
    notify_completion: bool = Field(
        default=False, description="Send notification on completion"
    )


class ReportResponse(BaseModel):
    """Response model for report operations."""

    id: UUID
    title: str
    query: str
    report_type: str
    generation_status: str
    formats_generated: list[str]
    word_count: int
    page_count: int
    quality_score: float
    confidence_score: float
    created_at: str
    generation_time_seconds: float | None
    download_urls: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_db_report(
        cls, report: GeneratedReport, base_url: str = ""
    ) -> "ReportResponse":
        """Create response from database report."""
        download_urls = {}
        for format_obj in report.formats:
            download_urls[format_obj.format_type] = (
                f"{base_url}/reports/{report.id}/download/{format_obj.format_type}"
            )

        return cls(
            id=report.id,
            title=report.title,
            query=report.query,
            report_type=report.report_type,
            generation_status=report.generation_status,
            formats_generated=[f.format_type for f in report.formats],
            word_count=report.word_count or 0,
            page_count=report.page_count or 0,
            quality_score=report.quality_score or 0.0,
            confidence_score=report.confidence_score or 0.0,
            created_at=report.created_at.isoformat(),
            generation_time_seconds=report.generation_time_seconds,
            download_urls=download_urls,
        )


class ReportListResponse(BaseModel):
    """Response model for report listing."""

    reports: list[ReportResponse]
    total_count: int
    page: int
    page_size: int
    has_more: bool


class ReportSearchRequest(BaseModel):
    """Request model for report search."""

    search_term: str = Field(..., min_length=1, description="Search term")
    user_id: UUID | None = Field(None, description="Filter by user ID")
    report_type: str | None = Field(None, description="Filter by report type")
    min_quality_score: float | None = Field(
        None, ge=0.0, le=1.0, description="Minimum quality score"
    )
    limit: int = Field(default=20, ge=1, le=100, description="Maximum results")
    offset: int = Field(default=0, ge=0, description="Results offset")


class ReportStatisticsResponse(BaseModel):
    """Response model for report statistics."""

    total_reports: int
    status_counts: dict[str, int]
    type_counts: dict[str, int]
    average_quality_score: float
    average_confidence_score: float
    average_generation_time: float
    average_word_count: int
    total_access_count: int
    storage_statistics: dict[str, Any] = Field(default_factory=dict)


# Dependency functions


def get_report_services():
    """Get report services (would be dependency injection in real app)."""
    # In a real application, these would be injected dependencies

    # For now, we'll simulate the services
    settings = create_report_settings()
    generator = ReportGenerator(settings)

    # These would be properly injected
    session = None  # get_session()
    report_repo = ReportRepository(session) if session else None
    format_repo = ReportFormatRepository(session) if session else None
    storage_service = (
        create_report_storage_service(report_repo, format_repo, settings)
        if report_repo and format_repo
        else None
    )

    return generator, storage_service, report_repo, format_repo


# API Endpoints


@router.post(
    "/generate", response_model=ReportResponse, status_code=status.HTTP_202_ACCEPTED
)
async def generate_report(
    request: CreateReportRequest, background_tasks: BackgroundTasks
) -> ReportResponse:
    """
    Generate a new report asynchronously.

    This endpoint accepts a report generation request and returns immediately
    with a report ID. The actual generation happens in the background.
    """
    try:
        generator, storage_service, report_repo, format_repo = get_report_services()

        if not generator:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Report generation service not available",
            )

        # Build report configuration
        config = ReportConfiguration(
            format=request.formats[0] if request.formats else ReportFormat.HTML,
            type=request.report_type,
            citation_style=request.citation_style,
            include_toc=request.include_toc,
            include_executive_summary=request.include_executive_summary,
            include_visualizations=request.include_visualizations,
            include_citations=request.include_citations,
            include_methodology=request.include_methodology,
        )

        # Create generation request
        gen_request = ReportGenerationRequest(
            workflow_data={
                "title": request.title,
                "query": request.query,
                "domains": request.domains,
                "project_id": str(request.project_id) if request.project_id else None,
                **request.workflow_data,
            },
            configuration=config,
            formats=request.formats,
            save_to_storage=request.save_to_storage,
            notify_completion=request.notify_completion,
        )

        # Add background task for generation
        background_tasks.add_task(
            _generate_report_task,
            generator,
            storage_service,
            gen_request,
            request.user_id,
            request.project_id,
        )

        # Create placeholder response (in real app, would create DB record first)
        response = ReportResponse(
            id=UUID("00000000-0000-0000-0000-000000000000"),  # Would be real ID
            title=request.title,
            query=request.query,
            report_type=request.report_type.value,
            generation_status="generating",
            formats_generated=[],
            word_count=0,
            page_count=0,
            quality_score=0.0,
            confidence_score=0.0,
            created_at="2024-01-01T00:00:00Z",  # Would be real timestamp
            generation_time_seconds=None,
        )

        logger.info(
            "Report generation requested",
            title=request.title,
            user_id=str(request.user_id) if request.user_id else None,
            formats=len(request.formats),
        )

        return response

    except Exception as e:
        logger.error("Failed to start report generation", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start report generation",
        ) from e


@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(report_id: UUID) -> ReportResponse:
    """Get report details by ID."""
    try:
        generator, storage_service, report_repo, format_repo = get_report_services()

        if not storage_service:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Report storage service not available",
            )

        report = await storage_service.retrieve_report(report_id)

        if not report:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Report {report_id} not found",
            )

        # Update access statistics
        await storage_service.update_report_access(report_id)

        return ReportResponse.from_db_report(report)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get report {report_id}", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve report",
        ) from e


@router.get("/{report_id}/download/{format_type}")
async def download_report(report_id: UUID, format_type: str) -> StreamingResponse:
    """Download report in specific format."""
    try:
        generator, storage_service, report_repo, format_repo = get_report_services()

        if not storage_service:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Report storage service not available",
            )

        # Retrieve report content
        content_result = await storage_service.retrieve_report_content(
            report_id, format_type
        )

        if not content_result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Report {report_id} in format {format_type} not found",
            )

        content_bytes, mime_type = content_result

        # Determine filename extension
        extensions = {
            "html": ".html",
            "pdf": ".pdf",
            "latex": ".tex",
            "docx": ".docx",
            "markdown": ".md",
            "json": ".json",
        }
        extension = extensions.get(format_type, f".{format_type}")
        filename = f"report_{report_id}{extension}"

        # Update access statistics
        await storage_service.update_report_access(report_id)

        return StreamingResponse(
            io.BytesIO(content_bytes),
            media_type=mime_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to download report {report_id}/{format_type}", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to download report",
        ) from e


@router.get("", response_model=ReportListResponse)
async def list_reports(
    user_id: UUID | None = Query(None, description="Filter by user ID"),
    status_filter: str | None = Query(None, description="Filter by status"),
    report_type: str | None = Query(None, description="Filter by report type"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
) -> ReportListResponse:
    """List reports with filtering and pagination."""
    try:
        generator, storage_service, report_repo, format_repo = get_report_services()

        if not storage_service:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Report storage service not available",
            )

        if user_id:
            reports = await storage_service.list_user_reports(
                user_id, limit=page_size + 1, status_filter=status_filter
            )
        else:
            # For public listing, you might want different logic
            reports = []  # Would implement general listing

        # Check if there are more results
        has_more = len(reports) > page_size
        if has_more:
            reports = reports[:page_size]

        # Convert to response models
        report_responses = [ReportResponse.from_db_report(report) for report in reports]

        return ReportListResponse(
            reports=report_responses,
            total_count=len(report_responses),  # Would be actual total from DB
            page=page,
            page_size=page_size,
            has_more=has_more,
        )

    except Exception as e:
        logger.error("Failed to list reports", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list reports",
        ) from e


@router.post("/search", response_model=ReportListResponse)
async def search_reports(request: ReportSearchRequest) -> ReportListResponse:
    """Search reports by text and filters."""
    try:
        generator, storage_service, report_repo, format_repo = get_report_services()

        if not storage_service:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Report storage service not available",
            )

        # Build search filters
        filters = {}
        if request.report_type:
            filters["report_type"] = request.report_type
        if request.min_quality_score is not None:
            filters["min_quality_score"] = request.min_quality_score

        # Perform search
        reports, total_count = await storage_service.search_reports(
            search_term=request.search_term,
            user_id=request.user_id,
            filters=filters,
            limit=request.limit,
            offset=request.offset,
        )

        # Convert to response models
        report_responses = [ReportResponse.from_db_report(report) for report in reports]

        page = (request.offset // request.limit) + 1
        has_more = request.offset + len(reports) < total_count

        return ReportListResponse(
            reports=report_responses,
            total_count=total_count,
            page=page,
            page_size=request.limit,
            has_more=has_more,
        )

    except Exception as e:
        logger.error("Failed to search reports", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to search reports",
        ) from e


@router.get("/statistics", response_model=ReportStatisticsResponse)
async def get_report_statistics(
    user_id: UUID | None = Query(None, description="Filter by user ID"),
    days: int = Query(30, ge=1, le=365, description="Days to look back"),
) -> ReportStatisticsResponse:
    """Get report generation statistics."""
    try:
        generator, storage_service, report_repo, format_repo = get_report_services()

        if not storage_service:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Report storage service not available",
            )

        # Get statistics from storage service
        stats = await storage_service.get_storage_statistics()

        return ReportStatisticsResponse(
            total_reports=stats.get("total_reports", 0),
            status_counts=stats.get("status_counts", {}),
            type_counts=stats.get("type_counts", {}),
            average_quality_score=stats.get("average_quality_score", 0.0),
            average_confidence_score=stats.get("average_confidence_score", 0.0),
            average_generation_time=stats.get("average_generation_time", 0.0),
            average_word_count=stats.get("average_word_count", 0),
            total_access_count=stats.get("total_access_count", 0),
            storage_statistics={
                "total_storage_mb": stats.get("total_storage_mb", 0),
                "total_files": stats.get("total_files", 0),
            },
        )

    except Exception as e:
        logger.error("Failed to get report statistics", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get statistics",
        ) from e


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    report_id: UUID,
    delete_files: bool = Query(True, description="Also delete associated files"),
) -> None:
    """Delete a report and optionally its files."""
    try:
        generator, storage_service, report_repo, format_repo = get_report_services()

        if not storage_service:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Report storage service not available",
            )

        success = await storage_service.delete_report(report_id, delete_files)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Report {report_id} not found",
            )

        logger.info(f"Deleted report {report_id}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete report {report_id}", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete report",
        ) from e


@router.get("/{report_id}/integrity")
async def verify_report_integrity(report_id: UUID) -> dict[str, Any]:
    """Verify the integrity of a report and its files."""
    try:
        generator, storage_service, report_repo, format_repo = get_report_services()

        if not storage_service:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Report storage service not available",
            )

        integrity_result = await storage_service.verify_report_integrity(report_id)

        return integrity_result

    except Exception as e:
        logger.error(f"Failed to verify report integrity {report_id}", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify report integrity",
        ) from e


# Background task functions


async def _generate_report_task(
    generator: ReportGenerator,
    storage_service: ReportStorageService | None,
    request: ReportGenerationRequest,
    user_id: UUID | None,
    project_id: UUID | None,
) -> None:
    """Background task for report generation."""
    try:
        logger.info("Starting report generation task")

        # Generate the report
        response = await generator.generate_report(request)

        if response.status == "completed":
            logger.info(
                "Report generation completed",
                report_id=response.report_id,
                formats=len(response.formats_generated),
                generation_time=response.generation_time,
            )
        else:
            logger.error(
                "Report generation failed",
                report_id=response.report_id,
                errors=response.errors,
            )

        # Here you would typically notify the user or update a status
        # For example, send a WebSocket message or email notification

    except Exception as e:
        logger.error("Report generation task failed", exc_info=e)
