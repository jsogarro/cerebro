"""
Research API endpoints for Research Platform.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from src.api.services.direct_execution_service import get_direct_execution_service
from src.middleware.tenant_context import TenantContext, get_tenant_context
from src.models.db.research_project import ProjectStatus
from src.models.db.session import get_session
from src.models.research_project import (
    ResearchProgress,
    ResearchProject,
    ResearchQuery,
    ResearchScope,
    ResearchStatus,
)
from src.repositories.research_repository import ResearchRepository

logger = get_logger()
router = APIRouter(prefix="/research")


async def get_research_repo(
    session: AsyncSession = Depends(get_session),
) -> ResearchRepository:
    """Get research repository dependency."""
    return ResearchRepository(session)


from pydantic import BaseModel  # noqa: E402


class CreateResearchProjectRequest(BaseModel):
    """Request model for creating a research project."""

    title: str
    query: ResearchQuery
    user_id: str
    scope: ResearchScope | None = None


@router.post(
    "/projects", response_model=ResearchProject, status_code=http_status.HTTP_201_CREATED
)
async def create_research_project(
    request: CreateResearchProjectRequest,
    repo: ResearchRepository = Depends(get_research_repo),
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> ResearchProject:
    """Create a new research project."""
    try:
        import json as _json

        if request.user_id != tenant_context.user_id:
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="Cannot create a project for another user",
            )

        # Create research project in database
        db_project = await repo.create(
            title=request.title,
            query=_json.dumps(request.query.model_dump()),
            user_id=tenant_context.user_id,
            organization_id=tenant_context.organization_id,
            domains=request.scope.domains if request.scope else [],
            status=ProjectStatus.DRAFT,
        )

        query_data = _json.loads(db_project.query) if isinstance(db_project.query, str) else db_project.query
        project = ResearchProject(
            id=db_project.id,
            title=db_project.title,
            query=ResearchQuery(**query_data),
            user_id=db_project.user_id,
            scope=request.scope,
            status=ResearchStatus.PENDING,
            created_at=db_project.created_at,
            updated_at=db_project.updated_at,
        )

        # Start direct execution via MASR
        try:
            execution_service = get_direct_execution_service()
            execution_id = await execution_service.start_research_execution(project)
            await repo.update_status(
                db_project.id,
                ProjectStatus.IN_PROGRESS,
                organization_id=tenant_context.organization_id,
            )
            project.status = ResearchStatus.IN_PROGRESS
            logger.info(
                "Started direct execution for research project",
                project_id=str(project.id),
                execution_id=execution_id,
            )
        except Exception as exec_error:
            logger.warning(
                "Failed to start execution, project created but not started",
                project_id=str(project.id),
                error=str(exec_error),
            )

        logger.info(
            "Created research project",
            project_id=str(project.id),
            title=request.title,
            user_id=tenant_context.user_id,
            organization_id=tenant_context.organization_id,
        )

        return project
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to create research project", exc_info=e)
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create research project",
        ) from e


@router.get("/projects/{project_id}", response_model=ResearchProject)
async def get_research_project(
    project_id: UUID,
    repo: ResearchRepository = Depends(get_research_repo),
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> ResearchProject:
    """Get a research project by ID."""
    # Fetch from database
    db_project = await repo.get_for_user(
        project_id,
        user_id=tenant_context.user_id,
        organization_id=tenant_context.organization_id,
    )

    if not db_project:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Research project {project_id} not found",
        )

    import json as _json

    query_data = _json.loads(db_project.query) if isinstance(db_project.query, str) else db_project.query
    project = ResearchProject(
        id=db_project.id,
        title=db_project.title,
        query=ResearchQuery(**query_data),
        user_id=db_project.user_id,
        status=ResearchStatus(db_project.status.value),
        created_at=db_project.created_at,
        updated_at=db_project.updated_at,
    )

    # Update status from direct execution
    try:
        execution_service = get_direct_execution_service()
        # Find execution for this project (simplified for demo)
        for execution in execution_service.active_executions.values():
            if execution.project_id == str(project_id):
                if execution.status == "completed":
                    project.status = ResearchStatus.COMPLETED
                    await repo.update_status(
                        project_id,
                        ProjectStatus.COMPLETED,
                        organization_id=tenant_context.organization_id,
                    )
                elif execution.status == "failed":
                    project.status = ResearchStatus.FAILED
                    await repo.update_status(
                        project_id,
                        ProjectStatus.FAILED,
                        organization_id=tenant_context.organization_id,
                    )
                elif execution.status in ["running", "pending"]:
                    project.status = ResearchStatus.IN_PROGRESS
                break
    except Exception as e:
        logger.warning(f"Could not get execution status: {e}")

    return project


@router.get("/projects", response_model=list[ResearchProject])
async def list_research_projects(
    user_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    repo: ResearchRepository = Depends(get_research_repo),
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> list[ResearchProject]:
    """List research projects with filtering."""
    if user_id and user_id != tenant_context.user_id:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Cannot list projects for another user",
        )

    # Fetch from database with filters
    effective_user_id = user_id or tenant_context.user_id
    if effective_user_id:
        db_projects = await repo.get_by_user(
            effective_user_id,
            status=ProjectStatus(status) if status else None,
            limit=limit,
            offset=offset,
            organization_id=tenant_context.organization_id,
        )

    # Convert to API models
    import json as _json

    def _parse_query(q: Any) -> dict[str, Any]:
        result: dict[str, Any] = _json.loads(q) if isinstance(q, str) else q
        return result

    return [
        ResearchProject(
            id=p.id,
            title=p.title,
            query=ResearchQuery(**_parse_query(p.query)),
            user_id=p.user_id,
            status=ResearchStatus(p.status.value),
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        for p in db_projects
    ]


@router.get("/projects/{project_id}/progress", response_model=ResearchProgress)
async def get_research_progress(
    project_id: UUID,
    repo: ResearchRepository = Depends(get_research_repo),
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> ResearchProgress:
    """Get real-time progress of a research project."""
    # Check project exists
    db_project = await repo.get_for_user(
        project_id,
        user_id=tenant_context.user_id,
        organization_id=tenant_context.organization_id,
    )
    if not db_project:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Research project {project_id} not found",
        )

    # Fetch progress from direct execution service
    try:
        execution_service = get_direct_execution_service()
        
        # Find execution for this project
        execution_status = None
        for execution in execution_service.active_executions.values():
            if execution.project_id == str(project_id):
                execution_status = execution
                break
        
        if execution_status:
            # Calculate progress from execution status
            total_tasks = 4  # Typical research workflow phases
            completed_tasks = int(execution_status.progress_percentage / 25)  # 25% per phase
            in_progress = 1 if execution_status.status == "running" else 0
            pending_tasks = max(0, total_tasks - completed_tasks - in_progress)
            
            progress = ResearchProgress(
                project_id=project_id,
                total_tasks=total_tasks,
                completed_tasks=completed_tasks,
                in_progress_tasks=in_progress,
                pending_tasks=pending_tasks,
                progress_percentage=execution_status.progress_percentage,
                current_agent=execution_status.current_phase,
            )
        else:
            # No active execution found - use DB status
            is_completed = db_project.status == ProjectStatus.COMPLETED
            progress = ResearchProgress(
                project_id=project_id,
                total_tasks=1,
                completed_tasks=1 if is_completed else 0,
                in_progress_tasks=0,
                pending_tasks=0,
                progress_percentage=100.0 if is_completed else 0.0,
                current_agent="completed" if is_completed else "pending",
            )
        
        return progress
        
    except Exception as e:
        logger.error(f"Failed to get progress: {e}")
        # Return default progress
        return ResearchProgress(
            project_id=project_id,
            total_tasks=1,
            completed_tasks=0,
            in_progress_tasks=0,
            pending_tasks=1,
            progress_percentage=0.0,
        )


@router.post("/projects/{project_id}/cancel", status_code=http_status.HTTP_204_NO_CONTENT)
async def cancel_research_project(
    project_id: UUID,
    repo: ResearchRepository = Depends(get_research_repo),
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> None:
    """Cancel a research project."""
    # Check project exists
    db_project = await repo.get_for_user(
        project_id,
        user_id=tenant_context.user_id,
        organization_id=tenant_context.organization_id,
    )
    if not db_project:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Research project {project_id} not found",
        )

    # Cancel direct execution
    execution_service = get_direct_execution_service()

    # Find and cancel execution for this project
    success = False
    for execution in execution_service.active_executions.values():
        if execution.project_id == str(project_id):
            success = await execution_service.cancel_execution(execution.execution_id)
            break

    if success:
        # Update project status in database
        await repo.update_status(
            project_id,
            ProjectStatus.CANCELLED,
            organization_id=tenant_context.organization_id,
        )
        logger.info("Cancelled research project", project_id=str(project_id))
    else:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to cancel research project",
        )


@router.post("/projects/{project_id}/refine")
async def refine_research_scope(
    project_id: UUID,
    scope: ResearchScope,
) -> ResearchProject:
    """Refine the scope of an existing research project."""
    # TODO: Update project scope
    # TODO: Adjust Temporal workflow

    raise HTTPException(
        status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
        detail="Scope refinement not yet implemented",
    )


@router.get("/projects/{project_id}/results")
async def get_research_results(
    project_id: UUID,
    repo: ResearchRepository = Depends(get_research_repo),
    tenant_context: TenantContext = Depends(get_tenant_context),
) -> dict[str, Any]:
    """Get the results of a completed research project."""
    # Check project exists
    db_project = await repo.get_for_user(
        project_id,
        user_id=tenant_context.user_id,
        organization_id=tenant_context.organization_id,
        load_relationships=["results", "agent_tasks", "checkpoints"],
    )
    if not db_project:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Research project {project_id} not found",
        )

    # Check DB results first
    if db_project.results:
        return {"project_id": str(project_id), "results": db_project.results}

    # Fall back to in-memory execution results
    try:
        execution_service = get_direct_execution_service()
        for execution in execution_service.active_executions.values():
            if execution.project_id == str(project_id) and execution.status == "completed":
                return {
                    "project_id": str(project_id),
                    "results": execution.agent_results or execution.final_output or {},
                    "quality_scores": execution.quality_scores,
                    "execution_time_seconds": execution.execution_time_seconds,
                }
    except Exception as e:
        logger.warning(f"Could not get execution results: {e}")

    raise HTTPException(
        status_code=http_status.HTTP_404_NOT_FOUND,
        detail=f"Results for project {project_id} not available yet",
    )
