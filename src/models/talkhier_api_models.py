"""
TalkHier Protocol API Models

This module defines request and response models for the TalkHier Protocol API,
implementing structured dialogue patterns from "Talk Structurally, Act Hierarchically" research.

The API enables multi-round refinement sessions with consensus building and quality assurance
through structured communication between supervisors and workers.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ================================
# Enums and Constants
# ================================

class SessionStatus(StrEnum):
    """TalkHier session lifecycle states"""
    INITIALIZING = "initializing"
    ACTIVE = "active"
    REFINING = "refining"
    CONSENSUS_CHECKING = "consensus_checking"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class RefinementStrategy(StrEnum):
    """Refinement strategies for multi-round dialogue"""
    QUALITY_FOCUSED = "quality_focused"      # Maximize quality through iterations
    CONSENSUS_DRIVEN = "consensus_driven"     # Focus on agreement between agents
    EFFICIENCY_BALANCED = "efficiency_balanced"  # Balance quality and rounds
    DEBATE_INTENSIVE = "debate_intensive"     # Encourage diverse perspectives
    RAPID_CONVERGENCE = "rapid_convergence"   # Minimize refinement rounds


class ConsensusType(StrEnum):
    """Types of consensus mechanisms"""
    MAJORITY = "majority"              # Simple majority agreement
    WEIGHTED = "weighted"              # Confidence-weighted consensus
    UNANIMOUS = "unanimous"            # Full agreement required
    THRESHOLD = "threshold"            # Quality threshold based
    HIERARCHICAL = "hierarchical"      # Supervisor-weighted consensus


class MessageRole(StrEnum):
    """Roles in TalkHier communication"""
    SUPERVISOR = "supervisor"
    WORKER = "worker"
    COORDINATOR = "coordinator"
    OBSERVER = "observer"


class ProtocolType(StrEnum):
    """Available TalkHier protocol variants"""
    STANDARD = "standard"              # Default multi-round refinement
    FAST_TRACK = "fast_track"         # Reduced rounds for simple tasks
    DEEP_ANALYSIS = "deep_analysis"   # Extended refinement for complex tasks
    COLLABORATIVE = "collaborative"    # Equal weight to all participants
    SUPERVISED = "supervised"          # Supervisor-guided refinement


# ================================
# Request Models
# ================================

class TalkHierSessionRequest(BaseModel):
    """Request to start a new TalkHier refinement session"""
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "query": "Analyze the impact of AI on employment with supporting data",
            "domains": ["research", "analytics"],
            "participants": ["literature-review", "data-analysis", "synthesis"],
            "protocol_type": "standard",
            "refinement_strategy": "quality_focused",
            "max_rounds": 3,
            "quality_threshold": 0.85,
            "consensus_type": "weighted"
        }
    })
    
    query: str = Field(..., min_length=1, description="Query to refine through structured dialogue")
    domains: list[str] = Field(default_factory=list, description="Relevant domains for the query")
    participants: list[str] | None = Field(None, description="Specific agents to participate")
    supervisor_type: str | None = Field(None, description="Type of supervisor to coordinate")
    
    # Protocol configuration
    protocol_type: ProtocolType = Field(ProtocolType.STANDARD, description="TalkHier protocol variant")
    refinement_strategy: RefinementStrategy = Field(
        RefinementStrategy.QUALITY_FOCUSED,
        description="Strategy for multi-round refinement"
    )
    
    # Refinement parameters
    max_rounds: int = Field(3, ge=1, le=10, description="Maximum refinement rounds")
    min_rounds: int = Field(1, ge=1, le=5, description="Minimum refinement rounds")
    quality_threshold: float = Field(0.85, ge=0.0, le=1.0, description="Target quality score")
    consensus_type: ConsensusType = Field(ConsensusType.WEIGHTED, description="Consensus mechanism")
    consensus_threshold: float = Field(0.8, ge=0.5, le=1.0, description="Consensus agreement threshold")
    
    # Optional parameters
    timeout_seconds: int | None = Field(300, ge=30, le=3600, description="Session timeout")
    enable_debate: bool = Field(True, description="Allow agents to debate and disagree")
    require_evidence: bool = Field(True, description="Require supporting evidence in responses")
    
    @field_validator('min_rounds')
    def validate_min_rounds(cls, v: int, info: Any) -> int:  # noqa: N805
        if 'max_rounds' in info.data and v > info.data['max_rounds']:
            raise ValueError("min_rounds cannot exceed max_rounds")
        return v


class RefinementRoundRequest(BaseModel):
    """Request to execute a refinement round in an active session"""
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "round_number": 2,
            "previous_result": {"content": "Initial analysis...", "confidence": 0.75},
            "refinement_focus": "Strengthen evidence and improve clarity",
            "participant_feedback": {
                "literature-review": "Need more recent sources",
                "synthesis": "Conclusion needs strengthening"
            }
        }
    })
    
    round_number: int = Field(..., ge=1, description="Current round number")
    previous_result: dict[str, Any] | None = Field(None, description="Result from previous round")
    refinement_focus: str | None = Field(None, description="Specific areas to refine")
    participant_feedback: dict[str, str] | None = Field(
        None,
        description="Feedback from each participant"
    )
    force_consensus: bool = Field(False, description="Force consensus in this round")


class ConsensusCheckRequest(BaseModel):
    """Request to check consensus status in a session"""
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "round_results": [
                {"agent": "literature-review", "confidence": 0.87, "content": "..."},
                {"agent": "synthesis", "confidence": 0.91, "content": "..."}
            ],
            "check_quality": True,
            "include_minority_report": True
        }
    })
    
    round_results: list[dict[str, Any]] = Field(..., description="Results from current round")
    check_quality: bool = Field(True, description="Include quality assessment in consensus")
    include_minority_report: bool = Field(False, description="Include dissenting opinions")


class SessionCloseRequest(BaseModel):
    """Request to close a TalkHier session"""
    
    reason: str | None = Field(None, description="Reason for closing session")
    save_transcript: bool = Field(True, description="Save session transcript")
    generate_summary: bool = Field(True, description="Generate session summary")


class ProtocolValidationRequest(BaseModel):
    """Request to validate TalkHier communication structure"""
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "messages": [
                {"role": "supervisor", "content": "Initial task...", "timestamp": "2025-01-08T10:00:00Z"},
                {"role": "worker", "content": "Response...", "timestamp": "2025-01-08T10:01:00Z"}
            ],
            "expected_protocol": "standard",
            "check_timing": True
        }
    })
    
    messages: list[dict[str, Any]] = Field(..., description="Messages to validate")
    expected_protocol: ProtocolType | None = Field(None, description="Expected protocol type")
    check_timing: bool = Field(True, description="Validate message timing")
    check_structure: bool = Field(True, description="Validate dialogue structure")


# ================================
# Response Models
# ================================

class ParticipantInfo(BaseModel):
    """Information about a session participant"""
    
    agent_id: str = Field(..., description="Unique agent identifier")
    agent_type: str = Field(..., description="Type of agent")
    role: MessageRole = Field(..., description="Role in the session")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="Current confidence level")
    rounds_participated: int = Field(0, ge=0, description="Number of rounds participated")
    quality_scores: list[float] = Field(default_factory=list, description="Quality scores per round")


class RefinementRound(BaseModel):
    """Details of a refinement round"""
    
    round_number: int = Field(..., ge=1, description="Round sequence number")
    status: str = Field(..., description="Round status")
    started_at: datetime = Field(..., description="Round start time")
    completed_at: datetime | None = Field(None, description="Round completion time")
    
    participants: list[str] = Field(..., description="Participating agents")
    messages: list[dict[str, Any]] = Field(default_factory=list, description="Round messages")
    
    quality_score: float = Field(0.0, ge=0.0, le=1.0, description="Round quality score")
    consensus_score: float = Field(0.0, ge=0.0, le=1.0, description="Consensus level")
    refinement_delta: float = Field(0.0, description="Quality improvement from previous round")
    
    result: dict[str, Any] | None = Field(None, description="Round result")


class ConsensusResult(BaseModel):
    """Result of consensus checking"""
    
    has_consensus: bool = Field(..., description="Whether consensus was achieved")
    consensus_type: ConsensusType = Field(..., description="Type of consensus checked")
    consensus_score: float = Field(..., ge=0.0, le=1.0, description="Consensus strength")
    
    agreement_matrix: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description="Pairwise agreement scores between participants"
    )
    
    quality_scores: dict[str, float] = Field(
        default_factory=dict,
        description="Quality scores per participant"
    )
    
    minority_reports: list[dict[str, Any]] | None = Field(
        None,
        description="Dissenting opinions if requested"
    )
    
    recommendation: str = Field(..., description="Recommended next action")
    reasoning: str = Field(..., description="Explanation of consensus result")


class TalkHierSessionResponse(BaseModel):
    """Response for session creation"""
    
    session_id: str = Field(..., description="Unique session identifier")
    status: SessionStatus = Field(..., description="Current session status")
    created_at: datetime = Field(..., description="Session creation time")
    
    protocol_type: ProtocolType = Field(..., description="Active protocol type")
    refinement_strategy: RefinementStrategy = Field(..., description="Active refinement strategy")
    
    participants: list[ParticipantInfo] = Field(..., description="Session participants")
    supervisor: str | None = Field(None, description="Coordinating supervisor")
    
    max_rounds: int = Field(..., description="Maximum refinement rounds")
    quality_threshold: float = Field(..., description="Target quality threshold")
    
    websocket_url: str | None = Field(None, description="WebSocket URL for live updates")
    estimated_duration_seconds: int = Field(..., description="Estimated session duration")


class SessionStatusResponse(BaseModel):
    """Response for session status query"""
    
    session_id: str = Field(..., description="Session identifier")
    status: SessionStatus = Field(..., description="Current status")
    
    current_round: int = Field(0, description="Current round number")
    total_rounds: int = Field(0, description="Total rounds completed")
    
    rounds: list[RefinementRound] = Field(default_factory=list, description="Round history")
    
    current_quality: float = Field(0.0, ge=0.0, le=1.0, description="Current quality score")
    current_consensus: float = Field(0.0, ge=0.0, le=1.0, description="Current consensus level")
    
    elapsed_seconds: int = Field(0, description="Time elapsed")
    remaining_seconds: int | None = Field(None, description="Time remaining")
    
    last_update: datetime = Field(..., description="Last update timestamp")


class RefinementRoundResponse(BaseModel):
    """Response for refinement round execution"""
    
    session_id: str = Field(..., description="Session identifier")
    round_number: int = Field(..., description="Completed round number")
    
    round_status: str = Field(..., description="Round completion status")
    duration_ms: int = Field(..., description="Round duration in milliseconds")
    
    participant_responses: dict[str, dict[str, Any]] = Field(
        ...,
        description="Responses from each participant"
    )
    
    aggregated_result: dict[str, Any] = Field(..., description="Aggregated round result")
    
    quality_score: float = Field(..., ge=0.0, le=1.0, description="Round quality score")
    consensus_score: float = Field(..., ge=0.0, le=1.0, description="Round consensus score")
    improvement_delta: float = Field(..., description="Improvement from previous round")
    
    continue_refinement: bool = Field(..., description="Whether to continue refinement")
    refinement_suggestion: str | None = Field(None, description="Suggested refinement focus")


class SessionCloseResponse(BaseModel):
    """Response for session closure"""
    
    session_id: str = Field(..., description="Closed session identifier")
    final_status: SessionStatus = Field(..., description="Final session status")
    
    total_rounds: int = Field(..., description="Total rounds completed")
    total_duration_seconds: int = Field(..., description="Total session duration")
    
    final_result: dict[str, Any] | None = Field(None, description="Final refined result")
    final_quality: float = Field(0.0, ge=0.0, le=1.0, description="Final quality score")
    final_consensus: float = Field(0.0, ge=0.0, le=1.0, description="Final consensus score")
    
    transcript_url: str | None = Field(None, description="URL to session transcript")
    summary: dict[str, Any] | None = Field(None, description="Session summary")
    
    performance_metrics: dict[str, Any] = Field(
        default_factory=dict,
        description="Session performance metrics"
    )


class ProtocolListResponse(BaseModel):
    """Response listing available TalkHier protocols"""
    
    protocols: list[dict[str, Any]] = Field(..., description="Available protocol configurations")
    default_protocol: ProtocolType = Field(..., description="Default protocol type")
    
    recommended_protocols: dict[str, ProtocolType] = Field(
        default_factory=dict,
        description="Recommended protocols by query type"
    )


class ValidationResponse(BaseModel):
    """Response for protocol validation"""
    
    is_valid: bool = Field(..., description="Whether communication follows protocol")
    protocol_detected: ProtocolType | None = Field(None, description="Detected protocol type")
    
    structural_errors: list[str] = Field(default_factory=list, description="Structure violations")
    timing_errors: list[str] = Field(default_factory=list, description="Timing violations")
    role_errors: list[str] = Field(default_factory=list, description="Role violations")
    
    quality_assessment: float | None = Field(None, description="Communication quality score")
    recommendations: list[str] = Field(default_factory=list, description="Improvement recommendations")


class AnalyticsResponse(BaseModel):
    """Response for TalkHier protocol analytics"""
    
    total_sessions: int = Field(..., description="Total sessions processed")
    active_sessions: int = Field(..., description="Currently active sessions")
    
    average_rounds: float = Field(..., description="Average rounds per session")
    average_quality: float = Field(..., description="Average final quality score")
    average_consensus: float = Field(..., description="Average consensus achievement")
    
    success_rate: float = Field(..., ge=0.0, le=1.0, description="Session success rate")
    timeout_rate: float = Field(..., ge=0.0, le=1.0, description="Session timeout rate")
    
    protocol_usage: dict[str, int] = Field(
        default_factory=dict,
        description="Usage count by protocol type"
    )
    
    strategy_performance: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description="Performance metrics by refinement strategy"
    )
    
    quality_trends: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Quality score trends over time"
    )
    
    consensus_patterns: dict[str, Any] = Field(
        default_factory=dict,
        description="Consensus achievement patterns"
    )


# ================================
# WebSocket Event Models
# ================================

class TalkHierWebSocketEvent(BaseModel):
    """Base model for TalkHier WebSocket events"""
    
    event_type: str = Field(..., description="Type of event")
    session_id: str = Field(..., description="Session identifier")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Event timestamp")
    data: dict[str, Any] = Field(..., description="Event-specific data")


class RoundStartedEvent(TalkHierWebSocketEvent):
    """Event when a refinement round starts"""
    
    event_type: Literal["round_started"] = "round_started"
    round_number: int = Field(..., description="Starting round number")
    participants: list[str] = Field(..., description="Round participants")


class MessageExchangeEvent(TalkHierWebSocketEvent):
    """Event for message exchange during refinement"""
    
    event_type: Literal["message_exchange"] = "message_exchange"
    sender: str = Field(..., description="Message sender")
    role: MessageRole = Field(..., description="Sender role")
    content_preview: str = Field(..., description="Message preview")
    confidence: float = Field(..., description="Sender confidence")


class ConsensusUpdateEvent(TalkHierWebSocketEvent):
    """Event for consensus status updates"""
    
    event_type: Literal["consensus_update"] = "consensus_update"
    current_consensus: float = Field(..., description="Current consensus level")
    trending_direction: Literal["improving", "declining", "stable"] = Field(
        ...,
        description="Consensus trend"
    )


class QualityUpdateEvent(TalkHierWebSocketEvent):
    """Event for quality score updates"""
    
    event_type: Literal["quality_update"] = "quality_update"
    current_quality: float = Field(..., description="Current quality score")
    improvement_delta: float = Field(..., description="Change from previous")
    target_reached: bool = Field(..., description="Whether target quality reached")


class SessionCompletedEvent(TalkHierWebSocketEvent):
    """Event when session completes"""
    
    event_type: Literal["session_completed"] = "session_completed"
    final_quality: float = Field(..., description="Final quality score")
    final_consensus: float = Field(..., description="Final consensus score")
    total_rounds: int = Field(..., description="Total rounds completed")
    success: bool = Field(..., description="Whether session succeeded")


# ================================
# Interactive Session Models
# ================================

class InteractiveMessage(BaseModel):
    """Message in an interactive TalkHier session"""
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "content": "I believe we should focus on recent AI developments",
            "role": "worker",
            "agent_id": "literature-review",
            "confidence": 0.85,
            "supporting_evidence": ["Reference 1", "Reference 2"]
        }
    })
    
    content: str = Field(..., min_length=1, description="Message content")
    role: MessageRole = Field(..., description="Sender role")
    agent_id: str | None = Field(None, description="Sender agent ID")
    confidence: float = Field(0.5, ge=0.0, le=1.0, description="Message confidence")
    supporting_evidence: list[str] | None = Field(None, description="Supporting evidence")
    in_response_to: str | None = Field(None, description="Message being responded to")


class InteractiveCommand(BaseModel):
    """Command for interactive session control"""
    
    command: Literal["pause", "resume", "skip_round", "force_consensus", "abort"] = Field(
        ...,
        description="Session control command"
    )
    reason: str | None = Field(None, description="Command reason")
    parameters: dict[str, Any] | None = Field(None, description="Command parameters")


# ================================
# Coordination Models
# ================================

class CoordinationRequest(BaseModel):
    """Request for multi-session coordination"""
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "session_ids": ["session-1", "session-2"],
            "coordination_type": "sequential",
            "share_context": True,
            "aggregate_results": True
        }
    })
    
    session_ids: list[str] = Field(..., min_length=2, description="Sessions to coordinate")
    coordination_type: Literal["sequential", "parallel", "hierarchical"] = Field(
        ...,
        description="Coordination pattern"
    )
    share_context: bool = Field(True, description="Share context between sessions")
    aggregate_results: bool = Field(True, description="Aggregate final results")
    master_session_id: str | None = Field(None, description="Master session for hierarchical")


class CoordinationStatus(BaseModel):
    """Status of coordinated sessions"""
    
    coordination_id: str = Field(..., description="Coordination identifier")
    session_statuses: dict[str, SessionStatus] = Field(..., description="Individual session statuses")
    overall_progress: float = Field(..., ge=0.0, le=1.0, description="Overall progress")
    aggregated_quality: float = Field(..., ge=0.0, le=1.0, description="Aggregated quality score")
    estimated_completion: datetime | None = Field(None, description="Estimated completion time")
    coordination_insights: list[str] = Field(default_factory=list, description="Coordination insights")