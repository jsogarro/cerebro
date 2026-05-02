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

from src.agents.communication.consensus_builder import ConsensusBuilder
from src.agents.communication.talkhier_message import (
    MessageType,
    TalkHierContent,
    TalkHierMessage,
)
from src.agents.supervisors.base_supervisor import BaseSupervisor
from src.agents.supervisors.supervisor_factory import (
    SupervisorFactory,
)
from src.ai_brain.integration.masr_supervisor_bridge import MASRSupervisorBridge
from src.ai_brain.router.masr import MASRouter, RoutingDecision
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
        self.sessions: dict[str, TalkHierSession] = self.state_manager.sessions
        self.consensus_builder = ConsensusBuilder()
        self.supervisor_factory = SupervisorFactory()
        self.masr_bridge = MASRSupervisorBridge()
        self.masr_router = MASRouter()
        
        # Protocol configurations
        self.protocol_configs = self._initialize_protocol_configs()
        
        # Performance tracking
        self.session_metrics = self.state_manager.session_metrics
        
        # Background tasks
        self.cleanup_task = None
        
    def _initialize_protocol_configs(self) -> dict[ProtocolType, dict[str, Any]]:
        """Initialize protocol configurations"""
        return {
            ProtocolType.STANDARD: {
                "default_rounds": 3,
                "quality_weight": 0.6,
                "consensus_weight": 0.4,
                "timeout_multiplier": 1.0
            },
            ProtocolType.FAST_TRACK: {
                "default_rounds": 2,
                "quality_weight": 0.5,
                "consensus_weight": 0.5,
                "timeout_multiplier": 0.5
            },
            ProtocolType.DEEP_ANALYSIS: {
                "default_rounds": 5,
                "quality_weight": 0.7,
                "consensus_weight": 0.3,
                "timeout_multiplier": 2.0
            },
            ProtocolType.COLLABORATIVE: {
                "default_rounds": 3,
                "quality_weight": 0.4,
                "consensus_weight": 0.6,
                "timeout_multiplier": 1.0
            },
            ProtocolType.SUPERVISED: {
                "default_rounds": 3,
                "quality_weight": 0.5,
                "consensus_weight": 0.5,
                "timeout_multiplier": 1.0,
                "supervisor_weight": 2.0
            }
        }
    
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
        
        # Validate session state
        if session.status != SessionStatus.ACTIVE:
            raise ValueError(f"Session {session_id} is not active (status: {session.status})")
        
        # Update session status
        session.status = SessionStatus.REFINING
        session.current_round = request.round_number
        
        # Start round timing
        round_start = datetime.now(UTC)
        
        # Create round record
        round_record = RefinementRound(
            round_number=request.round_number,
            status="in_progress",
            started_at=round_start,
            completed_at=None,
            participants=[p.agent_id for p in session.participants],
            messages=[],
            quality_score=0.0,
            consensus_score=0.0,
            refinement_delta=0.0,
            result=None
        )
        
        # Execute refinement through supervisor
        participant_responses = {}
        
        if session.supervisor:
            # Use supervisor coordination
            refinement_result = await self._execute_supervisor_refinement(
                session,
                request,
                round_record
            )
            participant_responses = refinement_result["responses"]
        else:
            # Direct agent refinement (fallback)
            participant_responses = await self._execute_direct_refinement(
                session,
                request,
                round_record
            )
        
        # Aggregate results
        aggregated_result = await self._aggregate_refinement_results(
            participant_responses,
            session.refinement_strategy
        )
        
        # Calculate quality and consensus scores
        quality_score = await self._calculate_quality_score(
            aggregated_result,
            session.quality_threshold
        )
        
        consensus_score = await self._calculate_consensus_score(
            participant_responses,
            session.consensus_type
        )
        
        # Calculate improvement delta
        previous_quality = session.rounds[-1].quality_score if session.rounds else 0.0
        improvement_delta = quality_score - previous_quality
        
        # Update round record
        round_record.completed_at = datetime.now(UTC)
        round_record.status = "completed"
        round_record.quality_score = quality_score
        round_record.consensus_score = consensus_score
        round_record.refinement_delta = improvement_delta
        round_record.result = aggregated_result
        
        # Add round to session
        session.rounds.append(round_record)
        
        # Update session state
        session.current_result = aggregated_result
        session.current_quality = quality_score
        session.current_consensus = consensus_score
        session.status = SessionStatus.ACTIVE
        session.last_update = datetime.now(UTC)
        
        # Update metrics
        self.state_manager.record_round(session_id, quality_score, consensus_score)
        
        # Determine if refinement should continue
        continue_refinement = self._should_continue_refinement(session)
        
        # Generate refinement suggestion
        refinement_suggestion = None
        if continue_refinement:
            refinement_suggestion = await self._generate_refinement_suggestion(
                session,
                participant_responses,
                aggregated_result
            )
        
        # Calculate duration
        duration_ms = max(
            1,
            int((datetime.now(UTC) - round_start).total_seconds() * 1000),
        )
        
        logger.info(f"Completed refinement round {request.round_number} for session {session_id}")
        logger.info(f"Quality: {quality_score:.2f}, Consensus: {consensus_score:.2f}, Delta: {improvement_delta:+.2f}")
        
        return RefinementRoundResponse(
            session_id=session_id,
            round_number=request.round_number,
            round_status="completed",
            duration_ms=duration_ms,
            participant_responses=participant_responses,
            aggregated_result=aggregated_result,
            quality_score=quality_score,
            consensus_score=consensus_score,
            improvement_delta=improvement_delta,
            continue_refinement=continue_refinement,
            refinement_suggestion=refinement_suggestion
        )
    
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
        
        # Update status
        previous_status = session.status
        session.status = SessionStatus.CONSENSUS_CHECKING
        
        # Build consensus using ConsensusBuilder
        consensus_messages = []
        for result in request.round_results:
            msg = TalkHierMessage(
                from_agent=result.get("agent", "unknown"),
                to_agent="consensus_checker",
                content=TalkHierContent(
                    content=str(result.get("content", "")),
                    confidence_score=result.get("confidence", 0.5)
                ),
                message_type=MessageType.RESPONSE
            )
            consensus_messages.append(msg)
        
        # Calculate consensus
        consensus_result = await self.consensus_builder.evaluate_consensus(
            consensus_messages,
            threshold=session.consensus_threshold
        )
        has_consensus = consensus_result.overall_score >= session.consensus_threshold
        consensus_score = consensus_result.overall_score
        
        # Calculate agreement matrix
        agreement_matrix = await self._calculate_agreement_matrix(
            request.round_results
        )
        
        # Calculate quality scores
        quality_scores = {}
        for result in request.round_results:
            agent_id = result.get("agent", "unknown")
            quality_scores[agent_id] = result.get("confidence", 0.0)
        
        # Generate minority reports if requested
        minority_reports = None
        if request.include_minority_report and not has_consensus:
            minority_reports = await self._generate_minority_reports(
                request.round_results,
                consensus_score
            )
        
        # Determine recommendation
        recommendation = self._generate_consensus_recommendation(
            has_consensus,
            consensus_score,
            session
        )
        
        # Generate reasoning
        reasoning = self._generate_consensus_reasoning(
            has_consensus,
            consensus_score,
            agreement_matrix,
            session
        )
        
        # Restore previous status
        session.status = previous_status
        session.last_update = datetime.now(UTC)
        
        logger.info(f"Consensus check for session {session_id}: {has_consensus} (score: {consensus_score:.2f})")
        
        return ConsensusResult(
            has_consensus=has_consensus,
            consensus_type=session.consensus_type,
            consensus_score=consensus_score,
            agreement_matrix=agreement_matrix,
            quality_scores=quality_scores,
            minority_reports=minority_reports,
            recommendation=recommendation,
            reasoning=reasoning
        )
    
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
        return await self.masr_router.route(query, context={"domains": domains})
    
    async def _create_supervisor(
        self,
        routing_decision: RoutingDecision,
        requested_type: str | None
    ) -> BaseSupervisor | None:
        """Create supervisor based on routing decision"""
        supervisor_type = self._resolve_supervisor_type(routing_decision, requested_type)

        from src.ai_brain.integration.masr_supervisor_bridge import (
            SupervisorConfiguration,
        )

        domains = routing_decision.complexity_analysis.domains
        domain = domains[0].value if domains and hasattr(domains[0], "value") else "research"
        allocation = routing_decision.agent_allocation

        config = SupervisorConfiguration(
            supervisor_type=supervisor_type,
            domain=domain,
            worker_allocation=allocation.worker_types,
            quality_threshold=routing_decision.estimated_quality or 0.85,
            max_refinement_rounds=allocation.retry_attempts or 3,
            timeout_seconds=allocation.timeout_seconds,
            max_workers=allocation.worker_count,
        )

        return await self.supervisor_factory.create_supervisor_from_config(config)

    def _resolve_supervisor_type(
        self,
        routing_decision: RoutingDecision,
        requested_type: str | None,
    ) -> str:
        """Resolve a concrete supervisor type from routing output or test doubles."""
        if requested_type:
            return requested_type

        allocation = getattr(routing_decision, "agent_allocation", None)
        supervisor_type = getattr(allocation, "supervisor_type", None)
        if isinstance(supervisor_type, str):
            return supervisor_type

        for supervisor_allocation in getattr(routing_decision, "supervisor_allocations", []):
            supervisor_type = getattr(supervisor_allocation, "supervisor_type", None)
            if isinstance(supervisor_type, str):
                return supervisor_type

        return "research"
    
    async def _determine_participants(
        self,
        requested: list[str] | None,
        routing_decision: RoutingDecision,
        supervisor: BaseSupervisor | None
    ) -> list[ParticipantInfo]:
        """Determine session participants"""
        participants = []
        
        # Use requested participants or routing decision
        agent_ids = requested or []
        
        if not agent_ids and routing_decision.agent_allocation:
            agent_ids = routing_decision.agent_allocation.worker_types
            if not isinstance(agent_ids, list):
                agent_ids = [
                    agent.agent_type
                    for agent in getattr(routing_decision.agent_allocation, "agents", [])
                    if isinstance(getattr(agent, "agent_type", None), str)
                ]
        
        # Create participant info
        for agent_id in agent_ids:
            participants.append(ParticipantInfo(
                agent_id=agent_id,
                agent_type=agent_id,
                role=MessageRole.WORKER,
                confidence=0.5,
                rounds_participated=0,
                quality_scores=[]
            ))
        
        # Add supervisor if present
        if supervisor:
            participants.append(ParticipantInfo(
                agent_id="supervisor",
                agent_type=supervisor.__class__.__name__,
                role=MessageRole.SUPERVISOR,
                confidence=0.8,
                rounds_participated=0,
                quality_scores=[]
            ))
        
        return participants
    
    def _estimate_duration(
        self,
        max_rounds: int,
        participant_count: int,
        protocol_config: dict[str, Any]
    ) -> int:
        """Estimate session duration in seconds"""
        base_duration = 30  # Base time per round
        participant_factor = participant_count * 10  # Time per participant
        round_duration = base_duration + participant_factor
        
        total_duration = max_rounds * round_duration
        timeout_multiplier = protocol_config.get("timeout_multiplier", 1.0)
        
        return int(total_duration * timeout_multiplier)
    
    async def _execute_supervisor_refinement(
        self,
        session: TalkHierSession,
        request: RefinementRoundRequest,
        round_record: RefinementRound
    ) -> dict[str, Any]:
        """Execute refinement through supervisor"""
        # Implementation would coordinate with supervisor
        # This is a simplified version
        responses = {}
        
        for participant in session.participants:
            if participant.role == MessageRole.WORKER:
                # Simulate worker response
                responses[participant.agent_id] = {
                    "content": f"Refined response from {participant.agent_id}",
                    "confidence": 0.75 + (request.round_number * 0.05),
                    "evidence": ["Evidence 1", "Evidence 2"]
                }
        
        return {"responses": responses}
    
    async def _execute_direct_refinement(
        self,
        session: TalkHierSession,
        request: RefinementRoundRequest,
        round_record: RefinementRound
    ) -> dict[str, dict[str, Any]]:
        """Execute direct agent refinement without supervisor"""
        responses = {}
        
        for participant in session.participants:
            if participant.role == MessageRole.WORKER:
                # Direct agent execution
                responses[participant.agent_id] = {
                    "content": f"Direct response from {participant.agent_id}",
                    "confidence": 0.7 + (request.round_number * 0.05),
                    "evidence": []
                }
        
        return responses
    
    async def _aggregate_refinement_results(
        self,
        responses: dict[str, dict[str, Any]],
        strategy: RefinementStrategy
    ) -> dict[str, Any]:
        """Aggregate participant responses based on strategy"""
        if not responses:
            return {
                "content": "",
                "confidence": 0.0,
                "aggregation_method": "empty",
            }

        if strategy == RefinementStrategy.QUALITY_FOCUSED:
            # Weight by confidence
            best_response = max(
                responses.items(),
                key=lambda x: x[1].get("confidence", 0)
            )
            return best_response[1]
        
        elif strategy == RefinementStrategy.CONSENSUS_DRIVEN:
            # Merge all responses
            merged_content = "\n".join([
                r.get("content", "")
                for r in responses.values()
            ])
            avg_confidence = sum(
                r.get("confidence", 0)
                for r in responses.values()
            ) / max(1, len(responses))
            
            return {
                "content": merged_content,
                "confidence": avg_confidence,
                "aggregation_method": "consensus"
            }
        
        else:
            # Default: return all responses
            return {
                "responses": responses,
                "aggregation_method": strategy.value
            }
    
    async def _calculate_quality_score(
        self,
        result: dict[str, Any],
        threshold: float
    ) -> float:
        """Calculate quality score for result"""
        # Base quality from confidence
        base_quality = float(result.get("confidence", 0.5))

        # Adjust for evidence
        evidence_bonus = 0.1 if result.get("evidence") else 0.0

        # Adjust for content length
        content = str(result.get("content", ""))
        length_factor = min(1.0, len(content) / 1000)

        quality = base_quality + evidence_bonus
        quality = quality * (0.7 + 0.3 * length_factor)

        return float(min(1.0, quality))
    
    async def _calculate_consensus_score(
        self,
        responses: dict[str, dict[str, Any]],
        consensus_type: ConsensusType
    ) -> float:
        """Calculate consensus score among responses"""
        if len(responses) <= 1:
            return 1.0
        
        confidences = [r.get("confidence", 0) for r in responses.values()]
        
        if consensus_type == ConsensusType.MAJORITY:
            # Simple agreement based on confidence similarity
            avg_confidence = sum(confidences) / len(confidences)
            variance = sum((c - avg_confidence) ** 2 for c in confidences) / len(confidences)
            consensus = 1.0 - min(1.0, variance)
            
        elif consensus_type == ConsensusType.WEIGHTED:
            # Weight by confidence values
            weighted_sum = sum(c * c for c in confidences)
            total_weight = sum(c for c in confidences)
            consensus = weighted_sum / max(1, total_weight)
            
        elif consensus_type == ConsensusType.UNANIMOUS:
            # All must be high confidence
            consensus = 1.0 if all(c >= 0.8 for c in confidences) else 0.0
            
        else:
            # Default threshold-based
            high_confidence_count = sum(1 for c in confidences if c >= 0.7)
            consensus = float(high_confidence_count) / float(len(confidences))

        return float(consensus)
    
    def _should_continue_refinement(self, session: TalkHierSession) -> bool:
        """Determine if refinement should continue"""
        # Check round limits
        if session.current_round >= session.max_rounds:
            return False
        
        if session.current_round < session.min_rounds:
            return True
        
        # Check quality threshold
        if session.current_quality >= session.quality_threshold and session.current_consensus >= session.consensus_threshold:
            return False
        
        # Check improvement trend
        if len(session.rounds) >= 2:
            recent_improvements = [
                r.refinement_delta
                for r in session.rounds[-2:]
            ]
            if all(delta < 0.01 for delta in recent_improvements):
                return False  # No significant improvement
        
        return True
    
    async def _generate_refinement_suggestion(
        self,
        session: TalkHierSession,
        responses: dict[str, dict[str, Any]],
        result: dict[str, Any]
    ) -> str:
        """Generate suggestion for next refinement round"""
        suggestions = []
        
        # Check quality gap
        quality_gap = session.quality_threshold - session.current_quality
        if quality_gap > 0.2:
            suggestions.append("Focus on improving evidence and supporting data")
        elif quality_gap > 0.1:
            suggestions.append("Refine clarity and strengthen key arguments")
        
        # Check consensus gap
        consensus_gap = session.consensus_threshold - session.current_consensus
        if consensus_gap > 0.2:
            suggestions.append("Address disagreements between participants")
        elif consensus_gap > 0.1:
            suggestions.append("Align perspectives on key points")
        
        # Check specific weaknesses
        low_confidence = [
            agent_id
            for agent_id, resp in responses.items()
            if resp.get("confidence", 0) < 0.7
        ]
        if low_confidence:
            suggestions.append(f"Strengthen responses from: {', '.join(low_confidence)}")
        
        return " | ".join(suggestions) if suggestions else "Continue general refinement"
    
    async def _calculate_agreement_matrix(
        self,
        results: list[dict[str, Any]]
    ) -> dict[str, dict[str, float]]:
        """Calculate pairwise agreement between participants"""
        matrix: dict[str, dict[str, float]] = {}
        
        for i, result1 in enumerate(results):
            agent1 = result1.get("agent", f"agent_{i}")
            matrix[agent1] = {}
            
            for j, result2 in enumerate(results):
                agent2 = result2.get("agent", f"agent_{j}")
                
                if i == j:
                    matrix[agent1][agent2] = 1.0
                else:
                    # Simple confidence-based agreement
                    conf1 = result1.get("confidence", 0.5)
                    conf2 = result2.get("confidence", 0.5)
                    agreement = 1.0 - abs(conf1 - conf2)
                    matrix[agent1][agent2] = agreement
        
        return matrix
    
    async def _generate_minority_reports(
        self,
        results: list[dict[str, Any]],
        consensus_score: float
    ) -> list[dict[str, Any]]:
        """Generate minority opinion reports"""
        minority_reports = []
        
        avg_confidence = sum(
            r.get("confidence", 0) for r in results
        ) / max(1, len(results))
        
        for result in results:
            confidence = result.get("confidence", 0)
            if abs(confidence - avg_confidence) > 0.2:
                minority_reports.append({
                    "agent": result.get("agent"),
                    "position": result.get("content"),
                    "confidence": confidence,
                    "deviation": confidence - avg_confidence
                })
        
        return minority_reports
    
    def _generate_consensus_recommendation(
        self,
        has_consensus: bool,
        consensus_score: float,
        session: TalkHierSession
    ) -> str:
        """Generate recommendation based on consensus status"""
        if has_consensus:
            return "Consensus achieved - proceed with final result"
        
        if consensus_score >= session.consensus_threshold * 0.9:
            return "Near consensus - one more refinement round recommended"
        
        if consensus_score < 0.5:
            return "Low consensus - consider debate mode or supervisor intervention"
        
        return "Continue refinement to improve consensus"
    
    def _generate_consensus_reasoning(
        self,
        has_consensus: bool,
        consensus_score: float,
        agreement_matrix: dict[str, dict[str, float]],
        session: TalkHierSession
    ) -> str:
        """Generate reasoning for consensus result"""
        reasoning_parts = []
        
        if has_consensus:
            reasoning_parts.append(
                f"Consensus achieved with score {consensus_score:.2f} "
                f"(threshold: {session.consensus_threshold:.2f})"
            )
        else:
            reasoning_parts.append(
                f"Consensus not reached - score {consensus_score:.2f} "
                f"below threshold {session.consensus_threshold:.2f}"
            )
        
        # Analyze agreement patterns
        high_agreement_pairs = []
        low_agreement_pairs = []
        
        for agent1, agreements in agreement_matrix.items():
            for agent2, score in agreements.items():
                if agent1 < agent2:  # Avoid duplicates
                    if score >= 0.8:
                        high_agreement_pairs.append((agent1, agent2))
                    elif score < 0.5:
                        low_agreement_pairs.append((agent1, agent2))
        
        if high_agreement_pairs:
            reasoning_parts.append(
                f"Strong agreement between: {', '.join([f'{a1}-{a2}' for a1, a2 in high_agreement_pairs[:3]])}"
            )
        
        if low_agreement_pairs:
            reasoning_parts.append(
                f"Disagreement between: {', '.join([f'{a1}-{a2}' for a1, a2 in low_agreement_pairs[:3]])}"
            )
        
        return " | ".join(reasoning_parts)
    
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
