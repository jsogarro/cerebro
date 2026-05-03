"""
Inter-Supervisor Communicator

Manages communication and coordination between supervisors in multi-supervisor
orchestration. Handles supervisor handoffs, coordination messages, and maintains
message history for cross-supervisor collaboration.
"""

from datetime import datetime
from typing import Any

from structlog import get_logger

from ..agents.communication.talkhier_message import (
    HierarchyMetadata,
    MessageType,
    TalkHierContent,
    TalkHierMessage,
)

logger = get_logger()


class InterSupervisorCommunicator:
    """Manages communication between supervisors."""

    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize inter-supervisor communicator."""
        self.config = config or {}

        # Communication tracking
        self.message_history: list[TalkHierMessage] = []
        self.active_conversations: dict[str, list[TalkHierMessage]] = {}

    async def coordinate_supervisor_handoff(
        self, from_supervisor: str, to_supervisor: str, handoff_data: dict[str, Any]
    ) -> bool:
        """Coordinate data handoff between supervisors."""

        try:
            # Create handoff message
            handoff_content = TalkHierContent(
                content=f"Data handoff from {from_supervisor} to {to_supervisor}",
                background="Cross-supervisor coordination for multi-domain query",
                intermediate_outputs=handoff_data,
                confidence_score=handoff_data.get("confidence", 0.85),
            )

            message = TalkHierMessage(
                from_agent=from_supervisor,
                to_agent=to_supervisor,
                message_type=MessageType.COORDINATION,
                content=handoff_content,
                hierarchy_metadata=HierarchyMetadata(
                    hierarchy_level=2,  # Supervisor level
                ),
                conversation_id=f"handoff_{from_supervisor}_{to_supervisor}",
            )

            # Track message
            self.message_history.append(message)

            conversation_key = f"{from_supervisor}-{to_supervisor}"
            if conversation_key not in self.active_conversations:
                self.active_conversations[conversation_key] = []
            self.active_conversations[conversation_key].append(message)

            logger.info(f"Coordinated handoff from {from_supervisor} to {to_supervisor}")
            return True

        except Exception as e:
            logger.error(f"Supervisor handoff failed: {e}")
            return False

    async def broadcast_coordination_message(
        self, supervisors: list[str], coordination_data: dict[str, Any]
    ) -> list[TalkHierMessage]:
        """Broadcast coordination message to multiple supervisors."""

        messages = []

        for supervisor in supervisors:
            content = TalkHierContent(
                content="Multi-supervisor coordination update",
                background="Coordinating across multiple domain supervisors",
                intermediate_outputs=coordination_data,
            )

            message = TalkHierMessage(
                from_agent="multi_supervisor_orchestrator",
                to_agent=supervisor,
                message_type=MessageType.COORDINATION,
                content=content,
                conversation_id=f"broadcast_{datetime.now().timestamp()}",
            )

            messages.append(message)
            self.message_history.append(message)

        logger.info(f"Broadcast coordination to {len(supervisors)} supervisors")
        return messages


__all__ = ["InterSupervisorCommunicator"]
