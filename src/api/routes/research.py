"""
Research API endpoints for Research Platform.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from structlog import get_logger

from src.api.services.direct_execution_service import get_direct_execution_service
from src.models.research_project import (
    ResearchProgress,
    ResearchProject,
    ResearchQuery,
    ResearchScope,
    ResearchStatus,
)

logger = get_logger()
router = APIRouter(prefix="/research")

# In-memory storage for demo (replace with database)
projects_db: dict[UUID, ResearchProject] = {}


from pydantic import BaseModel


class CreateResearchProjectRequest(BaseModel):
    """Request model for creating a research project."""

    title: str
    query: ResearchQuery
    user_id: str
    scope: ResearchScope | None = None


@router.post(
    "/projects", response_model=ResearchProject, status_code=status.HTTP_201_CREATED
)
async def create_research_project(
    request: CreateResearchProjectRequest,
) -> ResearchProject:
    """Create a new research project."""
    try:
        # Create research project
        project = ResearchProject(
            title=request.title,
            query=request.query,
            user_id=request.user_id,
            scope=request.scope,
        )

        # Store in memory (replace with database)
        projects_db[project.id] = project

        # Start direct execution via MASR
        try:
            execution_service = get_direct_execution_service()
            execution_id = await execution_service.start_research_execution(project)
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
            user_id=request.user_id,
        )

        return project
    except Exception as e:
        logger.error("Failed to create research project", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create research project",
        )


@router.get("/projects/{project_id}", response_model=ResearchProject)
async def get_research_project(project_id: UUID) -> ResearchProject:
    """Get a research project by ID."""
    # Fetch from in-memory storage (replace with database)
    project = projects_db.get(project_id)

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Research project {project_id} not found",
        )

    # Update status from direct execution
    try:
        execution_service = get_direct_execution_service()
        # Find execution for this project (simplified for demo)
        for execution in execution_service.active_executions.values():
            if execution.project_id == str(project_id):
                if execution.status == "completed":
                    project.status = ResearchStatus.COMPLETED
                elif execution.status == "failed":
                    project.status = ResearchStatus.FAILED
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
) -> list[ResearchProject]:
    """List research projects with filtering."""
    # Filter from in-memory storage (replace with database query)
    projects = list(projects_db.values())

    if user_id:
        projects = [p for p in projects if p.user_id == user_id]

    if status:
        projects = [p for p in projects if p.status.value == status]

    # Apply pagination
    start = offset
    end = offset + limit

    return projects[start:end]


@router.get("/projects/{project_id}/progress", response_model=ResearchProgress)
async def get_research_progress(project_id: UUID) -> ResearchProgress:
    """Get real-time progress of a research project."""
    # Check project exists
    if project_id not in projects_db:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
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
            # No active execution found
            progress = ResearchProgress(
                project_id=project_id,
                total_tasks=1,
                completed_tasks=1 if projects_db.get(project_id, ResearchProject()).status == ResearchStatus.COMPLETED else 0,
                in_progress_tasks=0,
                pending_tasks=0,
                progress_percentage=100.0 if projects_db.get(project_id, ResearchProject()).status == ResearchStatus.COMPLETED else 0.0,
                current_agent="completed" if projects_db.get(project_id, ResearchProject()).status == ResearchStatus.COMPLETED else "pending",
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


@router.post("/projects/{project_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_research_project(project_id: UUID) -> None:
    """Cancel a research project."""
    # Check project exists
    project = projects_db.get(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
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
        # Update project status
        project.status = ResearchStatus.CANCELLED
        logger.info("Cancelled research project", project_id=str(project_id))
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Scope refinement not yet implemented",
    )


@router.get("/projects/{project_id}/results")
async def get_research_results(project_id: UUID):
    """Get the results of a completed research project."""
    # Check project exists
    project = projects_db.get(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Research project {project_id} not found",
        )

    # Fetch results from Temporal
    results = await workflow_service.get_results(project_id)

    if not results:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Results for project {project_id} not available yet",
        )

    return results
