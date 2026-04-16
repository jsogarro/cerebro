"""
TalkHier Message Structure

Enhanced message format implementing the TalkHier protocol's structured
3-part message format while maintaining compatibility with existing
AgentMessage infrastructure.

Based on TalkHier research showing 88.38% accuracy improvement through
structured messaging and multi-round refinement.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ..models import AgentMessage


class MessageType(Enum):
    """Enhanced message types for TalkHier protocol."""

    # Basic types (compatible with existing system)
    FINDINGS = "findings"
    REQUEST = "request"
    RESPONSE = "response"
    COORDINATION = "coordination"

    # TalkHier-specific types
    INITIAL_RESPONSE = "initial_response"
    REFINEMENT_REQUEST = "refinement_request"
    CONSENSUS_CHECK = "consensus_check"
    VALIDATION_RESULT = "validation_result"
    SYNTHESIS_REQUEST = "synthesis_request"

    # Hierarchical types
    SUPERVISOR_ASSIGNMENT = "supervisor_assignment"
    WORKER_REPORT = "worker_report"
    ESCALATION = "escalation"
    DELEGATION = "delegation"


class ConsensusRequirement(Enum):
    """Consensus requirement levels."""

    NONE = "none"  # No consensus required
    SIMPLE = "simple"  # Simple majority (>50%)
    STRONG = "strong"  # Strong majority (>75%)
    UNANIMOUS = "unanimous"  # Full consensus (>95%)


class MessagePriority(Enum):
    """Message priority levels."""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3
    URGENT = 4


@dataclass
class TalkHierContent:
    """TalkHier's 3-part structured content format."""

    # Part 1: Direct content (answer to the query)
    content: str = ""

    # Part 2: Background information and context
    background: str = ""

    # Part 3: Intermediate outputs and working thoughts
    intermediate_outputs: dict[str, Any] = field(default_factory=dict)

    # Additional structured elements
    confidence_score: float = 1.0
    evidence: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass
class RefinementMetadata:
    """Metadata for refinement process."""

    round_number: int = 1
    max_rounds: int = 3
    quality_threshold: float = 0.95
    consensus_threshold: float = 0.95

    # Refinement tracking
    improvements_needed: list[str] = field(default_factory=list)
    validation_criteria: list[str] = field(default_factory=list)
    previous_consensus_scores: list[float] = field(default_factory=list)


@dataclass
class HierarchyMetadata:
    """Metadata for hierarchical communication."""

    hierarchy_level: int = 1  # 1=worker, 2=supervisor, 3=coordinator
    supervisor_id: str | None = None
    worker_ids: list[str] = field(default_factory=list)

    # Delegation information
    delegated_from: str | None = None
    delegation_depth: int = 0
    escalation_path: list[str] = field(default_factory=list)


class TalkHierMessage:
    """
    Enhanced message structure implementing TalkHier protocol.

    Extends the existing AgentMessage with:
    - Structured 3-part content format (Content, Background, Intermediate)
    - Hierarchical routing and delegation support
    - Multi-round refinement tracking
    - Consensus building mechanisms
    - Quality and confidence metrics
    """

    def __init__(
        self,
        from_agent: str,
        to_agent: str,
        message_type: MessageType,
        content: TalkHierContent | dict[str, Any] | str,
        # Hierarchy and routing
        hierarchy_metadata: HierarchyMetadata | None = None,
        conversation_id: str | None = None,
        # Refinement and consensus
        refinement_metadata: RefinementMetadata | None = None,
        consensus_requirement: ConsensusRequirement = ConsensusRequirement.NONE,
        # Message properties
        priority: MessagePriority = MessagePriority.NORMAL,
        tags: list[str] | None = None,
        dependencies: list[str] | None = None,
        # Context and metadata
        context: dict[str, Any] | None = None,
        original_query: str | None = None,
    ):
        """Initialize TalkHier message."""

        # Core identification
        self.message_id = str(uuid.uuid4())
        self.conversation_id = conversation_id or str(uuid.uuid4())
        self.timestamp = datetime.now()

        # Routing information
        self.from_agent = from_agent
        self.to_agent = to_agent
        self.message_type = message_type

        # Structured content
        if isinstance(content, TalkHierContent):
            self.talkhier_content = content
        elif isinstance(content, dict):
            self.talkhier_content = TalkHierContent(**content)
        else:
            # Convert string content to structured format
            self.talkhier_content = TalkHierContent(content=str(content))

        # Hierarchical metadata
        self.hierarchy_metadata = hierarchy_metadata or HierarchyMetadata()

        # Refinement metadata
        self.refinement_metadata = refinement_metadata or RefinementMetadata()

        # Consensus requirements
        self.consensus_requirement = consensus_requirement

        # Message properties
        self.priority = priority
        self.tags = tags or []
        self.dependencies = dependencies or []

        # Additional context
        self.context = context or {}
        self.original_query = original_query

        # Performance tracking
        self.created_at = datetime.now()
        self.processed_at: datetime | None = None
        self.response_time_ms: int | None = None

    def to_legacy_message(self) -> AgentMessage:
        """Convert to legacy AgentMessage for backward compatibility."""

        # Flatten TalkHier content for legacy format
        legacy_content = {
            "content": self.talkhier_content.content,
            "background": self.talkhier_content.background,
            "intermediate_outputs": self.talkhier_content.intermediate_outputs,
            "confidence": self.talkhier_content.confidence_score,
            # TalkHier metadata
            "hierarchy_level": self.hierarchy_metadata.hierarchy_level,
            "refinement_round": self.refinement_metadata.round_number,
            "consensus_requirement": self.consensus_requirement.value,
            "priority": self.priority.value,
            "tags": self.tags,
        }

        return AgentMessage(
            from_agent=self.from_agent,
            to_agent=self.to_agent,
            message_type=self.message_type.value,
            content=legacy_content,
            timestamp=self.timestamp.timestamp(),
        )

    @classmethod
    def from_legacy_message(cls, legacy_message: AgentMessage) -> "TalkHierMessage":
        """Convert from legacy AgentMessage to TalkHier format."""

        content_data = legacy_message.content

        # Extract TalkHier content
        talkhier_content = TalkHierContent(
            content=content_data.get("content", ""),
            background=content_data.get("background", ""),
            intermediate_outputs=content_data.get("intermediate_outputs", {}),
            confidence_score=content_data.get("confidence", 1.0),
        )

        # Extract hierarchy metadata
        hierarchy_metadata = HierarchyMetadata(
            hierarchy_level=content_data.get("hierarchy_level", 1)
        )

        # Extract refinement metadata
        refinement_metadata = RefinementMetadata(
            round_number=content_data.get("refinement_round", 1)
        )

        # Determine message type
        try:
            message_type = MessageType(legacy_message.message_type)
        except ValueError:
            message_type = MessageType.RESPONSE

        # Determine consensus requirement
        try:
            consensus_req = ConsensusRequirement(
                content_data.get("consensus_requirement", "none")
            )
        except ValueError:
            consensus_req = ConsensusRequirement.NONE

        return cls(
            from_agent=legacy_message.from_agent,
            to_agent=legacy_message.to_agent,
            message_type=message_type,
            content=talkhier_content,
            hierarchy_metadata=hierarchy_metadata,
            refinement_metadata=refinement_metadata,
            consensus_requirement=consensus_req,
            tags=content_data.get("tags", []),
        )

    def is_broadcast(self) -> bool:
        """Check if message is broadcast to multiple agents."""
        return self.to_agent == "*" or "," in self.to_agent

    def get_target_agents(self) -> list[str]:
        """Get list of target agents for this message."""
        if self.to_agent == "*":
            return ["*"]  # Broadcast to all
        elif "," in self.to_agent:
            return [agent.strip() for agent in self.to_agent.split(",")]
        else:
            return [self.to_agent]

    def requires_refinement(self) -> bool:
        """Check if message is part of refinement process."""
        return self.refinement_metadata.round_number > 1

    def is_supervisor_message(self) -> bool:
        """Check if message is from a supervisor."""
        return self.hierarchy_metadata.hierarchy_level >= 2

    def is_worker_message(self) -> bool:
        """Check if message is from a worker."""
        return self.hierarchy_metadata.hierarchy_level == 1

    def get_consensus_score(self) -> float:
        """Get consensus score for this message."""
        return self.talkhier_content.confidence_score

    def mark_processed(self) -> None:
        """Mark message as processed."""
        self.processed_at = datetime.now()
        if self.created_at:
            self.response_time_ms = int(
                (self.processed_at - self.created_at).total_seconds() * 1000
            )

    def to_dict(self) -> dict[str, Any]:
        """Convert message to dictionary for serialization."""
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "timestamp": self.timestamp.isoformat(),
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "message_type": self.message_type.value,
            "talkhier_content": {
                "content": self.talkhier_content.content,
                "background": self.talkhier_content.background,
                "intermediate_outputs": self.talkhier_content.intermediate_outputs,
                "confidence_score": self.talkhier_content.confidence_score,
                "evidence": self.talkhier_content.evidence,
                "assumptions": self.talkhier_content.assumptions,
                "limitations": self.talkhier_content.limitations,
            },
            "hierarchy_metadata": {
                "hierarchy_level": self.hierarchy_metadata.hierarchy_level,
                "supervisor_id": self.hierarchy_metadata.supervisor_id,
                "worker_ids": self.hierarchy_metadata.worker_ids,
            },
            "refinement_metadata": {
                "round_number": self.refinement_metadata.round_number,
                "max_rounds": self.refinement_metadata.max_rounds,
                "quality_threshold": self.refinement_metadata.quality_threshold,
                "consensus_threshold": self.refinement_metadata.consensus_threshold,
            },
            "consensus_requirement": self.consensus_requirement.value,
            "priority": self.priority.value,
            "tags": self.tags,
            "dependencies": self.dependencies,
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TalkHierMessage":
        """Create message from dictionary."""

        # Reconstruct TalkHier content
        content_data = data.get("talkhier_content", {})
        talkhier_content = TalkHierContent(**content_data)

        # Reconstruct hierarchy metadata
        hierarchy_data = data.get("hierarchy_metadata", {})
        hierarchy_metadata = HierarchyMetadata(**hierarchy_data)

        # Reconstruct refinement metadata
        refinement_data = data.get("refinement_metadata", {})
        refinement_metadata = RefinementMetadata(**refinement_data)

        # Create message
        message = cls(
            from_agent=data["from_agent"],
            to_agent=data["to_agent"],
            message_type=MessageType(data["message_type"]),
            content=talkhier_content,
            hierarchy_metadata=hierarchy_metadata,
            refinement_metadata=refinement_metadata,
            consensus_requirement=ConsensusRequirement(
                data.get("consensus_requirement", "none")
            ),
            priority=MessagePriority(data.get("priority", 1)),
            tags=data.get("tags", []),
            dependencies=data.get("dependencies", []),
            context=data.get("context", {}),
        )

        # Set IDs and timestamp
        message.message_id = data["message_id"]
        message.conversation_id = data["conversation_id"]
        message.timestamp = datetime.fromisoformat(data["timestamp"])

        return message


__all__ = [
    "ConsensusRequirement",
    "HierarchyMetadata",
    "MessagePriority",
    "MessageType",
    "RefinementMetadata",
    "TalkHierContent",
    "TalkHierMessage",
]
