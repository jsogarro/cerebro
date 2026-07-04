"""
Advanced Agent Communication System

Implements TalkHier-inspired communication protocol enhanced with LangGraph
integration for sophisticated hierarchical agent coordination.

Key Components:
- TalkHierMessage: Enhanced message structure with 3-part content format
- CommunicationProtocol: Hierarchical routing and consensus building
- RefinementWorkflow: Multi-round iterative improvement protocol
- ConsensusBuilder: Aggregation and validation of agent responses
"""

from .communication_protocol import CommunicationProtocol, RefinementResult
from .consensus_builder import ConsensusBuilder, ConsensusScore
from .talkhier_message import ConsensusRequirement, MessageType, TalkHierMessage

__all__ = [
    "CommunicationProtocol",
    "ConsensusBuilder",
    "ConsensusRequirement",
    "ConsensusScore",
    "MessageType",
    "RefinementResult",
    "TalkHierMessage",
]
