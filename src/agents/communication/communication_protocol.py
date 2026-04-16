"""
TalkHier Communication Protocol Implementation

Implements the TalkHier communication protocol with LangGraph integration,
providing structured multi-agent communication with consensus building
and iterative refinement capabilities.

Key Features:
- Multi-round refinement workflow (typically 3 rounds)
- Hierarchical message routing
- Consensus validation and building
- Integration with LangGraph state management
- Backward compatibility with existing AgentMessage system
"""

import asyncio
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from src.core.types import ProtocolStatsDict

from ..base import BaseAgent
from .consensus_builder import ConsensusBuilder, ConsensusScore, ValidationResult
from .talkhier_message import (
    ConsensusRequirement,
    MessageType,
    RefinementMetadata,
    TalkHierContent,
    TalkHierMessage,
)

logger = logging.getLogger(__name__)


class CommunicationMode(Enum):
    """Communication modes for different interaction patterns."""

    DIRECT = "direct"  # One-to-one communication
    BROADCAST = "broadcast"  # One-to-many within level
    HIERARCHICAL = "hierarchical"  # Up/down the supervision chain
    CONSENSUS = "consensus"  # Multi-round refinement
    VALIDATION = "validation"  # Cross-validation between peers


class WorkflowPhase(Enum):
    """Phases of TalkHier workflow."""

    INITIAL_EXECUTION = "initial_execution"
    CROSS_VALIDATION = "cross_validation"
    CONSENSUS_BUILDING = "consensus_building"
    FINAL_SYNTHESIS = "final_synthesis"
    COMPLETED = "completed"


@dataclass
class RefinementRound:
    """Single refinement round in TalkHier protocol."""

    round_number: int
    phase: WorkflowPhase
    participant_messages: list[TalkHierMessage] = field(default_factory=list)
    consensus_score: ConsensusScore | None = None
    validation_results: list[ValidationResult] = field(default_factory=list)

    # Round metadata
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    duration_ms: int | None = None

    # Round outcomes
    consensus_achieved: bool = False
    quality_threshold_met: bool = False
    improvements_needed: list[str] = field(default_factory=list)


@dataclass
class RefinementResult:
    """Final result of TalkHier refinement process."""

    # Process information
    total_rounds: int = 0
    consensus_achieved: bool = False
    final_consensus_score: float = 0.0

    # Final outputs
    synthesized_response: TalkHierContent | None = None
    confidence_score: float = 0.0
    quality_score: float = 0.0

    # Process metadata
    rounds: list[RefinementRound] = field(default_factory=list)
    total_duration_ms: int = 0
    participating_agents: list[str] = field(default_factory=list)

    # Improvement tracking
    initial_quality: float = 0.0
    final_quality: float = 0.0
    quality_improvement: float = 0.0


class CommunicationProtocol:
    """
    TalkHier-inspired communication protocol with LangGraph integration.

    Manages sophisticated agent communication patterns including:
    - Multi-round refinement workflows
    - Hierarchical coordination
    - Consensus building and validation
    - Message routing and delivery
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize communication protocol."""
        self.config = config or {}

        # Protocol configuration
        self.max_refinement_rounds = self.config.get("max_refinement_rounds", 3)
        self.consensus_threshold = self.config.get("consensus_threshold", 0.95)
        self.quality_threshold = self.config.get("quality_threshold", 0.8)
        self.refinement_timeout_minutes = self.config.get(
            "refinement_timeout_minutes", 30
        )

        # Consensus builder
        self.consensus_builder = ConsensusBuilder(
            self.config.get("consensus_builder", {})
        )

        # Message routing and storage
        self.active_conversations: dict[str, list[TalkHierMessage]] = {}
        self.conversation_metadata: dict[str, dict[str, Any]] = {}

        # Performance tracking
        self.total_conversations = 0
        self.successful_consensus = 0
        self.average_rounds = 0.0

    async def initiate_refinement_workflow(
        self,
        initial_query: str,
        participating_agents: list[BaseAgent],
        context: dict[str, Any] | None = None,
        consensus_threshold: float | None = None,
    ) -> RefinementResult:
        """
        Initiate TalkHier refinement workflow.

        Args:
            initial_query: The query or task to refine
            participating_agents: List of agents to participate
            context: Optional context information
            consensus_threshold: Override default consensus threshold

        Returns:
            RefinementResult with final consensus and metadata
        """

        conversation_id = f"refinement_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        consensus_target = consensus_threshold or self.consensus_threshold

        logger.info(f"Starting TalkHier refinement: {conversation_id}")

        self.total_conversations += 1
        start_time = datetime.now()

        try:
            # Initialize conversation
            self.active_conversations[conversation_id] = []
            self.conversation_metadata[conversation_id] = {
                "query": initial_query,
                "agents": [agent.get_agent_type() for agent in participating_agents],
                "context": context or {},
                "target_consensus": consensus_target,
                "started_at": start_time,
            }

            refinement_rounds = []

            # Round 1: Initial responses
            round_1 = await self._execute_round_1_initial_responses(
                conversation_id, initial_query, participating_agents, context
            )
            refinement_rounds.append(round_1)

            if round_1.consensus_achieved:
                logger.info(
                    f"Consensus achieved in Round 1: {round_1.consensus_score.overall_score:.3f}"
                )
                return await self._finalize_refinement(
                    conversation_id, refinement_rounds, start_time
                )

            # Round 2: Cross-validation and conflict resolution
            if (
                round_1.consensus_score.overall_score >= 0.7
            ):  # Proceed if reasonable progress
                round_2 = await self._execute_round_2_cross_validation(
                    conversation_id, round_1, participating_agents
                )
                refinement_rounds.append(round_2)

                if round_2.consensus_achieved:
                    logger.info(
                        f"Consensus achieved in Round 2: {round_2.consensus_score.overall_score:.3f}"
                    )
                    return await self._finalize_refinement(
                        conversation_id, refinement_rounds, start_time
                    )

            # Round 3: Final synthesis and consensus
            if len(refinement_rounds) >= 2:
                round_3 = await self._execute_round_3_final_synthesis(
                    conversation_id, refinement_rounds, participating_agents
                )
                refinement_rounds.append(round_3)

            # Finalize results
            return await self._finalize_refinement(
                conversation_id, refinement_rounds, start_time
            )

        except Exception as e:
            logger.error(f"Refinement workflow failed: {e}")
            return RefinementResult(
                total_rounds=len(refinement_rounds),
                consensus_achieved=False,
                participating_agents=[a.get_agent_type() for a in participating_agents],
            )

        finally:
            # Clean up conversation
            self.active_conversations.pop(conversation_id, None)
            self.conversation_metadata.pop(conversation_id, None)

    async def _execute_round_1_initial_responses(
        self,
        conversation_id: str,
        initial_query: str,
        agents: list[BaseAgent],
        context: dict[str, Any] | None,
    ) -> RefinementRound:
        """Execute Round 1: Gather initial responses from all agents."""

        round_1 = RefinementRound(round_number=1, phase=WorkflowPhase.INITIAL_EXECUTION)

        logger.info(f"Round 1: Gathering initial responses from {len(agents)} agents")

        try:
            # Create initial messages for all agents
            initial_messages = []

            for agent in agents:
                message = TalkHierMessage(
                    from_agent="supervisor",
                    to_agent=agent.get_agent_type(),
                    message_type=MessageType.INITIAL_RESPONSE,
                    content=TalkHierContent(
                        content=initial_query,
                        background=f"Initial request in conversation {conversation_id}",
                        intermediate_outputs={"context": context or {}},
                    ),
                    conversation_id=conversation_id,
                    refinement_metadata=RefinementMetadata(round_number=1),
                    context=context or {},
                )
                initial_messages.append(message)

            # Send messages to agents and collect responses
            response_tasks = []
            for agent, message in zip(agents, initial_messages):
                response_tasks.append(self._send_message_to_agent(agent, message))

            # Wait for all responses
            responses = await asyncio.gather(*response_tasks, return_exceptions=True)

            # Process responses
            valid_responses = []
            for i, response in enumerate(responses):
                if isinstance(response, Exception):
                    logger.error(
                        f"Agent {agents[i].get_agent_type()} failed: {response}"
                    )
                elif isinstance(response, TalkHierMessage):
                    valid_responses.append(response)
                    round_1.participant_messages.append(response)

            # Evaluate consensus
            round_1.consensus_score = await self.consensus_builder.evaluate_consensus(
                round_1.participant_messages
            )

            # Check if consensus threshold met
            round_1.consensus_achieved = (
                round_1.consensus_score.overall_score >= self.consensus_threshold
            )

            round_1.completed_at = datetime.now()
            round_1.duration_ms = int(
                (round_1.completed_at - round_1.started_at).total_seconds() * 1000
            )

            logger.info(
                f"Round 1 complete: {round_1.consensus_score.overall_score:.3f} consensus"
            )

        except Exception as e:
            logger.error(f"Round 1 execution failed: {e}")

        return round_1

    async def _execute_round_2_cross_validation(
        self, conversation_id: str, round_1: RefinementRound, agents: list[BaseAgent]
    ) -> RefinementRound:
        """Execute Round 2: Cross-validation and conflict resolution."""

        round_2 = RefinementRound(round_number=2, phase=WorkflowPhase.CROSS_VALIDATION)

        logger.info("Round 2: Cross-validation and conflict resolution")

        try:
            # Create validation messages based on Round 1 conflicts
            conflicts = round_1.consensus_score.conflicts_detected
            resolution_suggestions = round_1.consensus_score.resolution_suggestions

            validation_messages = []

            for agent in agents:
                # Each agent gets the other agents' responses for validation
                other_responses = [
                    msg
                    for msg in round_1.participant_messages
                    if msg.from_agent != agent.get_agent_type()
                ]

                validation_content = TalkHierContent(
                    content="Please validate and refine based on peer responses",
                    background=f"Round 2 validation with conflicts: {conflicts}",
                    intermediate_outputs={
                        "peer_responses": [msg.to_dict() for msg in other_responses],
                        "conflicts_to_resolve": conflicts,
                        "resolution_suggestions": resolution_suggestions,
                    },
                )

                message = TalkHierMessage(
                    from_agent="evaluation_supervisor",
                    to_agent=agent.get_agent_type(),
                    message_type=MessageType.REFINEMENT_REQUEST,
                    content=validation_content,
                    conversation_id=conversation_id,
                    refinement_metadata=RefinementMetadata(round_number=2),
                )

                validation_messages.append(message)

            # Send validation requests and collect responses
            response_tasks = []
            for agent, message in zip(agents, validation_messages):
                response_tasks.append(self._send_message_to_agent(agent, message))

            responses = await asyncio.gather(*response_tasks, return_exceptions=True)

            # Process Round 2 responses
            for i, response in enumerate(responses):
                if isinstance(response, Exception):
                    logger.error(
                        f"Round 2 agent {agents[i].get_agent_type()} failed: {response}"
                    )
                elif isinstance(response, TalkHierMessage):
                    round_2.participant_messages.append(response)

            # Evaluate Round 2 consensus
            round_2.consensus_score = await self.consensus_builder.evaluate_consensus(
                round_2.participant_messages
            )

            round_2.consensus_achieved = (
                round_2.consensus_score.overall_score >= self.consensus_threshold
            )

            round_2.completed_at = datetime.now()
            round_2.duration_ms = int(
                (round_2.completed_at - round_2.started_at).total_seconds() * 1000
            )

            logger.info(
                f"Round 2 complete: {round_2.consensus_score.overall_score:.3f} consensus"
            )

        except Exception as e:
            logger.error(f"Round 2 execution failed: {e}")

        return round_2

    async def _execute_round_3_final_synthesis(
        self,
        conversation_id: str,
        previous_rounds: list[RefinementRound],
        agents: list[BaseAgent],
    ) -> RefinementRound:
        """Execute Round 3: Final synthesis and consensus."""

        round_3 = RefinementRound(round_number=3, phase=WorkflowPhase.FINAL_SYNTHESIS)

        logger.info("Round 3: Final synthesis and consensus")

        try:
            # Prepare synthesis data from all previous rounds
            all_responses = []
            for round_data in previous_rounds:
                all_responses.extend(round_data.participant_messages)

            # Create synthesis message
            synthesis_content = TalkHierContent(
                content="Create final consensus synthesis",
                background="Final round synthesis of all previous responses",
                intermediate_outputs={
                    "all_previous_responses": [msg.to_dict() for msg in all_responses],
                    "consensus_progress": [
                        r.consensus_score.overall_score for r in previous_rounds
                    ],
                    "remaining_conflicts": previous_rounds[
                        -1
                    ].consensus_score.disagreement_areas,
                },
            )

            # Send synthesis request to all agents
            synthesis_tasks = []

            for agent in agents:
                message = TalkHierMessage(
                    from_agent="evaluation_supervisor",
                    to_agent=agent.get_agent_type(),
                    message_type=MessageType.SYNTHESIS_REQUEST,
                    content=synthesis_content,
                    conversation_id=conversation_id,
                    refinement_metadata=RefinementMetadata(round_number=3),
                    consensus_requirement=ConsensusRequirement.STRONG,
                )

                synthesis_tasks.append(self._send_message_to_agent(agent, message))

            # Collect final synthesis responses
            responses = await asyncio.gather(*synthesis_tasks, return_exceptions=True)

            for i, response in enumerate(responses):
                if isinstance(response, Exception):
                    logger.error(
                        f"Round 3 agent {agents[i].get_agent_type()} failed: {response}"
                    )
                elif isinstance(response, TalkHierMessage):
                    round_3.participant_messages.append(response)

            # Final consensus evaluation
            round_3.consensus_score = await self.consensus_builder.evaluate_consensus(
                round_3.participant_messages
            )

            round_3.consensus_achieved = (
                round_3.consensus_score.overall_score >= self.consensus_threshold
            )

            round_3.completed_at = datetime.now()
            round_3.duration_ms = int(
                (round_3.completed_at - round_3.started_at).total_seconds() * 1000
            )

            logger.info(
                f"Round 3 complete: {round_3.consensus_score.overall_score:.3f} consensus"
            )

        except Exception as e:
            logger.error(f"Round 3 execution failed: {e}")

        return round_3

    async def _send_message_to_agent(
        self, agent: BaseAgent, message: TalkHierMessage
    ) -> TalkHierMessage:
        """Send TalkHier message to agent and get response."""

        try:
            # Convert to legacy format for existing agents
            legacy_message = message.to_legacy_message()

            # Send via existing agent communication
            await agent.receive_message(legacy_message)

            # For now, simulate agent response
            # In full implementation, agents would process and respond
            response_content = TalkHierContent(
                content=f"Processed by {agent.get_agent_type()}",
                background=f"Agent {agent.get_agent_type()} processing",
                intermediate_outputs={"processing_time": "simulated"},
                confidence_score=0.8,
            )

            response = TalkHierMessage(
                from_agent=agent.get_agent_type(),
                to_agent=message.from_agent,
                message_type=MessageType.RESPONSE,
                content=response_content,
                conversation_id=message.conversation_id,
                refinement_metadata=message.refinement_metadata,
            )

            response.mark_processed()
            return response

        except Exception as e:
            logger.error(f"Failed to send message to {agent.get_agent_type()}: {e}")
            # Return error response
            error_content = TalkHierContent(
                content=f"Error processing message: {e!s}", confidence_score=0.0
            )

            return TalkHierMessage(
                from_agent=agent.get_agent_type(),
                to_agent=message.from_agent,
                message_type=MessageType.RESPONSE,
                content=error_content,
                conversation_id=message.conversation_id,
            )

    async def _finalize_refinement(
        self, conversation_id: str, rounds: list[RefinementRound], start_time: datetime
    ) -> RefinementResult:
        """Finalize refinement process and create result."""

        if not rounds:
            return RefinementResult()

        # Get final round data
        final_round = rounds[-1]

        # Calculate metrics
        total_duration = int((datetime.now() - start_time).total_seconds() * 1000)
        consensus_achieved = final_round.consensus_achieved

        if consensus_achieved:
            self.successful_consensus += 1

        # Update average rounds
        self.average_rounds = (
            self.average_rounds * (self.total_conversations - 1) + len(rounds)
        ) / self.total_conversations

        # Create synthesized response from final round
        synthesized_response = await self._synthesize_final_response(rounds)

        # Calculate quality improvement
        initial_quality = rounds[0].consensus_score.overall_score if rounds else 0.0
        final_quality = final_round.consensus_score.overall_score
        quality_improvement = final_quality - initial_quality

        result = RefinementResult(
            total_rounds=len(rounds),
            consensus_achieved=consensus_achieved,
            final_consensus_score=final_round.consensus_score.overall_score,
            synthesized_response=synthesized_response,
            confidence_score=(
                synthesized_response.confidence_score if synthesized_response else 0.0
            ),
            quality_score=final_quality,
            rounds=rounds,
            total_duration_ms=total_duration,
            participating_agents=self.conversation_metadata[conversation_id]["agents"],
            initial_quality=initial_quality,
            final_quality=final_quality,
            quality_improvement=quality_improvement,
        )

        logger.info(
            f"Refinement complete: {len(rounds)} rounds, "
            f"{final_quality:.3f} final quality, "
            f"{quality_improvement:+.3f} improvement"
        )

        return result

    async def _synthesize_final_response(
        self, rounds: list[RefinementRound]
    ) -> TalkHierContent | None:
        """Synthesize final response from all refinement rounds."""

        if not rounds:
            return None

        # Get all responses from final round
        final_messages = rounds[-1].participant_messages

        if not final_messages:
            return None

        # Simple synthesis: combine highest confidence responses
        best_response = max(
            final_messages, key=lambda msg: msg.talkhier_content.confidence_score
        )

        # Create synthesized content
        all_contents = [msg.talkhier_content.content for msg in final_messages]
        all_evidence = []
        for msg in final_messages:
            all_evidence.extend(msg.talkhier_content.evidence)

        synthesized = TalkHierContent(
            content=best_response.talkhier_content.content,
            background=f"Synthesized from {len(final_messages)} agent responses across {len(rounds)} refinement rounds",
            intermediate_outputs={
                "synthesis_method": "highest_confidence_selection",
                "total_rounds": len(rounds),
                "final_consensus": rounds[-1].consensus_score.overall_score,
                "contributing_agents": [msg.from_agent for msg in final_messages],
            },
            confidence_score=statistics.mean(
                [msg.talkhier_content.confidence_score for msg in final_messages]
            ),
            evidence=list(set(all_evidence)),  # Deduplicate evidence
        )

        return synthesized

    async def send_hierarchical_message(
        self, message: TalkHierMessage, target_agents: list[BaseAgent]
    ) -> list[TalkHierMessage]:
        """Send message through hierarchical routing."""

        responses = []

        try:
            # Route message based on hierarchy level
            if message.hierarchy_metadata.hierarchy_level >= 2:
                # Supervisor message - broadcast to workers
                for agent in target_agents:
                    response = await self._send_message_to_agent(agent, message)
                    responses.append(response)

            else:
                # Worker message - send to supervisor
                supervisor_agents = [
                    agent
                    for agent in target_agents
                    if "supervisor" in agent.get_agent_type()
                ]

                for supervisor in supervisor_agents:
                    response = await self._send_message_to_agent(supervisor, message)
                    responses.append(response)

        except Exception as e:
            logger.error(f"Hierarchical message routing failed: {e}")

        return responses

    async def get_protocol_stats(self) -> ProtocolStatsDict:
        """Get communication protocol performance statistics."""

        success_rate = self.successful_consensus / max(self.total_conversations, 1)

        consensus_stats = await self.consensus_builder.get_consensus_stats()

        return {
            "protocol": {
                "total_conversations": self.total_conversations,
                "successful_consensus": self.successful_consensus,
                "success_rate": success_rate,
                "average_rounds": self.average_rounds,
                "max_refinement_rounds": self.max_refinement_rounds,
                "consensus_threshold": self.consensus_threshold,
            },
            "consensus_builder": consensus_stats,
            "active_conversations": len(self.active_conversations),
        }


__all__ = [
    "CommunicationMode",
    "CommunicationProtocol",
    "RefinementResult",
    "RefinementRound",
    "WorkflowPhase",
]
