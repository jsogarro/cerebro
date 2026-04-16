"""
Hierarchical Supervisor API Routes

This module implements the REST API endpoints for supervisor coordination,
following the "Talk Structurally, Act Hierarchically" research patterns.
Provides comprehensive supervisor management, worker coordination, and
cross-domain orchestration capabilities.
"""

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, Depends, status
from fastapi.responses import JSONResponse
import asyncio
import logging

from src.models.supervisor_api_models import (
    SupervisorType,
    SupervisorExecuteRequest,
    SupervisorExecuteResponse,
    WorkerCoordinationRequest,
    WorkerCoordinationResponse,
    MultiSupervisorOrchestrationRequest,
    MultiSupervisorOrchestrationResponse,
    WorkerAllocationOptimizationRequest,
    WorkerAllocationOptimizationResponse,
    ConflictResolutionRequest,
    ConflictResolutionResponse,
    ExperimentRequest,
    ExperimentResponse,
    SupervisorInfo,
    WorkerInfo,
    SupervisorStatsResponse,
    SupervisorHealthResponse,
    SupervisorComparisonResponse,
    SupervisorListResponse,
    WorkerListResponse,
    SupervisorWebSocketEvent,
    WorkerCoordinationProgressEvent,
    SupervisorErrorResponse,
)
from src.api.services.supervisor_coordination_service import SupervisorCoordinationService


# Configure logging
logger = logging.getLogger(__name__)

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

# WebSocket connection manager for real-time updates
class SupervisorConnectionManager:
    """Manages WebSocket connections for supervisor real-time updates"""
    
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}
        self.supervisor_subscriptions: Dict[str, List[WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, client_id: str):
        """Accept and register a WebSocket connection"""
        await websocket.accept()
        if client_id not in self.active_connections:
            self.active_connections[client_id] = []
        self.active_connections[client_id].append(websocket)
    
    def disconnect(self, websocket: WebSocket, client_id: str):
        """Remove a WebSocket connection"""
        if client_id in self.active_connections:
            self.active_connections[client_id].remove(websocket)
            if not self.active_connections[client_id]:
                del self.active_connections[client_id]
    
    async def send_supervisor_event(self, supervisor_type: str, event: SupervisorWebSocketEvent):
        """Send event to all clients subscribed to a supervisor"""
        if supervisor_type in self.supervisor_subscriptions:
            for connection in self.supervisor_subscriptions[supervisor_type]:
                try:
                    await connection.send_json(event.model_dump())
                except Exception as e:
                    logger.error(f"Error sending event to client: {e}")
    
    async def broadcast_event(self, event: dict):
        """Broadcast event to all connected clients"""
        for client_connections in self.active_connections.values():
            for connection in client_connections:
                try:
                    await connection.send_json(event)
                except Exception as e:
                    logger.error(f"Error broadcasting event: {e}")

# Initialize connection manager
connection_manager = SupervisorConnectionManager()


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
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error executing supervisor task: {e}")
        raise HTTPException(
            status_code=500,
            detail="Internal error during supervisor execution"
        )


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
        logger.error(f"Error listing supervisors: {e}")
        raise HTTPException(
            status_code=500,
            detail="Error retrieving supervisor list"
        )


@router.get("/{supervisor_type}", response_model=SupervisorInfo)
async def get_supervisor_info(supervisor_type: SupervisorType) -> SupervisorInfo:
    """
    Get detailed information about a specific supervisor.
    """
    try:
        return await supervisor_service.get_supervisor_info(supervisor_type.value)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting supervisor info: {e}")
        raise HTTPException(
            status_code=500,
            detail="Error retrieving supervisor information"
        )


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
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting supervisor workers: {e}")
        raise HTTPException(
            status_code=500,
            detail="Error retrieving worker information"
        )


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
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error coordinating workers: {e}")
        raise HTTPException(
            status_code=500,
            detail="Error during worker coordination"
        )


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
        logger.error(f"Error in multi-supervisor orchestration: {e}")
        raise HTTPException(
            status_code=500,
            detail="Error during multi-supervisor orchestration"
        )


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
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting supervisor stats: {e}")
        raise HTTPException(
            status_code=500,
            detail="Error retrieving supervisor statistics"
        )


@router.get("/{supervisor_type}/health", response_model=SupervisorHealthResponse)
async def get_supervisor_health(supervisor_type: SupervisorType) -> SupervisorHealthResponse:
    """
    Get health status of a specific supervisor.
    
    Provides health metrics and recommendations for supervisor optimization.
    """
    try:
        return await supervisor_service.get_supervisor_health(supervisor_type.value)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting supervisor health: {e}")
        raise HTTPException(
            status_code=500,
            detail="Error retrieving supervisor health status"
        )


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
        logger.error(f"Error optimizing worker allocation: {e}")
        raise HTTPException(
            status_code=500,
            detail="Error during allocation optimization"
        )


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
        logger.error(f"Error resolving conflicts: {e}")
        raise HTTPException(
            status_code=500,
            detail="Error during conflict resolution"
        )


@router.get("/performance/compare", response_model=SupervisorComparisonResponse)
async def compare_supervisor_performance(
    supervisors: List[SupervisorType] = Query(
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
        logger.error(f"Error comparing supervisor performance: {e}")
        raise HTTPException(
            status_code=500,
            detail="Error during performance comparison"
        )


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
        logger.error(f"Error running experiment: {e}")
        raise HTTPException(
            status_code=500,
            detail="Error during coordination experiment"
        )


# WebSocket Endpoints

@router.websocket("/{supervisor_type}/ws")
async def supervisor_websocket(
    websocket: WebSocket,
    supervisor_type: SupervisorType,
    client_id: Optional[str] = Query(None)
):
    """
    WebSocket endpoint for real-time supervisor updates.
    
    Provides live updates on supervisor status, task assignments,
    worker coordination progress, and performance alerts.
    """
    client_id = client_id or f"anonymous-{id(websocket)}"
    
    try:
        await connection_manager.connect(websocket, client_id)
        
        # Subscribe to supervisor events
        if supervisor_type.value not in connection_manager.supervisor_subscriptions:
            connection_manager.supervisor_subscriptions[supervisor_type.value] = []
        connection_manager.supervisor_subscriptions[supervisor_type.value].append(websocket)
        
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
                logger.error(f"WebSocket error: {e}")
                await websocket.send_json({
                    "error": "Processing error"
                })
                
    except Exception as e:
        logger.error(f"WebSocket connection error: {e}")
    finally:
        connection_manager.disconnect(websocket, client_id)
        if supervisor_type.value in connection_manager.supervisor_subscriptions:
            if websocket in connection_manager.supervisor_subscriptions[supervisor_type.value]:
                connection_manager.supervisor_subscriptions[supervisor_type.value].remove(websocket)


@router.websocket("/coordination/ws")
async def coordination_progress_websocket(
    websocket: WebSocket,
    coordination_id: Optional[str] = Query(None)
):
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
            for progress in [10, 30, 50, 70, 90, 100]:
                await asyncio.sleep(1)  # Simulate processing
                
                event = WorkerCoordinationProgressEvent(
                    coordination_id=coordination_id,
                    event_type="progress",
                    progress_percentage=float(progress),
                    current_phase=f"Phase {progress // 25 + 1}",
                    workers_active=5 - (progress // 25),
                    estimated_remaining_seconds=max(0, 10 - progress // 10)
                )
                
                await websocket.send_json(event.model_dump())
                
                if progress == 100:
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
        logger.error(f"Coordination WebSocket error: {e}")
    finally:
        connection_manager.disconnect(websocket, client_id)


# Error Handlers

@router.exception_handler(ValueError)
async def value_error_handler(request, exc: ValueError):
    """Handle ValueError exceptions"""
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=SupervisorErrorResponse(
            error_code="INVALID_VALUE",
            message=str(exc),
            request_id=str(id(request))
        ).model_dump()
    )


@router.exception_handler(Exception)
async def general_exception_handler(request, exc: Exception):
    """Handle general exceptions"""
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=SupervisorErrorResponse(
            error_code="INTERNAL_ERROR",
            message="An internal error occurred",
            request_id=str(id(request)),
            suggestions=["Please try again later", "Contact support if problem persists"]
        ).model_dump()
    )