"""
Reports API endpoints for Research Platform.

This module provides REST API endpoints for report generation, retrieval,
and management, following functional programming principles.
"""

import io
import time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.middleware.tenant_context import TenantContext, get_tenant_context
from src.models.db.generated_report import GeneratedReport
from src.models.db.research_project import ResearchProject
from src.models.db.session import get_session, get_session_factory
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
    ReportStorageError,
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
    generation_time_seconds: float | None = None
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


def _tenant_ids(tenant_context: TenantContext) -> tuple[UUID, UUID]:
    """Return validated tenant identifiers, failing closed for bad claims."""
    try:
        return UUID(tenant_context.user_id), UUID(tenant_context.organization_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authenticated tenant identifiers are invalid",
        ) from exc


def _validate_requested_user(
    requested_user_id: UUID | None, authenticated_user_id: UUID
) -> None:
    """Reject a caller-supplied user filter that differs from the auth subject."""
    if requested_user_id is not None and requested_user_id != authenticated_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reports may only be accessed for the authenticated user",
        )


async def _validate_project_access(
    session: AsyncSession,
    project_id: UUID | None,
    *,
    user_id: UUID,
    organization_id: UUID,
) -> UUID | None:
    """Validate project ownership before using a caller-provided project ID."""
    if project_id is None:
        return None

    query = select(ResearchProject.id).where(
        ResearchProject.id == project_id,
        ResearchProject.user_id == user_id,
        ResearchProject.organization_id == organization_id,
        ResearchProject.deleted_at.is_(None),
    )
    result = await session.execute(query)
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )
    return project_id


def _trusted_workflow_data(
    request: CreateReportRequest, project_id: UUID | None
) -> dict[str, Any]:
    """Build generation input without allowing nested caller IDs to override auth."""
    workflow_data = dict(request.workflow_data)
    workflow_data.update(
        {
            "title": request.title,
            "query": request.query,
            "domains": request.domains,
        }
    )
    if project_id is None:
        workflow_data.pop("project_id", None)
    else:
        workflow_data["project_id"] = str(project_id)
    return workflow_data


def get_report_services(
    session: AsyncSession,
) -> tuple[
    ReportGenerator, ReportStorageService, ReportRepository, ReportFormatRepository
]:
    """Create report services backed by the request's live database session."""
    settings = create_report_settings()
    generator = ReportGenerator(settings)
    report_repo = ReportRepository(session)
    format_repo = ReportFormatRepository(session)
    storage_service = create_report_storage_service(report_repo, format_repo, settings)
    return generator, storage_service, report_repo, format_repo


# API Endpoints


@router.post(
    "/generate", response_model=ReportResponse, status_code=status.HTTP_202_ACCEPTED
)
async def generate_report(
    request: CreateReportRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> ReportResponse:
    """
    Generate a new report asynchronously.

    This endpoint accepts a report generation request and returns immediately
    with a report ID. The actual generation happens in the background.
    """
    try:
        authenticated_user_id, organization_id = _tenant_ids(tenant_context)
        _validate_requested_user(request.user_id, authenticated_user_id)
        trusted_project_id = await _validate_project_access(
            session,
            request.project_id,
            user_id=authenticated_user_id,
            organization_id=organization_id,
        )
        generator, _storage_service, report_repo, _format_repo = get_report_services(
            session
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
            max_sections=None,
            custom_css=None,
            template_name=None,
            author_name=None,
            institution=None,
        )

        gen_request = ReportGenerationRequest(
            project_id=trusted_project_id,
            workflow_data=_trusted_workflow_data(request, trusted_project_id),
            configuration=config,
            formats=request.formats,
            save_to_storage=request.save_to_storage,
            notify_completion=request.notify_completion,
        )

        # Persist the request before queueing work. The response ID and timestamp
        # therefore identify a real row that the worker can update.
        db_report = await report_repo.create_report(
            title=request.title,
            report_type=request.report_type.value,
            query=request.query,
            user_id=authenticated_user_id,
            project_id=trusted_project_id,
            organization_id=organization_id,
            domains=request.domains,
            configuration=config.model_dump(mode="json"),
            formats_generated=[],
            generation_status="pending",
            created_by=str(authenticated_user_id),
        )
        reloaded_report = await report_repo.get_report_with_formats(
            db_report.id,
            organization_id=organization_id,
            user_id=authenticated_user_id,
        )
        if reloaded_report is None:
            raise RuntimeError("Persisted report could not be reloaded")
        db_report = reloaded_report

        # Add background task for generation
        background_tasks.add_task(
            _generate_report_task,
            generator,
            db_report.id,
            gen_request,
            authenticated_user_id,
            trusted_project_id,
            organization_id,
        )

        response = ReportResponse.from_db_report(db_report)

        logger.info(
            "Report generation requested",
            title=request.title,
            report_id=str(db_report.id),
            user_id=str(authenticated_user_id),
            formats=len(request.formats),
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to start report generation", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start report generation",
        ) from e


@router.get("/statistics", response_model=ReportStatisticsResponse)
async def get_report_statistics(
    user_id: UUID | None = Query(None, description="Filter by user ID"),
    days: int = Query(30, ge=1, le=365, description="Days to look back"),
    session: AsyncSession = Depends(get_session),
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> ReportStatisticsResponse:
    """Get report generation statistics."""
    try:
        authenticated_user_id, organization_id = _tenant_ids(tenant_context)
        _validate_requested_user(user_id, authenticated_user_id)
        _generator, storage_service, _report_repo, _format_repo = get_report_services(
            session
        )

        if not storage_service:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Report storage service not available",
            )

        # Get statistics from storage service
        stats = await storage_service.get_storage_statistics(
            organization_id=organization_id,
            user_id=authenticated_user_id,
            days=days,
        )

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

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get report statistics", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get statistics",
        ) from e


@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(
    report_id: UUID,
    session: AsyncSession = Depends(get_session),
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> ReportResponse:
    """Get report details by ID."""
    try:
        authenticated_user_id, organization_id = _tenant_ids(tenant_context)
        _generator, storage_service, _report_repo, _format_repo = get_report_services(
            session
        )

        if not storage_service:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Report storage service not available",
            )

        report = await storage_service.retrieve_report(
            report_id,
            organization_id=organization_id,
            user_id=authenticated_user_id,
        )

        if not report:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Report {report_id} not found",
            )

        # Update access statistics
        await storage_service.update_report_access(
            report_id,
            organization_id=organization_id,
            user_id=authenticated_user_id,
        )

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
async def download_report(
    report_id: UUID,
    format_type: str,
    session: AsyncSession = Depends(get_session),
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> StreamingResponse:
    """Download report in specific format."""
    try:
        authenticated_user_id, organization_id = _tenant_ids(tenant_context)
        _generator, storage_service, _report_repo, _format_repo = get_report_services(
            session
        )

        if not storage_service:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Report storage service not available",
            )

        # Retrieve report content
        content_result = await storage_service.retrieve_report_content(
            report_id,
            format_type,
            organization_id=organization_id,
            user_id=authenticated_user_id,
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
        await storage_service.update_report_access(
            report_id,
            organization_id=organization_id,
            user_id=authenticated_user_id,
        )

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
    session: AsyncSession = Depends(get_session),
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> ReportListResponse:
    """List reports with filtering and pagination."""
    try:
        authenticated_user_id, organization_id = _tenant_ids(tenant_context)
        _validate_requested_user(user_id, authenticated_user_id)
        _generator, storage_service, _report_repo, _format_repo = get_report_services(
            session
        )

        if not storage_service:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Report storage service not available",
            )

        reports = await storage_service.list_user_reports(
            authenticated_user_id,
            limit=page_size + 1,
            status_filter=status_filter,
            organization_id=organization_id,
        )

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

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to list reports", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list reports",
        ) from e


@router.post("/search", response_model=ReportListResponse)
async def search_reports(
    request: ReportSearchRequest,
    session: AsyncSession = Depends(get_session),
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> ReportListResponse:
    """Search reports by text and filters."""
    try:
        authenticated_user_id, organization_id = _tenant_ids(tenant_context)
        _validate_requested_user(request.user_id, authenticated_user_id)
        _generator, storage_service, _report_repo, _format_repo = get_report_services(
            session
        )

        if not storage_service:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Report storage service not available",
            )

        # Build search filters
        filters: dict[str, str | int | float] = {}
        if request.report_type:
            filters["report_type"] = str(request.report_type)
        if request.min_quality_score is not None:
            filters["min_quality_score"] = request.min_quality_score

        # Perform search
        reports, total_count = await storage_service.search_reports(
            search_term=request.search_term,
            user_id=authenticated_user_id,
            filters=filters,
            limit=request.limit,
            offset=request.offset,
            organization_id=organization_id,
        )

        report_responses = [ReportResponse.from_db_report(report) for report in reports]

        page = int((request.offset // request.limit) + 1)
        has_more = request.offset + len(reports) < total_count

        return ReportListResponse(
            reports=report_responses,
            total_count=total_count,
            page=page,
            page_size=request.limit,
            has_more=has_more,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to search reports", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to search reports",
        ) from e


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    report_id: UUID,
    delete_files: bool = Query(True, description="Also delete associated files"),
    session: AsyncSession = Depends(get_session),
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> None:
    """Delete a report and optionally its files."""
    try:
        authenticated_user_id, organization_id = _tenant_ids(tenant_context)
        _generator, storage_service, _report_repo, _format_repo = get_report_services(
            session
        )

        if not storage_service:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Report storage service not available",
            )

        success = await storage_service.delete_report(
            report_id,
            delete_files,
            organization_id=organization_id,
            user_id=authenticated_user_id,
            deleted_by=str(authenticated_user_id),
        )

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
async def verify_report_integrity(
    report_id: UUID,
    session: AsyncSession = Depends(get_session),
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    """Verify the integrity of a report and its files."""
    try:
        authenticated_user_id, organization_id = _tenant_ids(tenant_context)
        _generator, storage_service, _report_repo, _format_repo = get_report_services(
            session
        )

        if not storage_service:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Report storage service not available",
            )

        integrity_result = await storage_service.verify_report_integrity(
            report_id,
            organization_id=organization_id,
            user_id=authenticated_user_id,
        )

        if not isinstance(integrity_result, dict):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Invalid response from storage service",
            )

        return integrity_result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to verify report integrity {report_id}", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify report integrity",
        ) from e


# Background task functions


async def _generate_report_task(
    generator: ReportGenerator,
    report_id: UUID,
    request: ReportGenerationRequest,
    user_id: UUID,
    project_id: UUID | None,
    organization_id: UUID,
) -> None:
    """Generate and finalize the exact pending report row returned to the caller."""
    session_factory = get_session_factory()

    async with session_factory() as session:
        _generator, storage_service, report_repo, _format_repo = get_report_services(
            session
        )
        try:
            report_record = await report_repo.update_report_status(
                report_id,
                "generating",
                organization_id=organization_id,
                user_id=user_id,
            )
            if report_record is None:
                raise ReportStorageError(
                    f"Pending report {report_id} was not found in the requested tenant"
                )

            started_at = time.perf_counter()
            input_data = await generator._extract_input_data(request)
            report = await generator._build_report_structure(
                input_data,
                request.configuration,
                str(report_id),
            )
            outputs = await generator._generate_formats(report, request.formats)
            generation_time = time.perf_counter() - started_at
            report.metadata.generation_time_seconds = generation_time
            report.get_word_count()
            report.estimate_page_count()

            if request.save_to_storage:
                await storage_service.store_report(
                    report,
                    outputs,
                    user_id=user_id,
                    project_id=project_id,
                    organization_id=organization_id,
                    existing_report_id=report_id,
                )
            else:
                metadata = storage_service._report_metadata(report, "")
                metadata.pop("storage_path", None)
                await report_repo.update_report(
                    report_id,
                    metadata,
                    organization_id=organization_id,
                    user_id=user_id,
                )
                await report_repo.mark_report_completed(
                    report_id,
                    [],
                    {},
                    generation_time=generation_time,
                    organization_id=organization_id,
                    user_id=user_id,
                )

            await session.commit()
            logger.info(
                "Report generation completed",
                report_id=str(report_id),
                formats=len(outputs),
                generation_time=generation_time,
            )
        except Exception as exc:
            await session.rollback()
            try:
                await report_repo.update_report_status(
                    report_id,
                    "failed",
                    str(exc),
                    organization_id=organization_id,
                    user_id=user_id,
                )
                await session.commit()
            except Exception:
                await session.rollback()
                logger.error(
                    "Failed to persist report generation failure",
                    report_id=str(report_id),
                    exc_info=True,
                )
            logger.error(
                "Report generation task failed",
                report_id=str(report_id),
                exc_info=exc,
            )
