"""
Hierarchical Supervisor API Routes

This module implements the REST API endpoints for supervisor coordination,
following the "Talk Structurally, Act Hierarchically" research patterns.
Provides comprehensive supervisor management, worker coordination, and
cross-domain orchestration capabilities.
"""

import json

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from structlog import get_logger

from src.api.services.supervisor_coordination_service import (
    SupervisorCoordinationService,
)
from src.api.services.supervisor_progress_tracker import SupervisorProgressTracker
from src.models.supervisor_api_models import (
    ConflictResolutionRequest,
    ConflictResolutionResponse,
    ExperimentRequest,
    ExperimentResponse,
    MultiSupervisorOrchestrationRequest,
    MultiSupervisorOrchestrationResponse,
    SupervisorComparisonResponse,
    SupervisorExecuteRequest,
    SupervisorExecuteResponse,
    SupervisorHealthResponse,
    SupervisorInfo,
    SupervisorListResponse,
    SupervisorStatsResponse,
    SupervisorType,
    SupervisorWebSocketEvent,
    WorkerAllocationOptimizationRequest,
    WorkerAllocationOptimizationResponse,
    WorkerCoordinationProgressEvent,
    WorkerCoordinationRequest,
    WorkerCoordinationResponse,
    WorkerListResponse,
)

logger = get_logger()

# Create router with prefix and tags
router = APIRouter(
    prefix="/api/v1/supervisors",
    tags=["supervisors", "hierarchical-coordination"],
    responses={
        404: {"description": "Supervisor not found"},
        500: {"description": "Internal server error"}
    }
)

# Initialize service (in production, this would be dependency injected)
supervisor_service = SupervisorCoordinationService()

# Initialize connection manager
connection_manager = SupervisorProgressTracker()


# Primary Endpoints

@router.post("/{supervisor_type}/execute", response_model=SupervisorExecuteResponse)
async def execute_supervisor_task(
    supervisor_type: SupervisorType,
    request: SupervisorExecuteRequest
) -> SupervisorExecuteResponse:
    """
    Execute a task through a specific supervisor with worker coordination.
    
    This endpoint implements the hierarchical supervision pattern where a
    supervisor coordinates multiple specialist workers to complete complex tasks.
    """
    try:
        # Validate supervisor type
        if supervisor_type not in SupervisorType:
            raise HTTPException(
                status_code=404,
                detail=f"Supervisor type '{supervisor_type}' not found"
            )
        
        # Execute through service
        response = await supervisor_service.execute_supervisor_task(
            supervisor_type.value,
            request
        )
        
        # Send WebSocket event for task completion
        event = SupervisorWebSocketEvent(
            event_type="task_completed",
            supervisor_type=supervisor_type,
            data={
                "execution_id": response.execution_id,
                "quality_score": response.quality_score,
                "execution_time_ms": response.execution_time_ms
            },
            priority="medium"
        )
        await connection_manager.send_supervisor_event(supervisor_type.value, event)
        
        return response
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(
            "supervisor_task_execution_failed",
            supervisor_type=supervisor_type.value,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail="Internal error during supervisor execution"
        ) from e


@router.get("", response_model=SupervisorListResponse)
async def list_supervisors() -> SupervisorListResponse:
    """
    List all available supervisors with their current status and capabilities.
    """
    try:
        supervisors = await supervisor_service.get_all_supervisors()
        
        active_count = sum(1 for s in supervisors if s.status == "active")
        available_count = sum(
            1 for s in supervisors 
            if s.status == "active" and s.active_tasks < 5
        )
        
        return SupervisorListResponse(
            supervisors=supervisors,
            total_count=len(supervisors),
            active_count=active_count,
            available_count=available_count
        )
        
    except Exception as e:
        logger.error("supervisor_list_failed", error=str(e))
        raise HTTPException(
            status_code=500,
            detail="Error retrieving supervisor list"
        ) from e


@router.get("/{supervisor_type}", response_model=SupervisorInfo)
async def get_supervisor_info(supervisor_type: SupervisorType) -> SupervisorInfo:
    """
    Get detailed information about a specific supervisor.
    """
    try:
        return await supervisor_service.get_supervisor_info(supervisor_type.value)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.error(
            "supervisor_info_get_failed",
            supervisor_type=supervisor_type.value,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail="Error retrieving supervisor information"
        ) from e


@router.get("/{supervisor_type}/workers", response_model=WorkerListResponse)
async def get_supervisor_workers(supervisor_type: SupervisorType) -> WorkerListResponse:
    """
    Get all workers managed by a specific supervisor.
    """
    try:
        workers = await supervisor_service.get_supervisor_workers(supervisor_type.value)
        
        active_workers = sum(1 for w in workers if w.status != "idle")
        idle_workers = sum(1 for w in workers if w.status == "idle")
        
        return WorkerListResponse(
            supervisor_type=supervisor_type,
            workers=workers,
            total_workers=len(workers),
            active_workers=active_workers,
            idle_workers=idle_workers
        )
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.error(
            "supervisor_workers_get_failed",
            supervisor_type=supervisor_type.value,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail="Error retrieving worker information"
        ) from e


@router.post("/{supervisor_type}/coordinate", response_model=WorkerCoordinationResponse)
async def coordinate_workers(
    supervisor_type: SupervisorType,
    request: WorkerCoordinationRequest
) -> WorkerCoordinationResponse:
    """
    Coordinate specific workers under a supervisor for a task.
    
    This endpoint allows fine-grained control over worker coordination,
    including specification of coordination modes and conflict resolution strategies.
    """
    try:
        response = await supervisor_service.coordinate_workers(
            supervisor_type.value,
            request
        )
        
        # Send WebSocket event for coordination start
        event = WorkerCoordinationProgressEvent(
            coordination_id=response.coordination_id,
            event_type="started",
            progress_percentage=0.0,
            current_phase="initialization",
            workers_active=len(response.workers_assigned),
            estimated_remaining_seconds=response.estimated_completion_time
        )
        await connection_manager.broadcast_event(event.model_dump())
        
        return response
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(
            "supervisor_worker_coordination_failed",
            supervisor_type=supervisor_type.value,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail="Error during worker coordination"
        ) from e


@router.post("/multi/orchestrate", response_model=MultiSupervisorOrchestrationResponse)
async def orchestrate_multi_supervisor(
    request: MultiSupervisorOrchestrationRequest
) -> MultiSupervisorOrchestrationResponse:
    """
    Orchestrate multiple supervisors for complex cross-domain tasks.
    
    This endpoint enables sophisticated multi-supervisor coordination for
    queries that span multiple domains, with optional result synthesis.
    """
    try:
        response = await supervisor_service.orchestrate_multi_supervisor(request)
        
        # Send WebSocket event for orchestration completion
        event = {
            "event_type": "multi_supervisor_orchestration_complete",
            "orchestration_id": response.orchestration_id,
            "supervisors_count": len(response.supervisors_involved),
            "consensus_achieved": response.consensus_achieved,
            "quality_metrics": response.quality_metrics
        }
        await connection_manager.broadcast_event(event)
        
        return response
        
    except Exception as e:
        logger.error("multi_supervisor_orchestration_failed", error=str(e))
        raise HTTPException(
            status_code=500,
            detail="Error during multi-supervisor orchestration"
        ) from e


@router.get("/{supervisor_type}/stats", response_model=SupervisorStatsResponse)
async def get_supervisor_stats(supervisor_type: SupervisorType) -> SupervisorStatsResponse:
    """
    Get performance statistics for a specific supervisor.
    
    Returns comprehensive metrics including success rate, average quality,
    execution times, and worker utilization.
    """
    try:
        return await supervisor_service.get_supervisor_stats(supervisor_type.value)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.error(
            "supervisor_stats_get_failed",
            supervisor_type=supervisor_type.value,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail="Error retrieving supervisor statistics"
        ) from e


@router.get("/{supervisor_type}/health", response_model=SupervisorHealthResponse)
async def get_supervisor_health(supervisor_type: SupervisorType) -> SupervisorHealthResponse:
    """
    Get health status of a specific supervisor.
    
    Provides health metrics and recommendations for supervisor optimization.
    """
    try:
        return await supervisor_service.get_supervisor_health(supervisor_type.value)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.error(
            "supervisor_health_get_failed",
            supervisor_type=supervisor_type.value,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail="Error retrieving supervisor health status"
        ) from e


# Advanced Coordination Endpoints

@router.post("/optimize-allocation", response_model=WorkerAllocationOptimizationResponse)
async def optimize_worker_allocation(
    request: WorkerAllocationOptimizationRequest
) -> WorkerAllocationOptimizationResponse:
    """
    Optimize worker allocation based on task requirements and goals.
    
    Uses intelligent optimization to determine the best worker allocation
    for quality, speed, cost, or balanced objectives.
    """
    try:
        return await supervisor_service.optimize_worker_allocation(request)
    except Exception as e:
        logger.error("supervisor_worker_allocation_optimization_failed", error=str(e))
        raise HTTPException(
            status_code=500,
            detail="Error during allocation optimization"
        ) from e


@router.post("/resolve-conflicts", response_model=ConflictResolutionResponse)
async def resolve_conflicts(
    request: ConflictResolutionRequest
) -> ConflictResolutionResponse:
    """
    Resolve conflicts between worker outputs using specified strategy.
    
    Implements multiple conflict resolution strategies including supervisor override,
    majority vote, quality-based selection, and structured debate.
    """
    try:
        response = await supervisor_service.resolve_conflict(request)
        
        # Send WebSocket event for conflict resolution
        event = WorkerCoordinationProgressEvent(
            coordination_id=request.conflict_id,
            event_type="conflict_detected",
            progress_percentage=50.0,
            current_phase="conflict_resolution",
            workers_active=len(request.worker_outputs),
            details={
                "resolution_strategy": request.resolution_strategy.value,
                "confidence": response.confidence_score
            }
        )
        await connection_manager.broadcast_event(event.model_dump())
        
        return response
        
    except Exception as e:
        logger.error(
            "supervisor_conflict_resolution_failed",
            conflict_id=request.conflict_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail="Error during conflict resolution"
        ) from e


@router.get("/performance/compare", response_model=SupervisorComparisonResponse)
async def compare_supervisor_performance(
    supervisors: list[SupervisorType] = Query(
        ...,
        description="List of supervisor types to compare"
    )
) -> SupervisorComparisonResponse:
    """
    Compare performance metrics across multiple supervisors.
    
    Provides rankings and recommendations for supervisor selection based on
    various performance criteria.
    """
    try:
        supervisor_types = [s.value for s in supervisors]
        return await supervisor_service.compare_supervisor_performance(supervisor_types)
    except Exception as e:
        logger.error(
            "supervisor_performance_comparison_failed",
            supervisor_count=len(supervisors),
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail="Error during performance comparison"
        ) from e


@router.post("/experiment", response_model=ExperimentResponse)
async def run_coordination_experiment(
    request: ExperimentRequest
) -> ExperimentResponse:
    """
    Run experiments to test different coordination strategies.
    
    Enables systematic testing of various supervision strategies to determine
    optimal approaches for specific query types.
    """
    try:
        response = await supervisor_service.run_experiment(request)
        
        # Send WebSocket event for experiment completion
        event = {
            "event_type": "experiment_complete",
            "experiment_id": response.experiment_id,
            "best_strategy": response.best_strategy.value,
            "strategies_tested": len(response.strategies_tested)
        }
        await connection_manager.broadcast_event(event)
        
        return response
        
    except Exception as e:
        logger.error("supervisor_coordination_experiment_failed", error=str(e))
        raise HTTPException(
            status_code=500,
            detail="Error during coordination experiment"
        ) from e


# WebSocket Endpoints

@router.websocket("/coordination/ws")
async def coordination_progress_websocket(
    websocket: WebSocket,
    coordination_id: str | None = Query(None)
) -> None:
    """
    WebSocket endpoint for real-time worker coordination progress updates.

    Streams live updates about worker assignments, task progress,
    conflict detection, and resolution events.
    """
    client_id = f"coordination-{coordination_id or id(websocket)}"

    try:
        await connection_manager.connect(websocket, client_id)

        # Send initial connection confirmation
        await websocket.send_json({
            "event_type": "connection_established",
            "coordination_id": coordination_id
        })

        # Simulate progress updates if coordination_id provided
        if coordination_id:
            # In production, this would fetch real coordination status
            async for event in connection_manager.iter_coordination_progress_events(
                coordination_id
            ):
                await websocket.send_json(event.model_dump())

                if event.progress_percentage == 100:
                    await websocket.send_json({
                        "event_type": "completed",
                        "coordination_id": coordination_id,
                        "message": "Coordination completed successfully"
                    })

        # Keep connection alive
        while True:
            try:
                data = await websocket.receive_json()
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except WebSocketDisconnect:
                break

    except Exception as e:
        logger.error(
            "coordination_websocket_error",
            coordination_id=coordination_id,
            client_id=client_id,
            error=str(e),
        )
    finally:
        connection_manager.disconnect(websocket, client_id)


@router.websocket("/{supervisor_type}/ws")
async def supervisor_websocket(
    websocket: WebSocket,
    supervisor_type: SupervisorType,
    client_id: str | None = Query(None)
) -> None:
    """
    WebSocket endpoint for real-time supervisor updates.
    
    Provides live updates on supervisor status, task assignments,
    worker coordination progress, and performance alerts.
    """
    client_id = client_id or f"anonymous-{id(websocket)}"
    
    try:
        await connection_manager.connect(websocket, client_id)
        
        # Subscribe to supervisor events
        connection_manager.subscribe_supervisor(supervisor_type.value, websocket)
        
        # Send initial status
        supervisor_info = await supervisor_service.get_supervisor_info(supervisor_type.value)
        await websocket.send_json({
            "event_type": "connection_established",
            "supervisor_info": supervisor_info.model_dump()
        })
        
        # Keep connection alive and handle messages
        while True:
            try:
                data = await websocket.receive_json()
                
                # Handle different message types
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                elif data.get("type") == "subscribe":
                    # Handle subscription requests
                    pass
                elif data.get("type") == "get_status":
                    health = await supervisor_service.get_supervisor_health(supervisor_type.value)
                    await websocket.send_json({
                        "event_type": "status_update",
                        "health": health.model_dump()
                    })
                    
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                await websocket.send_json({
                    "error": "Invalid JSON format"
                })
            except Exception as e:
                logger.error(
                    "supervisor_websocket_message_error",
                    supervisor_type=supervisor_type.value,
                    client_id=client_id,
                    error=str(e),
                )
                await websocket.send_json({
                    "error": "Processing error"
                })
                
    except Exception as e:
        logger.error(
            "supervisor_websocket_connection_error",
            supervisor_type=supervisor_type.value,
            client_id=client_id,
            error=str(e),
        )
    finally:
        connection_manager.disconnect(websocket, client_id)
        connection_manager.unsubscribe_supervisor(supervisor_type.value, websocket)


# Error Handlers (deprecated - use FastAPI exception handlers in main app)

# @router.exception_handler(ValueError)
# async def value_error_handler(request: Any, exc: ValueError) -> JSONResponse:
#     return JSONResponse(
#         status_code=status.HTTP_400_BAD_REQUEST,
#         content=SupervisorErrorResponse(
#             error_code="INVALID_VALUE",
#             message=str(exc),
#             request_id=str(id(request))
#         ).model_dump()
#     )


# @router.exception_handler(Exception)
# async def general_exception_handler(request: Any, exc: Exception) -> JSONResponse:
#     logger.error(f"Unhandled exception: {exc}")
#     return JSONResponse(
#         status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#         content=SupervisorErrorResponse(
#             error_code="INTERNAL_ERROR",
#             message="An internal error occurred",
#             request_id=str(id(request)),
#             suggestions=["Please try again later", "Contact support if problem persists"]
#         ).model_dump()
#     )
