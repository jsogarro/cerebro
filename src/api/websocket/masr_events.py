"""
MASR WebSocket Events Module

Real-time routing events and notifications for MASR routing intelligence.
Enables live streaming of routing decisions, cost optimization updates,
and strategy evaluation notifications.

Based on "MasRouter: Learning to Route LLMs" research patterns.
"""

import asyncio
from typing import Dict, List, Optional, Any, Set
from datetime import datetime
from enum import Enum

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from src.api.websocket.models import WebSocketMessage, MessageType
from src.ai_brain.models.masr import (
    QueryDomain,
    QueryComplexity,
    RoutingStrategy,
    RoutingDecision
)
from src.models.masr_api_models import (
    RoutingDecisionResponse,
    CostEstimationResponse,
    RouterStatus
)


class MASREventType(str, Enum):
    """Types of MASR routing events"""
    ROUTING_STARTED = "routing_started"
    ROUTING_DECISION = "routing_decision"
    COST_UPDATE = "cost_update"
    STRATEGY_CHANGE = "strategy_change"
    OPTIMIZATION_UPDATE = "optimization_update"
    LEARNING_UPDATE = "learning_update"
    PERFORMANCE_ALERT = "performance_alert"
    MODEL_AVAILABILITY = "model_availability"
    SUPERVISOR_ALLOCATION = "supervisor_allocation"
    ROUTING_COMPLETE = "routing_complete"
    ROUTING_ERROR = "routing_error"


class MASREvent(BaseModel):
    """Base MASR routing event"""
    event_type: MASREventType
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    routing_id: Optional[str] = None
    data: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = None


class RoutingProgressEvent(MASREvent):
    """Progress update for routing decision"""
    event_type: MASREventType = MASREventType.ROUTING_STARTED
    query_preview: str
    estimated_complexity: QueryComplexity
    estimated_duration_ms: int
    stages: List[str] = [
        "query_analysis",
        "strategy_selection", 
        "supervisor_allocation",
        "cost_optimization",
        "final_decision"
    ]
    current_stage: str


class CostOptimizationEvent(MASREvent):
    """Real-time cost optimization update"""
    event_type: MASREventType = MASREventType.COST_UPDATE
    original_cost: float
    optimized_cost: float
    cost_reduction_percent: float
    optimization_strategy: str
    confidence_score: float
    breakdown: Optional[Dict[str, float]] = None


class StrategyEvaluationEvent(MASREvent):
    """Strategy evaluation progress"""
    event_type: MASREventType = MASREventType.STRATEGY_CHANGE
    strategies_evaluated: List[str]
    current_best: str
    current_score: float
    evaluation_progress: float  # 0-1
    trade_offs: Dict[str, str]


class LearningUpdateEvent(MASREvent):
    """Learning system update notification"""
    event_type: MASREventType = MASREventType.LEARNING_UPDATE
    feedback_count: int
    accuracy_improvement: float
    cost_prediction_accuracy: float
    quality_prediction_accuracy: float
    strategies_refined: List[str]
    next_retraining: Optional[datetime] = None


class PerformanceAlertEvent(MASREvent):
    """Performance alert notification"""
    event_type: MASREventType = MASREventType.PERFORMANCE_ALERT
    alert_level: str  # "info", "warning", "critical"
    metric: str
    current_value: float
    threshold: float
    message: str
    recommendations: List[str]


class MASRWebSocketManager:
    """
    Manager for MASR WebSocket connections and real-time events.
    
    Handles:
    - Connection lifecycle management
    - Event broadcasting to subscribed clients
    - Routing progress streaming
    - Cost optimization notifications
    - Learning update broadcasts
    """
    
    def __init__(self):
        """Initialize WebSocket manager"""
        self.active_connections: Dict[str, Set[WebSocket]] = {
            "global": set(),  # Global MASR events
            "routing": set(),  # Active routing sessions
            "learning": set(),  # Learning updates
            "monitoring": set()  # Performance monitoring
        }
        self.routing_sessions: Dict[str, Dict[str, Any]] = {}
        self.event_history: List[MASREvent] = []
        self.max_history_size = 1000
        
    async def connect(
        self,
        websocket: WebSocket,
        channel: str = "global",
        routing_id: Optional[str] = None
    ):
        """
        Connect a WebSocket client to MASR events.
        
        Args:
            websocket: WebSocket connection
            channel: Event channel to subscribe to
            routing_id: Optional specific routing session ID
        """
        await websocket.accept()
        
        if channel not in self.active_connections:
            self.active_connections[channel] = set()
        
        self.active_connections[channel].add(websocket)
        
        # If subscribing to specific routing session
        if routing_id:
            if routing_id not in self.routing_sessions:
                self.routing_sessions[routing_id] = {
                    "websockets": set(),
                    "start_time": datetime.utcnow(),
                    "events": []
                }
            self.routing_sessions[routing_id]["websockets"].add(websocket)
        
        # Send connection confirmation
        await self._send_event(
            websocket,
            MASREvent(
                event_type=MASREventType.ROUTING_STARTED,
                data={
                    "message": "Connected to MASR routing events",
                    "channel": channel,
                    "routing_id": routing_id
                }
            )
        )
    
    def disconnect(self, websocket: WebSocket):
        """
        Disconnect a WebSocket client.
        
        Args:
            websocket: WebSocket connection to disconnect
        """
        # Remove from all channels
        for channel in self.active_connections:
            self.active_connections[channel].discard(websocket)
        
        # Remove from routing sessions
        for session in self.routing_sessions.values():
            session["websockets"].discard(websocket)
    
    async def broadcast_routing_progress(
        self,
        routing_id: str,
        stage: str,
        progress_data: Dict[str, Any]
    ):
        """
        Broadcast routing progress to subscribed clients.
        
        Args:
            routing_id: Routing session ID
            stage: Current routing stage
            progress_data: Progress information
        """
        event = RoutingProgressEvent(
            routing_id=routing_id,
            current_stage=stage,
            query_preview=progress_data.get("query_preview", ""),
            estimated_complexity=progress_data.get("complexity", QueryComplexity.MODERATE),
            estimated_duration_ms=progress_data.get("duration_ms", 1000),
            data=progress_data
        )
        
        await self._broadcast_to_channel("routing", event)
        
        # Also send to session-specific subscribers
        if routing_id in self.routing_sessions:
            for ws in self.routing_sessions[routing_id]["websockets"]:
                await self._send_event(ws, event)
    
    async def broadcast_cost_optimization(
        self,
        routing_id: str,
        original_cost: float,
        optimized_cost: float,
        optimization_details: Dict[str, Any]
    ):
        """
        Broadcast cost optimization updates.
        
        Args:
            routing_id: Routing session ID
            original_cost: Original estimated cost
            optimized_cost: Optimized cost after analysis
            optimization_details: Details of optimization
        """
        cost_reduction = ((original_cost - optimized_cost) / original_cost) * 100
        
        event = CostOptimizationEvent(
            routing_id=routing_id,
            original_cost=original_cost,
            optimized_cost=optimized_cost,
            cost_reduction_percent=cost_reduction,
            optimization_strategy=optimization_details.get("strategy", "balanced"),
            confidence_score=optimization_details.get("confidence", 0.85),
            breakdown=optimization_details.get("breakdown"),
            data=optimization_details
        )
        
        await self._broadcast_to_channel("routing", event)
        await self._broadcast_to_channel("monitoring", event)
    
    async def broadcast_strategy_evaluation(
        self,
        routing_id: str,
        strategies: List[str],
        current_best: str,
        evaluation_data: Dict[str, Any]
    ):
        """
        Broadcast strategy evaluation progress.
        
        Args:
            routing_id: Routing session ID
            strategies: Strategies being evaluated
            current_best: Current best strategy
            evaluation_data: Evaluation details
        """
        event = StrategyEvaluationEvent(
            routing_id=routing_id,
            strategies_evaluated=strategies,
            current_best=current_best,
            current_score=evaluation_data.get("score", 0),
            evaluation_progress=evaluation_data.get("progress", 0),
            trade_offs=evaluation_data.get("trade_offs", {}),
            data=evaluation_data
        )
        
        await self._broadcast_to_channel("routing", event)
    
    async def broadcast_learning_update(
        self,
        feedback_stats: Dict[str, Any],
        improvements: Dict[str, float]
    ):
        """
        Broadcast learning system updates.
        
        Args:
            feedback_stats: Feedback statistics
            improvements: Performance improvements
        """
        event = LearningUpdateEvent(
            feedback_count=feedback_stats.get("total_feedback", 0),
            accuracy_improvement=improvements.get("accuracy", 0),
            cost_prediction_accuracy=improvements.get("cost_accuracy", 0),
            quality_prediction_accuracy=improvements.get("quality_accuracy", 0),
            strategies_refined=feedback_stats.get("refined_strategies", []),
            next_retraining=feedback_stats.get("next_retraining"),
            data={
                "feedback_stats": feedback_stats,
                "improvements": improvements
            }
        )
        
        await self._broadcast_to_channel("learning", event)
        await self._broadcast_to_channel("global", event)
    
    async def broadcast_performance_alert(
        self,
        metric: str,
        current_value: float,
        threshold: float,
        alert_level: str = "info"
    ):
        """
        Broadcast performance alerts.
        
        Args:
            metric: Metric name
            current_value: Current metric value
            threshold: Alert threshold
            alert_level: Alert severity level
        """
        message = f"{metric} has reached {current_value} (threshold: {threshold})"
        
        recommendations = []
        if "latency" in metric.lower():
            recommendations.append("Consider speed-optimized strategy")
            recommendations.append("Check model availability")
        elif "cost" in metric.lower():
            recommendations.append("Switch to cost-efficient strategy")
            recommendations.append("Review supervisor allocations")
        elif "error" in metric.lower():
            recommendations.append("Enable fallback mechanisms")
            recommendations.append("Check service health")
        
        event = PerformanceAlertEvent(
            alert_level=alert_level,
            metric=metric,
            current_value=current_value,
            threshold=threshold,
            message=message,
            recommendations=recommendations,
            data={
                "metric": metric,
                "value": current_value,
                "threshold": threshold
            }
        )
        
        await self._broadcast_to_channel("monitoring", event)
        
        # Critical alerts go to all channels
        if alert_level == "critical":
            await self._broadcast_to_channel("global", event)
    
    async def send_routing_complete(
        self,
        routing_id: str,
        decision: RoutingDecisionResponse
    ):
        """
        Send routing completion notification.
        
        Args:
            routing_id: Routing session ID
            decision: Final routing decision
        """
        event = MASREvent(
            event_type=MASREventType.ROUTING_COMPLETE,
            routing_id=routing_id,
            data={
                "domain": decision.domain,
                "strategy": decision.strategy,
                "estimated_cost": decision.estimated_cost,
                "estimated_latency_ms": decision.estimated_latency_ms,
                "confidence_score": decision.confidence_score,
                "supervisor_count": len(decision.supervisor_allocations),
                "reasoning": decision.reasoning
            }
        )
        
        await self._broadcast_to_channel("routing", event)
        
        # Clean up session
        if routing_id in self.routing_sessions:
            del self.routing_sessions[routing_id]
    
    async def send_routing_error(
        self,
        routing_id: Optional[str],
        error: str,
        details: Optional[Dict[str, Any]] = None
    ):
        """
        Send routing error notification.
        
        Args:
            routing_id: Routing session ID (if applicable)
            error: Error message
            details: Additional error details
        """
        event = MASREvent(
            event_type=MASREventType.ROUTING_ERROR,
            routing_id=routing_id,
            data={
                "error": error,
                "details": details or {},
                "timestamp": datetime.utcnow().isoformat()
            }
        )
        
        await self._broadcast_to_channel("routing", event)
        
        if routing_id in self.routing_sessions:
            for ws in self.routing_sessions[routing_id]["websockets"]:
                await self._send_event(ws, event)
    
    async def _broadcast_to_channel(self, channel: str, event: MASREvent):
        """
        Broadcast event to all clients in a channel.
        
        Args:
            channel: Channel name
            event: Event to broadcast
        """
        if channel not in self.active_connections:
            return
        
        # Store in history
        self._add_to_history(event)
        
        # Broadcast to all connections in channel
        disconnected = set()
        for websocket in self.active_connections[channel]:
            try:
                await self._send_event(websocket, event)
            except WebSocketDisconnect:
                disconnected.add(websocket)
            except Exception:
                disconnected.add(websocket)
        
        # Clean up disconnected clients
        for ws in disconnected:
            self.disconnect(ws)
    
    async def _send_event(self, websocket: WebSocket, event: MASREvent):
        """
        Send event to a specific WebSocket client.
        
        Args:
            websocket: WebSocket connection
            event: Event to send
        """
        message = WebSocketMessage(
            type=MessageType.MASR_EVENT,
            data=event.dict(),
            timestamp=event.timestamp
        )
        
        await websocket.send_json(message.dict())
    
    def _add_to_history(self, event: MASREvent):
        """
        Add event to history with size limit.
        
        Args:
            event: Event to add
        """
        self.event_history.append(event)
        
        # Trim history if too large
        if len(self.event_history) > self.max_history_size:
            self.event_history = self.event_history[-self.max_history_size:]
    
    async def get_event_history(
        self,
        event_type: Optional[MASREventType] = None,
        routing_id: Optional[str] = None,
        limit: int = 100
    ) -> List[MASREvent]:
        """
        Get historical events with optional filtering.
        
        Args:
            event_type: Filter by event type
            routing_id: Filter by routing ID
            limit: Maximum events to return
            
        Returns:
            List of historical events
        """
        filtered = self.event_history
        
        if event_type:
            filtered = [e for e in filtered if e.event_type == event_type]
        
        if routing_id:
            filtered = [e for e in filtered if e.routing_id == routing_id]
        
        return filtered[-limit:]


# Global WebSocket manager instance
masr_ws_manager = MASRWebSocketManager()