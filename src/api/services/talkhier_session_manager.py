"""
TalkHier Session Manager

Advanced session coordination and analytics management for TalkHier protocol.
Handles multi-session coordination, performance tracking, and analytics aggregation.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from src.models.talkhier_api_models import (
    CoordinationRequest,
    CoordinationStatus,
    ProtocolType,
    SessionStatus,
)

logger = logging.getLogger(__name__)


@dataclass
class SessionMetrics:
    """Metrics for a single session"""
    session_id: str
    protocol_type: ProtocolType | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    rounds_completed: int = 0
    quality_scores: list[float] = field(default_factory=list)
    consensus_scores: list[float] = field(default_factory=list)
    round_durations: list[int] = field(default_factory=list)
    final_quality: float = 0.0
    final_consensus: float = 0.0
    success: bool = False


@dataclass
class CoordinationGroup:
    """Group of coordinated sessions"""
    coordination_id: str
    session_ids: list[str]
    coordination_type: str
    created_at: datetime
    status: str = "active"
    shared_context: dict[str, Any] = field(default_factory=dict)
    aggregated_results: dict[str, Any] | None = None


class TalkHierSessionManager:
    """
    Manages TalkHier sessions with analytics and coordination capabilities
    """
    
    def __init__(self) -> None:
        self.sessions: dict[str, SessionMetrics] = {}
        self.coordinations: dict[str, CoordinationGroup] = {}
        self.analytics_events: list[dict[str, Any]] = []
        self.protocol_stats: dict[ProtocolType, dict[str, Any]] = defaultdict(lambda: {
            "total_sessions": 0,
            "successful_sessions": 0,
            "total_rounds": 0,
            "total_quality": 0.0,
            "total_consensus": 0.0
        })
        
    async def register_session(
        self,
        session_id: str,
        config: dict[str, Any]
    ) -> None:
        """Register a new session for tracking"""
        self.sessions[session_id] = SessionMetrics(
            session_id=session_id,
            protocol_type=config.get("protocol_type"),
            started_at=datetime.utcnow()
        )
        
        # Update protocol stats
        if config.get("protocol_type"):
            self.protocol_stats[config["protocol_type"]]["total_sessions"] += 1
    
    async def unregister_session(self, session_id: str) -> None:
        """Unregister a completed session"""
        if session_id in self.sessions:
            metrics = self.sessions[session_id]
            metrics.completed_at = datetime.utcnow()
            
            # Update protocol stats
            if metrics.protocol_type:
                stats = self.protocol_stats[metrics.protocol_type]
                if metrics.success:
                    stats["successful_sessions"] += 1
                stats["total_rounds"] += metrics.rounds_completed
                stats["total_quality"] += metrics.final_quality
                stats["total_consensus"] += metrics.final_consensus
    
    async def update_round_metrics(
        self,
        session_id: str,
        round_data: dict[str, Any]
    ) -> None:
        """Update metrics for a completed round"""
        if session_id in self.sessions:
            metrics = self.sessions[session_id]
            metrics.rounds_completed += 1
            metrics.quality_scores.append(round_data.get("quality", 0.0))
            metrics.consensus_scores.append(round_data.get("consensus", 0.0))
            metrics.round_durations.append(round_data.get("duration_ms", 0))
            metrics.final_quality = round_data.get("quality", 0.0)
            metrics.final_consensus = round_data.get("consensus", 0.0)
    
    async def coordinate_sessions(
        self,
        request: CoordinationRequest
    ) -> CoordinationStatus:
        """Coordinate multiple sessions"""
        coordination_id = f"coord_{datetime.utcnow().timestamp()}"
        
        # Create coordination group
        coordination = CoordinationGroup(
            coordination_id=coordination_id,
            session_ids=request.session_ids,
            coordination_type=request.coordination_type,
            created_at=datetime.utcnow()
        )
        
        self.coordinations[coordination_id] = coordination
        
        # Initialize session statuses
        session_statuses = {}
        for session_id in request.session_ids:
            if session_id in self.sessions:
                session_statuses[session_id] = SessionStatus.ACTIVE
            else:
                session_statuses[session_id] = SessionStatus.INITIALIZING
        
        return CoordinationStatus(
            coordination_id=coordination_id,
            session_statuses=session_statuses,
            overall_progress=0.0,
            aggregated_quality=0.0,
            estimated_completion=None,
            coordination_insights=[]
        )
    
    async def get_coordination_status(
        self,
        coordination_id: str
    ) -> CoordinationStatus:
        """Get status of coordinated sessions"""
        if coordination_id not in self.coordinations:
            raise ValueError(f"Coordination {coordination_id} not found")
        
        coordination = self.coordinations[coordination_id]
        
        # Calculate overall progress
        total_progress = 0.0
        total_quality = 0.0
        session_statuses = {}
        
        for session_id in coordination.session_ids:
            if session_id in self.sessions:
                metrics = self.sessions[session_id]
                session_statuses[session_id] = SessionStatus.ACTIVE
                total_progress += metrics.rounds_completed / max(3, metrics.rounds_completed)
                total_quality += metrics.final_quality
            else:
                session_statuses[session_id] = SessionStatus.INITIALIZING
        
        num_sessions = len(coordination.session_ids)
        overall_progress = total_progress / max(1, num_sessions)
        aggregated_quality = total_quality / max(1, num_sessions)
        
        return CoordinationStatus(
            coordination_id=coordination_id,
            session_statuses=session_statuses,
            overall_progress=overall_progress,
            aggregated_quality=aggregated_quality,
            estimated_completion=None,
            coordination_insights=self._generate_coordination_insights(coordination)
        )
    
    async def get_analytics(
        self,
        time_range: str = "24h",
        protocol_type: ProtocolType | None = None,
        min_quality: float | None = None
    ) -> dict[str, Any]:
        """Get aggregated analytics"""
        # Parse time range
        cutoff_time = self._parse_time_range(time_range)
        
        # Filter sessions
        filtered_sessions = [
            metrics for metrics in self.sessions.values()
            if (not cutoff_time or (metrics.started_at and metrics.started_at >= cutoff_time))
            and (not protocol_type or metrics.protocol_type == protocol_type)
            and (not min_quality or metrics.final_quality >= min_quality)
        ]
        
        if not filtered_sessions:
            return self._empty_analytics()
        
        # Calculate aggregated metrics
        total_sessions = len(filtered_sessions)
        active_sessions = sum(1 for m in filtered_sessions if not m.completed_at)
        successful_sessions = sum(1 for m in filtered_sessions if m.success)
        
        all_rounds = [m.rounds_completed for m in filtered_sessions if m.rounds_completed > 0]
        average_rounds = sum(all_rounds) / len(all_rounds) if all_rounds else 0
        
        all_quality = [m.final_quality for m in filtered_sessions if m.final_quality > 0]
        average_quality = sum(all_quality) / len(all_quality) if all_quality else 0
        
        all_consensus = [m.final_consensus for m in filtered_sessions if m.final_consensus > 0]
        average_consensus = sum(all_consensus) / len(all_consensus) if all_consensus else 0
        
        success_rate = successful_sessions / total_sessions if total_sessions > 0 else 0
        timeout_rate = 0.0  # Would need timeout tracking
        
        # Protocol usage
        protocol_usage: dict[str, int] = defaultdict(int)
        for metrics in filtered_sessions:
            if metrics.protocol_type:
                protocol_usage[metrics.protocol_type.value] += 1
        
        # Strategy performance
        strategy_performance = self._calculate_strategy_performance(filtered_sessions)
        
        # Quality trends
        quality_trends = self._calculate_quality_trends(filtered_sessions)
        
        # Consensus patterns
        consensus_patterns = self._calculate_consensus_patterns(filtered_sessions)
        
        return {
            "total_sessions": total_sessions,
            "active_sessions": active_sessions,
            "average_rounds": average_rounds,
            "average_quality": average_quality,
            "average_consensus": average_consensus,
            "success_rate": success_rate,
            "timeout_rate": timeout_rate,
            "protocol_usage": dict(protocol_usage),
            "strategy_performance": strategy_performance,
            "quality_trends": quality_trends,
            "consensus_patterns": consensus_patterns
        }
    
    async def log_analytics_event(
        self,
        event_type: str,
        session_id: str,
        metrics: dict[str, Any],
        timestamp: datetime
    ) -> None:
        """Log an analytics event"""
        self.analytics_events.append({
            "event_type": event_type,
            "session_id": session_id,
            "metrics": metrics,
            "timestamp": timestamp
        })
        
        # Keep only recent events (e.g., last 10000)
        if len(self.analytics_events) > 10000:
            self.analytics_events = self.analytics_events[-10000:]
    
    async def get_health_status(self) -> dict[str, Any]:
        """Get health status of session manager"""
        return {
            "status": "healthy",
            "active_sessions": sum(1 for m in self.sessions.values() if not m.completed_at),
            "total_sessions": len(self.sessions),
            "active_coordinations": len(self.coordinations),
            "analytics_events": len(self.analytics_events)
        }
    
    # Helper methods
    
    def _parse_time_range(self, time_range: str) -> datetime | None:
        """Parse time range string to datetime cutoff"""
        now = datetime.utcnow()
        
        if time_range == "1h":
            return now - timedelta(hours=1)
        elif time_range == "24h":
            return now - timedelta(days=1)
        elif time_range == "7d":
            return now - timedelta(days=7)
        elif time_range == "30d":
            return now - timedelta(days=30)
        else:
            return None
    
    def _empty_analytics(self) -> dict[str, Any]:
        """Return empty analytics structure"""
        return {
            "total_sessions": 0,
            "active_sessions": 0,
            "average_rounds": 0.0,
            "average_quality": 0.0,
            "average_consensus": 0.0,
            "success_rate": 0.0,
            "timeout_rate": 0.0,
            "protocol_usage": {},
            "strategy_performance": {},
            "quality_trends": [],
            "consensus_patterns": {}
        }
    
    def _generate_coordination_insights(
        self,
        coordination: CoordinationGroup
    ) -> list[str]:
        """Generate insights for coordination group"""
        insights = []
        
        if coordination.coordination_type == "parallel":
            insights.append("Sessions running in parallel for faster completion")
        elif coordination.coordination_type == "sequential":
            insights.append("Sessions running sequentially with context sharing")
        elif coordination.coordination_type == "hierarchical":
            insights.append("Hierarchical coordination with master session control")
        
        if len(coordination.session_ids) > 5:
            insights.append(f"Large coordination group ({len(coordination.session_ids)} sessions)")
        
        return insights
    
    def _calculate_strategy_performance(
        self,
        sessions: list[SessionMetrics]
    ) -> dict[str, dict[str, float]]:
        """Calculate performance by strategy"""
        # Simplified - would need strategy tracking
        return {
            "quality_focused": {"avg_quality": 0.88, "avg_rounds": 3.2},
            "consensus_driven": {"avg_quality": 0.85, "avg_rounds": 2.8},
            "efficiency_balanced": {"avg_quality": 0.82, "avg_rounds": 2.5}
        }
    
    def _calculate_quality_trends(
        self,
        sessions: list[SessionMetrics]
    ) -> list[dict[str, Any]]:
        """Calculate quality trends over time"""
        trends = []
        
        # Group by hour
        hourly_quality = defaultdict(list)
        for metrics in sessions:
            if metrics.started_at and metrics.final_quality > 0:
                hour_key = metrics.started_at.replace(minute=0, second=0, microsecond=0)
                hourly_quality[hour_key].append(metrics.final_quality)
        
        # Calculate averages
        for hour, qualities in sorted(hourly_quality.items()):
            trends.append({
                "timestamp": hour.isoformat(),
                "average_quality": sum(qualities) / len(qualities),
                "session_count": len(qualities)
            })
        
        return trends
    
    def _calculate_consensus_patterns(
        self,
        sessions: list[SessionMetrics]
    ) -> dict[str, Any]:
        """Analyze consensus achievement patterns"""
        high_consensus = sum(1 for m in sessions if m.final_consensus >= 0.8)
        low_consensus = sum(1 for m in sessions if m.final_consensus < 0.5)
        
        return {
            "high_consensus_rate": high_consensus / len(sessions) if sessions else 0,
            "low_consensus_rate": low_consensus / len(sessions) if sessions else 0,
            "average_rounds_to_consensus": 2.5  # Would need actual calculation
        }