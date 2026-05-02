"""
TalkHier Protocol API Routes

This module implements REST and WebSocket endpoints for the TalkHier Protocol API,
following patterns from "Talk Structurally, Act Hierarchically" research.

The API provides structured dialogue management, multi-round refinement,
consensus building, and real-time communication capabilities.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    Path,
    Query,
    WebSocket,
    WebSocketDisconnect,
)

from src.api.services.talkhier_session_manager import TalkHierSessionManager
from src.api.services.talkhier_session_service import TalkHierSessionService
from src.api.websocket.connection_manager import ConnectionManager
from src.api.websocket.talkhier_websocket_events import TalkHierWebSocketHandler
from src.models.talkhier_api_models import (
    AnalyticsResponse,
    ConsensusCheckRequest,
    ConsensusResult,
    CoordinationRequest,
    CoordinationStatus,
    InteractiveCommand,
    InteractiveMessage,
    ProtocolListResponse,
    ProtocolType,
    ProtocolValidationRequest,
    RefinementRoundRequest,
    RefinementRoundResponse,
    SessionCloseRequest,
    SessionCloseResponse,
    SessionStatusResponse,
    TalkHierSessionRequest,
    TalkHierSessionResponse,
    ValidationResponse,
)

logger = logging.getLogger(__name__)

# Create router with prefix and tags
router = APIRouter(
    prefix="/api/v1/talkhier",
    tags=["TalkHier Protocol", "Agent Framework APIs"]
)

# Initialize services
session_service = TalkHierSessionService()
session_manager = TalkHierSessionManager()
websocket_handler = TalkHierWebSocketHandler()
connection_manager = ConnectionManager()


# ================================
# Session Management Endpoints
# ================================

@router.post("/sessions", response_model=TalkHierSessionResponse)
async def create_refinement_session(
    request: TalkHierSessionRequest
) -> TalkHierSessionResponse:
    """
    Start a new TalkHier refinement session
    
    Creates a structured dialogue session for multi-round refinement
    with consensus building and quality assurance.
    
    Research Foundation:
    - "Talk Structurally, Act Hierarchically" multi-round refinement
    - Consensus mechanisms for quality assurance
    - Hierarchical supervision patterns
    
    Args:
        request: Session configuration request
        
    Returns:
        Session creation response with ID and WebSocket URL
        
    Example:
        ```python
        response = await client.post("/api/v1/talkhier/sessions", json={
            "query": "Analyze AI impact on employment",
            "domains": ["research", "analytics"],
            "protocol_type": "standard",
            "refinement_strategy": "quality_focused",
            "max_rounds": 3,
            "quality_threshold": 0.85
        })
        session_id = response.json()["session_id"]
        ```
    """
    try:
        logger.info(f"Creating TalkHier session for query: {request.query[:100]}...")
        
        # Create session through service
        session_response = await session_service.create_session(request)
        
        # Register with session manager for monitoring
        await session_manager.register_session(
            session_id=session_response.session_id,
            config={
                "protocol_type": request.protocol_type,
                "refinement_strategy": request.refinement_strategy,
                "max_rounds": request.max_rounds,
                "quality_threshold": request.quality_threshold
            }
        )
        
        # Log analytics
        await _log_session_analytics(
            "session_created",
            session_response.session_id,
            {
                "protocol": request.protocol_type.value,
                "strategy": request.refinement_strategy.value,
                "participants": len(session_response.participants)
            }
        )
        
        return session_response

    except Exception as e:
        logger.error(f"Failed to create TalkHier session: {e!s}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/sessions/{session_id}", response_model=SessionStatusResponse)
async def get_session_status(
    session_id: str = Path(..., description="Session identifier")
) -> SessionStatusResponse:
    """
    Get current status of a TalkHier session
    
    Returns detailed session information including round history,
    quality scores, consensus levels, and timing information.
    
    Args:
        session_id: Unique session identifier
        
    Returns:
        Current session status and history
        
    Example:
        ```python
        status = await client.get(f"/api/v1/talkhier/sessions/{session_id}")
        print(f"Round {status['current_round']}, Quality: {status['current_quality']}")
        ```
    """
    try:
        status_response = await session_service.get_session_status(session_id)
        
        # Log access
        await _log_session_analytics(
            "session_accessed",
            session_id,
            {"current_round": status_response.current_round}
        )
        
        return status_response
        
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except Exception as e:
        logger.error(f"Failed to get session status: {e!s}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/sessions/{session_id}/round", response_model=RefinementRoundResponse)
async def execute_refinement_round(
    session_id: str,
    request: RefinementRoundRequest
) -> RefinementRoundResponse:
    """
    Execute a refinement round in an active session
    
    Coordinates multi-agent refinement with quality assessment
    and consensus tracking. Each round improves upon previous results.
    
    Research Pattern: Progressive refinement with consensus building
    
    Args:
        session_id: Session identifier
        request: Round execution parameters
        
    Returns:
        Round results with quality and consensus scores
        
    Example:
        ```python
        round_result = await client.post(
            f"/api/v1/talkhier/sessions/{session_id}/round",
            json={
                "round_number": 2,
                "previous_result": previous_round_result,
                "refinement_focus": "Improve evidence quality"
            }
        )
        ```
    """
    try:
        logger.info(f"Executing refinement round {request.round_number} for session {session_id}")
        
        # Execute round
        round_response = await session_service.execute_refinement_round(
            session_id,
            request
        )
        
        # Broadcast round completion via WebSocket
        await websocket_handler.broadcast_round_completed(
            session_id,
            round_response
        )
        
        # Update session manager
        await session_manager.update_round_metrics(
            session_id,
            {
                "round": request.round_number,
                "quality": round_response.quality_score,
                "consensus": round_response.consensus_score,
                "duration_ms": round_response.duration_ms
            }
        )
        
        # Log analytics
        await _log_session_analytics(
            "round_executed",
            session_id,
            {
                "round": request.round_number,
                "quality": round_response.quality_score,
                "consensus": round_response.consensus_score,
                "continue": round_response.continue_refinement
            }
        )
        
        return round_response

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except Exception as e:
        logger.error(f"Failed to execute refinement round: {e!s}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/sessions/{session_id}/consensus", response_model=ConsensusResult)
async def check_consensus_status(
    session_id: str,
    request: ConsensusCheckRequest
) -> ConsensusResult:
    """
    Check consensus status among session participants
    
    Analyzes agreement levels between agents and provides
    detailed consensus metrics with minority reports if requested.
    
    Research Pattern: Consensus detection for quality assurance
    
    Args:
        session_id: Session identifier
        request: Consensus check parameters
        
    Returns:
        Detailed consensus analysis
        
    Example:
        ```python
        consensus = await client.post(
            f"/api/v1/talkhier/sessions/{session_id}/consensus",
            json={
                "round_results": round_responses,
                "check_quality": True,
                "include_minority_report": True
            }
        )
        ```
    """
    try:
        consensus_result = await session_service.check_consensus(
            session_id,
            request
        )
        
        # Broadcast consensus update via WebSocket
        await websocket_handler.broadcast_consensus_update(
            session_id,
            consensus_result
        )
        
        # Log analytics
        await _log_session_analytics(
            "consensus_checked",
            session_id,
            {
                "has_consensus": consensus_result.has_consensus,
                "score": consensus_result.consensus_score,
                "type": consensus_result.consensus_type.value
            }
        )
        
        return consensus_result

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except Exception as e:
        logger.error(f"Failed to check consensus: {e!s}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/sessions/{session_id}/close", response_model=SessionCloseResponse)
async def close_session(
    session_id: str,
    request: SessionCloseRequest = SessionCloseRequest(reason=None, save_transcript=True, generate_summary=True)
) -> SessionCloseResponse:
    """
    Close a TalkHier session
    
    Finalizes the session, generates summary, and optionally
    saves transcript for future reference.
    
    Args:
        session_id: Session identifier
        request: Close parameters
        
    Returns:
        Session closure summary
        
    Example:
        ```python
        closure = await client.post(
            f"/api/v1/talkhier/sessions/{session_id}/close",
            json={
                "save_transcript": True,
                "generate_summary": True
            }
        )
        ```
    """
    try:
        close_response = await session_service.close_session(
            session_id,
            request
        )
        
        # Unregister from session manager
        await session_manager.unregister_session(session_id)
        
        # Broadcast session completion
        await websocket_handler.broadcast_session_completed(
            session_id,
            close_response
        )

        # Close WebSocket connections for all clients in this session
        if session_id in websocket_handler.session_connections:
            disconnect_errors: list[str] = []
            for connection_id in list(websocket_handler.session_connections[session_id]):
                try:
                    await connection_manager.disconnect(connection_id)
                except Exception as disconnect_err:
                    disconnect_errors.append(f"{connection_id}: {disconnect_err}")
            if disconnect_errors:
                logger.warning(
                    f"Failed to disconnect {len(disconnect_errors)} connection(s) "
                    f"for session {session_id}: {disconnect_errors}"
                )
        
        # Log analytics
        await _log_session_analytics(
            "session_closed",
            session_id,
            {
                "final_status": close_response.final_status.value,
                "total_rounds": close_response.total_rounds,
                "final_quality": close_response.final_quality,
                "final_consensus": close_response.final_consensus
            }
        )
        
        return close_response

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except Exception as e:
        logger.error(f"Failed to close session: {e!s}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# ================================
# Protocol Management Endpoints
# ================================

@router.get("/protocols", response_model=ProtocolListResponse)
async def list_available_protocols() -> ProtocolListResponse:
    """
    List available TalkHier protocol configurations
    
    Returns all available protocol types with their characteristics
    and recommended use cases.
    
    Returns:
        List of available protocols
        
    Example:
        ```python
        protocols = await client.get("/api/v1/talkhier/protocols")
        for protocol in protocols["protocols"]:
            print(f"{protocol['type']}: {protocol['description']}")
        ```
    """
    protocols = [
        {
            "type": ProtocolType.STANDARD.value,
            "description": "Default multi-round refinement protocol",
            "default_rounds": 3,
            "characteristics": ["balanced", "general-purpose"],
            "use_cases": ["research queries", "analysis tasks"]
        },
        {
            "type": ProtocolType.FAST_TRACK.value,
            "description": "Reduced rounds for simple tasks",
            "default_rounds": 2,
            "characteristics": ["quick", "efficient"],
            "use_cases": ["simple queries", "time-sensitive tasks"]
        },
        {
            "type": ProtocolType.DEEP_ANALYSIS.value,
            "description": "Extended refinement for complex tasks",
            "default_rounds": 5,
            "characteristics": ["thorough", "quality-focused"],
            "use_cases": ["complex research", "critical analysis"]
        },
        {
            "type": ProtocolType.COLLABORATIVE.value,
            "description": "Equal weight to all participants",
            "default_rounds": 3,
            "characteristics": ["democratic", "consensus-driven"],
            "use_cases": ["team decisions", "collaborative research"]
        },
        {
            "type": ProtocolType.SUPERVISED.value,
            "description": "Supervisor-guided refinement",
            "default_rounds": 3,
            "characteristics": ["hierarchical", "controlled"],
            "use_cases": ["quality-critical", "enterprise tasks"]
        }
    ]
    
    recommended_protocols = {
        "simple_query": ProtocolType.FAST_TRACK,
        "research_query": ProtocolType.STANDARD,
        "complex_analysis": ProtocolType.DEEP_ANALYSIS,
        "team_collaboration": ProtocolType.COLLABORATIVE,
        "enterprise_task": ProtocolType.SUPERVISED
    }
    
    return ProtocolListResponse(
        protocols=protocols,
        default_protocol=ProtocolType.STANDARD,
        recommended_protocols=recommended_protocols
    )


@router.post("/validate", response_model=ValidationResponse)
async def validate_communication_structure(
    request: ProtocolValidationRequest
) -> ValidationResponse:
    """
    Validate TalkHier communication structure
    
    Checks if a sequence of messages follows TalkHier protocol
    guidelines and provides improvement recommendations.
    
    Args:
        request: Messages to validate
        
    Returns:
        Validation results and recommendations
        
    Example:
        ```python
        validation = await client.post("/api/v1/talkhier/validate", json={
            "messages": message_history,
            "expected_protocol": "standard",
            "check_timing": True
        })
        ```
    """
    try:
        validation_response = await session_service.validate_protocol(request)
        
        # Log validation
        logger.info(f"Protocol validation: {validation_response.is_valid} "
                   f"(detected: {validation_response.protocol_detected})")
        
        return validation_response

    except Exception as e:
        logger.error(f"Protocol validation failed: {e!s}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# ================================
# Analytics Endpoints
# ================================

@router.get("/analytics", response_model=AnalyticsResponse)
async def get_protocol_analytics(
    time_range: str | None = Query("24h", description="Time range (1h, 24h, 7d, 30d)"),
    protocol_type: ProtocolType | None = Query(None, description="Filter by protocol type"),
    min_quality: float | None = Query(None, ge=0.0, le=1.0, description="Minimum quality filter")
) -> AnalyticsResponse:
    """
    Get TalkHier protocol performance analytics
    
    Returns aggregated analytics about session performance,
    protocol usage, and quality trends.
    
    Args:
        time_range: Analytics time window
        protocol_type: Optional protocol filter
        min_quality: Optional quality threshold
        
    Returns:
        Protocol performance analytics
        
    Example:
        ```python
        analytics = await client.get("/api/v1/talkhier/analytics?time_range=7d")
        print(f"Success rate: {analytics['success_rate']:.1%}")
        ```
    """
    try:
        # Get analytics from session manager
        analytics = await session_manager.get_analytics(
            time_range=time_range or "24h",
            protocol_type=protocol_type,
            min_quality=min_quality
        )
        
        # Add protocol-specific insights
        if protocol_type:
            analytics["protocol_insights"] = await _get_protocol_insights(
                protocol_type,
                analytics
            )
        
        return AnalyticsResponse(**analytics)

    except Exception as e:
        logger.error(f"Failed to get analytics: {e!s}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# ================================
# WebSocket Endpoints
# ================================

@router.websocket("/sessions/{session_id}/live")
async def websocket_session_updates(
    websocket: WebSocket,
    session_id: str
) -> None:
    """
    WebSocket endpoint for real-time session updates
    
    Provides live updates during TalkHier refinement sessions including:
    - Round start/completion events
    - Message exchanges between participants
    - Consensus updates
    - Quality score changes
    
    Example:
        ```javascript
        const ws = new WebSocket(`ws://localhost:8000/api/v1/talkhier/sessions/${sessionId}/live`);
        
        ws.onmessage = (event) => {
            const update = JSON.parse(event.data);
            console.log(`Event: ${update.event_type}`, update.data);
        };
        ```
    """
    connection_id = await connection_manager.connect(websocket)

    try:
        # Register connection for session
        await websocket_handler.register_session_connection(
            session_id,
            connection_id,
            websocket
        )
        
        # Send initial session status
        status = await session_service.get_session_status(session_id)
        await websocket.send_json({
            "event_type": "session_status",
            "session_id": session_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": {
                "status": status.status.value,
                "current_round": status.current_round,
                "current_quality": status.current_quality,
                "current_consensus": status.current_consensus
            }
        })
        
        # Keep connection alive and handle messages
        while True:
            data = await websocket.receive_json()
            
            # Handle client messages (e.g., status requests)
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif data.get("type") == "get_status":
                status = await session_service.get_session_status(session_id)
                await websocket.send_json({
                    "type": "status_update",
                    "data": status.dict()
                })
                
    except WebSocketDisconnect:
        await websocket_handler.unregister_session_connection(
            session_id,
            connection_id
        )
        await connection_manager.disconnect(connection_id)
    except Exception as e:
        logger.error(f"WebSocket error for session {session_id}: {e!s}")
        await websocket.close(code=1011, reason=str(e))


@router.websocket("/interactive")
async def websocket_interactive_session(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for interactive TalkHier dialogue
    
    Enables real-time interactive refinement sessions where
    users can participate in the structured dialogue process.
    
    Example:
        ```javascript
        const ws = new WebSocket('ws://localhost:8000/api/v1/talkhier/interactive');
        
        // Send message
        ws.send(JSON.stringify({
            type: 'message',
            content: 'I believe we should focus on recent developments',
            role: 'worker',
            confidence: 0.85
        }));
        
        // Send command
        ws.send(JSON.stringify({
            type: 'command',
            command: 'force_consensus'
        }));
        ```
    """
    connection_id = await connection_manager.connect(websocket)
    session_id: str | None = None
    
    try:
        # Wait for session initialization
        init_data = await websocket.receive_json()
        
        if init_data.get("type") == "init_session":
            # Create or join session
            if "session_id" in init_data:
                session_id = init_data["session_id"]
                # Join existing session
                await websocket_handler.join_interactive_session(
                    session_id,
                    connection_id,
                    websocket
                )
            else:
                # Create new interactive session
                request = TalkHierSessionRequest(**init_data.get("config", {}))
                session_response = await session_service.create_session(request)
                session_id = session_response.session_id
                
                await websocket_handler.register_interactive_session(
                    session_id,
                    connection_id,
                    websocket
                )
            
            # Send confirmation
            await websocket.send_json({
                "type": "session_initialized",
                "session_id": session_id
            })
        
        # Handle interactive messages and commands
        while True:
            data = await websocket.receive_json()
            
            if data.get("type") == "message":
                # Handle interactive message
                message = InteractiveMessage(**data)
                if session_id is not None:
                    await websocket_handler.handle_interactive_message(
                        session_id,
                        connection_id,
                        message
                    )

            elif data.get("type") == "command":
                # Handle interactive command
                command = InteractiveCommand(**data)
                if session_id is not None:
                    await websocket_handler.handle_interactive_command(
                        session_id,
                        connection_id,
                        command
                    )
                
    except WebSocketDisconnect:
        if session_id:
            await websocket_handler.leave_interactive_session(
                session_id,
                connection_id
            )
        await connection_manager.disconnect(connection_id)
    except Exception as e:
        logger.error(f"Interactive session error: {e!s}")
        await websocket.close(code=1011, reason=str(e))


@router.websocket("/coordination")
async def websocket_coordination_monitoring(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for multi-session coordination monitoring
    
    Provides real-time updates for coordinated TalkHier sessions
    running in parallel or hierarchical patterns.
    
    Example:
        ```javascript
        const ws = new WebSocket('ws://localhost:8000/api/v1/talkhier/coordination');
        
        ws.send(JSON.stringify({
            type: 'monitor',
            coordination_id: 'coord-123'
        }));
        
        ws.onmessage = (event) => {
            const update = JSON.parse(event.data);
            console.log(`Coordination progress: ${update.overall_progress}`);
        };
        ```
    """
    connection_id = await connection_manager.connect(websocket)
    coordination_id: str | None = None

    try:
        # Wait for coordination monitoring request
        init_data = await websocket.receive_json()

        if init_data.get("type") == "monitor":
            coordination_id = init_data["coordination_id"]

            # Register for coordination updates
            await websocket_handler.register_coordination_monitor(
                coordination_id,
                connection_id,
                websocket
            )

            # Send initial status
            if coordination_id is not None:
                status = await session_manager.get_coordination_status(coordination_id)
            await websocket.send_json({
                "type": "coordination_status",
                "data": status.dict()
            })
        
        # Keep connection alive and send updates
        while True:
            data = await websocket.receive_json()
            
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif data.get("type") == "get_status" and coordination_id is not None:
                status = await session_manager.get_coordination_status(coordination_id)
                await websocket.send_json({
                    "type": "status_update",
                    "data": status.dict()
                })

    except WebSocketDisconnect:
        if coordination_id:
            await websocket_handler.unregister_coordination_monitor(
                coordination_id,
                connection_id
            )
        await connection_manager.disconnect(connection_id)
    except Exception as e:
        logger.error(f"Coordination monitoring error: {e!s}")
        await websocket.close(code=1011, reason=str(e))


# ================================
# Multi-Session Coordination
# ================================

@router.post("/coordinate", response_model=CoordinationStatus)
async def coordinate_multiple_sessions(
    request: CoordinationRequest
) -> CoordinationStatus:
    """
    Coordinate multiple TalkHier sessions
    
    Enables running multiple refinement sessions in coordinated
    patterns (sequential, parallel, or hierarchical).
    
    Research Pattern: Multi-session coordination for complex tasks
    
    Args:
        request: Coordination configuration
        
    Returns:
        Coordination status and monitoring details
        
    Example:
        ```python
        coordination = await client.post("/api/v1/talkhier/coordinate", json={
            "session_ids": ["session-1", "session-2"],
            "coordination_type": "parallel",
            "share_context": True,
            "aggregate_results": True
        })
        ```
    """
    try:
        # Create coordination through session manager
        coordination_status = await session_manager.coordinate_sessions(request)
        
        # Log coordination
        logger.info(f"Created coordination {coordination_status.coordination_id} "
                   f"for {len(request.session_ids)} sessions")
        
        return coordination_status

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception as e:
        logger.error(f"Failed to coordinate sessions: {e!s}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# ================================
# Helper Functions
# ================================

async def _log_session_analytics(
    event_type: str,
    session_id: str,
    metrics: dict[str, Any]
) -> None:
    """Log session analytics for monitoring"""
    try:
        await session_manager.log_analytics_event(
            event_type=event_type,
            session_id=session_id,
            metrics=metrics,
            timestamp=datetime.now(UTC)
        )
    except Exception as e:
        logger.warning(f"Failed to log analytics: {e!s}")


async def _get_protocol_insights(
    protocol_type: ProtocolType,
    analytics: dict[str, Any]
) -> list[str]:
    """Generate protocol-specific insights from analytics"""
    insights = []
    
    if protocol_type == ProtocolType.FAST_TRACK:
        avg_rounds = analytics.get("average_rounds", 0)
        if avg_rounds > 2:
            insights.append("Fast track sessions exceeding expected rounds - consider standard protocol")
    
    elif protocol_type == ProtocolType.DEEP_ANALYSIS:
        avg_quality = analytics.get("average_quality", 0)
        if avg_quality < 0.85:
            insights.append("Deep analysis sessions not achieving expected quality levels")
    
    elif protocol_type == ProtocolType.SUPERVISED:
        success_rate = analytics.get("success_rate", 0)
        if success_rate > 0.95:
            insights.append("Supervised protocol showing excellent success rates")
    
    return insights


# ================================
# Health Check
# ================================

@router.get("/health")
async def talkhier_health_check() -> dict[str, Any]:
    """
    Health check for TalkHier Protocol API
    
    Returns:
        Service health status
    """
    try:
        active_sessions = len(session_service.sessions)
        manager_status = await session_manager.get_health_status()
        
        return {
            "status": "healthy",
            "service": "TalkHier Protocol API",
            "active_sessions": active_sessions,
            "session_manager": manager_status,
            "timestamp": datetime.now(UTC).isoformat()
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "service": "TalkHier Protocol API",
            "error": str(e),
            "timestamp": datetime.now(UTC).isoformat()
        }