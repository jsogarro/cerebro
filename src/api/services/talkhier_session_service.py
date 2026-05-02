"""
TalkHier Session Service

This service manages TalkHier protocol sessions, implementing structured dialogue
and multi-round refinement patterns from "Talk Structurally, Act Hierarchically" research.

Core responsibilities:
- Session lifecycle management
- Multi-round refinement coordination
- Consensus tracking and quality assessment
- Integration with existing TalkHier protocol
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.agents.communication.talkhier_message import TalkHierMessage
from src.agents.supervisors.base_supervisor import BaseSupervisor
from src.agents.supervisors.supervisor_factory import (
    SupervisorFactory,
)
from src.ai_brain.integration.masr_supervisor_bridge import MASRSupervisorBridge
from src.ai_brain.router.masr import MASRouter, RoutingDecision
from src.api.services.talkhier_consensus_evaluator import TalkHierConsensusEvaluator
from src.api.services.talkhier_round_executor import TalkHierRoundExecutor
from src.api.services.talkhier_session_coordinator import TalkHierSessionCoordinator
from src.api.services.talkhier_state_manager import TalkHierStateManager
from src.models.talkhier_api_models import (
    ConsensusCheckRequest,
    ConsensusResult,
    ConsensusType,
    MessageRole,
    ParticipantInfo,
    ProtocolType,
    ProtocolValidationRequest,
    RefinementRound,
    RefinementRoundRequest,
    RefinementRoundResponse,
    RefinementStrategy,
    SessionCloseRequest,
    SessionCloseResponse,
    SessionStatus,
    SessionStatusResponse,
    TalkHierSessionRequest,
    TalkHierSessionResponse,
    ValidationResponse,
)

logger = logging.getLogger(__name__)


@dataclass
class TalkHierSession:
    """Internal representation of a TalkHier session"""
    
    session_id: str
    query: str
    domains: list[str]
    status: SessionStatus
    created_at: datetime
    
    # Configuration
    protocol_type: ProtocolType
    refinement_strategy: RefinementStrategy
    max_rounds: int
    min_rounds: int
    quality_threshold: float
    consensus_type: ConsensusType
    consensus_threshold: float
    timeout_seconds: int
    
    # Participants
    participants: list[ParticipantInfo]
    supervisor: BaseSupervisor | None = None
    supervisor_type: str | None = None
    
    # State
    current_round: int = 0
    rounds: list[RefinementRound] = field(default_factory=list)
    messages: list[TalkHierMessage] = field(default_factory=list)
    
    # Results
    current_result: dict[str, Any] | None = None
    current_quality: float = 0.0
    current_consensus: float = 0.0
    
    # Timing
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_update: datetime = field(default_factory=lambda: datetime.now(UTC))
    
    # WebSocket
    websocket_connections: list[str] = field(default_factory=list)


class TalkHierSessionService:
    """
    Service managing TalkHier protocol sessions
    
    This service coordinates structured dialogue sessions following the
    TalkHier protocol, enabling multi-round refinement with consensus building.
    """
    
    def __init__(self) -> None:
        self.state_manager = TalkHierStateManager()
        self.round_executor = TalkHierRoundExecutor()
        self.consensus_evaluator = TalkHierConsensusEvaluator()
        self.sessions: dict[str, TalkHierSession] = self.state_manager.sessions
        self.consensus_builder = self.consensus_evaluator.consensus_builder
        self.supervisor_factory = SupervisorFactory()
        self.masr_bridge = MASRSupervisorBridge()
        self.masr_router = MASRouter()
        self.session_coordinator = TalkHierSessionCoordinator(
            self.supervisor_factory,
            self.masr_bridge,
            self.masr_router,
        )
        
        # Protocol configurations
        self.protocol_configs = self._initialize_protocol_configs()
        
        # Performance tracking
        self.session_metrics = self.state_manager.session_metrics
        
        # Background tasks
        self.cleanup_task = None
        
    def _initialize_protocol_configs(self) -> dict[ProtocolType, dict[str, Any]]:
        """Initialize protocol configurations"""
        return self.session_coordinator.initialize_protocol_configs()
    
    async def create_session(
        self,
        request: TalkHierSessionRequest
    ) -> TalkHierSessionResponse:
        """
        Create a new TalkHier refinement session
        
        Args:
            request: Session creation request
            
        Returns:
            Session creation response with session ID and configuration
        """
        session_id = str(uuid.uuid4())
        
        # Get protocol configuration
        protocol_config = self.protocol_configs[request.protocol_type]
        
        # Route query through MASR for intelligent supervisor selection
        routing_decision = await self._get_routing_decision(
            request.query,
            request.domains
        )
        
        # Create supervisor based on routing decision
        supervisor = await self._create_supervisor(
            routing_decision,
            request.supervisor_type
        )
        
        # Determine participants
        participants = await self._determine_participants(
            request.participants,
            routing_decision,
            supervisor
        )
        
        # Create session
        session = TalkHierSession(
            session_id=session_id,
            query=request.query,
            domains=request.domains,
            status=SessionStatus.INITIALIZING,
            created_at=datetime.now(UTC),
            protocol_type=request.protocol_type,
            refinement_strategy=request.refinement_strategy,
            max_rounds=request.max_rounds,
            min_rounds=request.min_rounds,
            quality_threshold=request.quality_threshold,
            consensus_type=request.consensus_type,
            consensus_threshold=request.consensus_threshold,
            timeout_seconds=request.timeout_seconds or 300,
            participants=participants,
            supervisor=supervisor,
            supervisor_type=self._resolve_supervisor_type(
                routing_decision,
                request.supervisor_type,
            ),
        )
        
        # Store session
        self.state_manager.store_session(session_id, session)
        
        # Initialize session metrics
        self.state_manager.initialize_metrics(session_id)
        
        # Update status to active
        session.status = SessionStatus.ACTIVE
        session.started_at = datetime.now(UTC)
        
        # Calculate estimated duration
        estimated_duration = self._estimate_duration(
            request.max_rounds,
            len(participants),
            protocol_config
        )
        
        # Generate WebSocket URL
        websocket_url = f"/api/v1/talkhier/sessions/{session_id}/live"
        
        logger.info(f"Created TalkHier session {session_id} with {len(participants)} participants")
        
        return TalkHierSessionResponse(
            session_id=session_id,
            status=session.status,
            created_at=session.created_at,
            protocol_type=session.protocol_type,
            refinement_strategy=session.refinement_strategy,
            participants=session.participants,
            supervisor=session.supervisor_type,
            max_rounds=session.max_rounds,
            quality_threshold=session.quality_threshold,
            websocket_url=websocket_url,
            estimated_duration_seconds=estimated_duration
        )
    
    async def get_session_status(self, session_id: str) -> SessionStatusResponse:
        """
        Get current status of a TalkHier session
        
        Args:
            session_id: Session identifier
            
        Returns:
            Current session status and history
        """
        session = self._get_session(session_id)
        
        # Calculate elapsed and remaining time
        elapsed_seconds = 0
        remaining_seconds = None
        
        if session.started_at:
            elapsed_seconds = int((datetime.now(UTC) - session.started_at).total_seconds())
            remaining_seconds = max(0, session.timeout_seconds - elapsed_seconds)
        
        return SessionStatusResponse(
            session_id=session_id,
            status=session.status,
            current_round=session.current_round,
            total_rounds=len(session.rounds),
            rounds=session.rounds,
            current_quality=session.current_quality,
            current_consensus=session.current_consensus,
            elapsed_seconds=elapsed_seconds,
            remaining_seconds=remaining_seconds,
            last_update=session.last_update
        )
    
    async def execute_refinement_round(
        self,
        session_id: str,
        request: RefinementRoundRequest
    ) -> RefinementRoundResponse:
        """
        Execute a refinement round in an active session
        
        Args:
            session_id: Session identifier
            request: Refinement round request
            
        Returns:
            Round execution results
        """
        session = self._get_session(session_id)

        response = await self.round_executor.execute_refinement_round(
            session_id,
            session,
            request,
            self.state_manager,
        )

        logger.info(f"Completed refinement round {request.round_number} for session {session_id}")
        logger.info(
            f"Quality: {response.quality_score:.2f}, "
            f"Consensus: {response.consensus_score:.2f}, "
            f"Delta: {response.improvement_delta:+.2f}"
        )

        return response
    
    async def check_consensus(
        self,
        session_id: str,
        request: ConsensusCheckRequest
    ) -> ConsensusResult:
        """
        Check consensus status in a session
        
        Args:
            session_id: Session identifier
            request: Consensus check request
            
        Returns:
            Consensus analysis result
        """
        session = self._get_session(session_id)

        result = await self.consensus_evaluator.check_consensus(
            session_id,
            session,
            request,
        )

        logger.info(
            f"Consensus check for session {session_id}: "
            f"{result.has_consensus} (score: {result.consensus_score:.2f})"
        )

        return result
    
    async def close_session(
        self,
        session_id: str,
        request: SessionCloseRequest
    ) -> SessionCloseResponse:
        """
        Close a TalkHier session
        
        Args:
            session_id: Session identifier
            request: Close request
            
        Returns:
            Session closure summary
        """
        session = self._get_session(session_id)
        
        # Determine final status
        if session.status == SessionStatus.ACTIVE:
            if session.current_quality >= session.quality_threshold:
                final_status = SessionStatus.COMPLETED
            else:
                final_status = SessionStatus.CANCELLED
        else:
            final_status = session.status
        
        # Update session
        session.status = final_status
        session.completed_at = datetime.now(UTC)
        
        # Calculate total duration
        total_duration = 0
        if session.started_at and session.completed_at:
            total_duration = int((session.completed_at - session.started_at).total_seconds())
        
        # Generate transcript URL if requested
        transcript_url = None
        if request.save_transcript:
            transcript_url = await self._save_session_transcript(session)
        
        # Generate summary if requested
        summary = None
        if request.generate_summary:
            summary = await self._generate_session_summary(session)
        
        # Collect performance metrics
        performance_metrics = {
            "total_messages": self.session_metrics[session_id]["message_count"],
            "quality_progression": self.session_metrics[session_id]["quality_progression"],
            "consensus_progression": self.session_metrics[session_id]["consensus_progression"],
            "average_round_duration": total_duration / max(1, len(session.rounds)),
            "efficiency_score": self._calculate_efficiency_score(session)
        }
        
        # Clean up session (optionally keep for analytics)
        if not request.save_transcript:
            del self.sessions[session_id]
            del self.session_metrics[session_id]
        
        logger.info(f"Closed session {session_id} with status {final_status}")
        
        return SessionCloseResponse(
            session_id=session_id,
            final_status=final_status,
            total_rounds=len(session.rounds),
            total_duration_seconds=total_duration,
            final_result=session.current_result,
            final_quality=session.current_quality,
            final_consensus=session.current_consensus,
            transcript_url=transcript_url,
            summary=summary,
            performance_metrics=performance_metrics
        )
    
    async def validate_protocol(
        self,
        request: ProtocolValidationRequest
    ) -> ValidationResponse:
        """
        Validate TalkHier communication structure
        
        Args:
            request: Validation request
            
        Returns:
            Validation results and recommendations
        """
        structural_errors = []
        timing_errors = []
        role_errors = []
        
        # Detect protocol type
        protocol_detected = await self._detect_protocol_type(request.messages)
        
        # Validate structure
        if request.check_structure:
            structural_errors = await self._validate_message_structure(
                request.messages,
                protocol_detected
            )
        
        # Validate timing
        if request.check_timing:
            timing_errors = await self._validate_message_timing(
                request.messages
            )
        
        # Validate roles
        role_errors = await self._validate_message_roles(
            request.messages,
            protocol_detected
        )
        
        # Calculate quality assessment
        quality_assessment = await self._assess_communication_quality(
            request.messages
        )
        
        # Generate recommendations
        recommendations = await self._generate_protocol_recommendations(
            structural_errors,
            timing_errors,
            role_errors,
            quality_assessment
        )
        
        # Determine overall validity
        is_valid = not (structural_errors or timing_errors or role_errors)
        
        return ValidationResponse(
            is_valid=is_valid,
            protocol_detected=protocol_detected,
            structural_errors=structural_errors,
            timing_errors=timing_errors,
            role_errors=role_errors,
            quality_assessment=quality_assessment,
            recommendations=recommendations
        )
    
    # =============================
    # Helper Methods
    # =============================
    
    def _get_session(self, session_id: str) -> TalkHierSession:
        """Get session by ID with validation"""
        session = self.state_manager.get_session(session_id)
        if not isinstance(session, TalkHierSession):
            raise ValueError(f"Session {session_id} has invalid state")
        return session
    
    async def _get_routing_decision(
        self,
        query: str,
        domains: list[str]
    ) -> RoutingDecision:
        """Get routing decision from MASR"""
        return await self.session_coordinator.get_routing_decision(query, domains)
    
    async def _create_supervisor(
        self,
        routing_decision: RoutingDecision,
        requested_type: str | None
    ) -> BaseSupervisor | None:
        """Create supervisor based on routing decision"""
        return await self.session_coordinator.create_supervisor(
            routing_decision,
            requested_type,
        )

    def _resolve_supervisor_type(
        self,
        routing_decision: RoutingDecision,
        requested_type: str | None,
    ) -> str:
        """Resolve a concrete supervisor type from routing output or test doubles."""
        return self.session_coordinator.resolve_supervisor_type(
            routing_decision,
            requested_type,
        )
    
    async def _determine_participants(
        self,
        requested: list[str] | None,
        routing_decision: RoutingDecision,
        supervisor: BaseSupervisor | None
    ) -> list[ParticipantInfo]:
        """Determine session participants"""
        return await self.session_coordinator.determine_participants(
            requested,
            routing_decision,
            supervisor,
        )
    
    def _estimate_duration(
        self,
        max_rounds: int,
        participant_count: int,
        protocol_config: dict[str, Any]
    ) -> int:
        """Estimate session duration in seconds"""
        return self.session_coordinator.estimate_duration(
            max_rounds,
            participant_count,
            protocol_config,
        )
    
    async def _execute_supervisor_refinement(
        self,
        session: TalkHierSession,
        request: RefinementRoundRequest,
        round_record: RefinementRound
    ) -> dict[str, Any]:
        """Execute refinement through supervisor"""
        return await self.round_executor.execute_supervisor_refinement(
            session,
            request,
            round_record,
        )
    
    async def _execute_direct_refinement(
        self,
        session: TalkHierSession,
        request: RefinementRoundRequest,
        round_record: RefinementRound
    ) -> dict[str, dict[str, Any]]:
        """Execute direct agent refinement without supervisor"""
        return await self.round_executor.execute_direct_refinement(
            session,
            request,
            round_record,
        )
    
    async def _aggregate_refinement_results(
        self,
        responses: dict[str, dict[str, Any]],
        strategy: RefinementStrategy
    ) -> dict[str, Any]:
        """Aggregate participant responses based on strategy"""
        return await self.round_executor.aggregate_refinement_results(responses, strategy)
    
    async def _calculate_quality_score(
        self,
        result: dict[str, Any],
        threshold: float
    ) -> float:
        """Calculate quality score for result"""
        return await self.round_executor.calculate_quality_score(result, threshold)
    
    async def _calculate_consensus_score(
        self,
        responses: dict[str, dict[str, Any]],
        consensus_type: ConsensusType
    ) -> float:
        """Calculate consensus score among responses"""
        return await self.round_executor.calculate_consensus_score(
            responses,
            consensus_type,
        )
    
    def _should_continue_refinement(self, session: TalkHierSession) -> bool:
        """Determine if refinement should continue"""
        return self.round_executor.should_continue_refinement(session)
    
    async def _generate_refinement_suggestion(
        self,
        session: TalkHierSession,
        responses: dict[str, dict[str, Any]],
        result: dict[str, Any]
    ) -> str:
        """Generate suggestion for next refinement round"""
        return await self.round_executor.generate_refinement_suggestion(
            session,
            responses,
            result,
        )
    
    async def _calculate_agreement_matrix(
        self,
        results: list[dict[str, Any]]
    ) -> dict[str, dict[str, float]]:
        """Calculate pairwise agreement between participants"""
        return await self.consensus_evaluator.calculate_agreement_matrix(results)
    
    async def _generate_minority_reports(
        self,
        results: list[dict[str, Any]],
        consensus_score: float
    ) -> list[dict[str, Any]]:
        """Generate minority opinion reports"""
        return await self.consensus_evaluator.generate_minority_reports(
            results,
            consensus_score,
        )
    
    def _generate_consensus_recommendation(
        self,
        has_consensus: bool,
        consensus_score: float,
        session: TalkHierSession
    ) -> str:
        """Generate recommendation based on consensus status"""
        return self.consensus_evaluator.generate_consensus_recommendation(
            has_consensus,
            consensus_score,
            session,
        )
    
    def _generate_consensus_reasoning(
        self,
        has_consensus: bool,
        consensus_score: float,
        agreement_matrix: dict[str, dict[str, float]],
        session: TalkHierSession
    ) -> str:
        """Generate reasoning for consensus result"""
        return self.consensus_evaluator.generate_consensus_reasoning(
            has_consensus,
            consensus_score,
            agreement_matrix,
            session,
        )
    
    async def _save_session_transcript(
        self,
        session: TalkHierSession
    ) -> str:
        """Save session transcript and return URL"""
        # In production, this would save to storage
        transcript_id = f"transcript_{session.session_id}"
        return f"/api/v1/talkhier/transcripts/{transcript_id}"
    
    async def _generate_session_summary(
        self,
        session: TalkHierSession
    ) -> dict[str, Any]:
        """Generate comprehensive session summary"""
        return {
            "session_id": session.session_id,
            "query": session.query,
            "total_rounds": len(session.rounds),
            "final_quality": session.current_quality,
            "final_consensus": session.current_consensus,
            "quality_progression": [r.quality_score for r in session.rounds],
            "consensus_progression": [r.consensus_score for r in session.rounds],
            "key_insights": await self._extract_key_insights(session),
            "participant_performance": await self._analyze_participant_performance(session)
        }
    
    async def _extract_key_insights(
        self,
        session: TalkHierSession
    ) -> list[str]:
        """Extract key insights from session"""
        insights = []
        
        # Quality achievement
        if session.current_quality >= session.quality_threshold:
            insights.append(f"Quality target achieved ({session.current_quality:.2f})")
        else:
            insights.append(f"Quality below target ({session.current_quality:.2f} < {session.quality_threshold:.2f})")
        
        # Consensus achievement
        if session.current_consensus >= session.consensus_threshold:
            insights.append(f"Strong consensus reached ({session.current_consensus:.2f})")
        else:
            insights.append(f"Limited consensus ({session.current_consensus:.2f})")
        
        # Efficiency
        if len(session.rounds) <= session.min_rounds + 1:
            insights.append("Efficient convergence achieved")
        elif len(session.rounds) >= session.max_rounds:
            insights.append("Maximum rounds utilized")
        
        return insights
    
    async def _analyze_participant_performance(
        self,
        session: TalkHierSession
    ) -> dict[str, dict[str, Any]]:
        """Analyze individual participant performance"""
        performance = {}
        
        for participant in session.participants:
            performance[participant.agent_id] = {
                "role": participant.role.value,
                "rounds_participated": participant.rounds_participated,
                "average_quality": (
                    sum(participant.quality_scores) / len(participant.quality_scores)
                    if participant.quality_scores else 0.0
                ),
                "final_confidence": participant.confidence
            }
        
        return performance
    
    def _calculate_efficiency_score(
        self,
        session: TalkHierSession
    ) -> float:
        """Calculate session efficiency score"""
        # Factors: rounds used vs min/max, quality achieved, time taken
        
        rounds_factor = 1.0 - (
            (len(session.rounds) - session.min_rounds) /
            max(1, session.max_rounds - session.min_rounds)
        )
        
        quality_factor = session.current_quality / session.quality_threshold
        
        time_factor = 1.0
        if session.started_at and session.completed_at:
            actual_duration = (session.completed_at - session.started_at).total_seconds()
            time_factor = min(1.0, session.timeout_seconds / max(1, actual_duration))
        
        efficiency = (rounds_factor * 0.3 + quality_factor * 0.5 + time_factor * 0.2)
        
        return min(1.0, efficiency)
    
    async def _detect_protocol_type(
        self,
        messages: list[dict[str, Any]]
    ) -> ProtocolType:
        """Detect protocol type from message patterns"""
        # Analyze message patterns to detect protocol
        # This is a simplified detection
        
        if len(messages) <= 2:
            return ProtocolType.FAST_TRACK
        elif len(messages) >= 10:
            return ProtocolType.DEEP_ANALYSIS
        
        # Check for supervisor messages
        has_supervisor = any(
            m.get("role") == MessageRole.SUPERVISOR.value
            for m in messages
        )
        
        if has_supervisor:
            return ProtocolType.SUPERVISED
        
        return ProtocolType.STANDARD
    
    async def _validate_message_structure(
        self,
        messages: list[dict[str, Any]],
        protocol: ProtocolType
    ) -> list[str]:
        """Validate message structure for protocol"""
        errors = []
        
        # Check required fields
        for i, msg in enumerate(messages):
            if "role" not in msg:
                errors.append(f"Message {i}: missing 'role' field")
            if "content" not in msg:
                errors.append(f"Message {i}: missing 'content' field")
        
        # Check protocol-specific requirements
        if protocol == ProtocolType.SUPERVISED:
            supervisor_msgs = [m for m in messages if m.get("role") == MessageRole.SUPERVISOR.value]
            if not supervisor_msgs:
                errors.append("Supervised protocol requires supervisor messages")
        
        return errors
    
    async def _validate_message_timing(
        self,
        messages: list[dict[str, Any]]
    ) -> list[str]:
        """Validate message timing sequence"""
        errors = []
        
        last_timestamp = None
        for i, msg in enumerate(messages):
            if "timestamp" in msg:
                try:
                    timestamp = datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
                    if last_timestamp and timestamp < last_timestamp:
                        errors.append(f"Message {i}: timestamp out of order")
                    last_timestamp = timestamp
                except (ValueError, TypeError) as e:
                    logger.warning(
                        "Invalid timestamp format: message_index=%s timestamp=%s error=%s",
                        i,
                        msg["timestamp"],
                        str(e)
                    )
                    errors.append(f"Message {i}: invalid timestamp format: {type(e).__name__}")
        
        return errors
    
    async def _validate_message_roles(
        self,
        messages: list[dict[str, Any]],
        protocol: ProtocolType
    ) -> list[str]:
        """Validate message roles for protocol"""
        errors = []
        
        valid_roles = {r.value for r in MessageRole}
        
        for i, msg in enumerate(messages):
            role = msg.get("role")
            if role and role not in valid_roles:
                errors.append(f"Message {i}: invalid role '{role}'")
        
        return errors
    
    async def _assess_communication_quality(
        self,
        messages: list[dict[str, Any]]
    ) -> float:
        """Assess overall communication quality"""
        if not messages:
            return 0.0
        
        # Factors: message completeness, confidence levels, evidence
        quality_scores = []
        
        for msg in messages:
            score = 0.5  # Base score
            
            # Check content length
            content = msg.get("content", "")
            if len(content) > 100:
                score += 0.2
            
            # Check confidence
            if "confidence" in msg:
                score += msg["confidence"] * 0.2
            
            # Check evidence
            if msg.get("supporting_evidence"):
                score += 0.1
            
            quality_scores.append(min(1.0, score))
        
        return sum(quality_scores) / len(quality_scores)
    
    async def _generate_protocol_recommendations(
        self,
        structural_errors: list[str],
        timing_errors: list[str],
        role_errors: list[str],
        quality_score: float
    ) -> list[str]:
        """Generate recommendations for protocol improvement"""
        recommendations = []
        
        if structural_errors:
            recommendations.append("Fix message structure issues for protocol compliance")
        
        if timing_errors:
            recommendations.append("Ensure messages are properly ordered by timestamp")
        
        if role_errors:
            recommendations.append("Use valid message roles (supervisor, worker, coordinator)")
        
        if quality_score < 0.7:
            recommendations.append("Improve message quality with more detailed content and evidence")
        
        if not recommendations:
            recommendations.append("Communication follows protocol guidelines well")
        
        return recommendations
