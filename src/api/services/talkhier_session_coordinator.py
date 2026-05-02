"""Session coordination helpers for TalkHier sessions."""

from typing import Any

from src.agents.supervisors.base_supervisor import BaseSupervisor
from src.agents.supervisors.supervisor_factory import SupervisorFactory
from src.ai_brain.integration.masr_supervisor_bridge import MASRSupervisorBridge
from src.ai_brain.router.masr import MASRouter, RoutingDecision
from src.models.talkhier_api_models import MessageRole, ParticipantInfo, ProtocolType


class TalkHierSessionCoordinator:
    """Coordinates routing, supervisor creation, participants, and session estimates."""

    def __init__(
        self,
        supervisor_factory: SupervisorFactory,
        masr_bridge: MASRSupervisorBridge,
        masr_router: MASRouter,
    ) -> None:
        self.supervisor_factory = supervisor_factory
        self.masr_bridge = masr_bridge
        self.masr_router = masr_router

    def initialize_protocol_configs(self) -> dict[ProtocolType, dict[str, Any]]:
        """Initialize protocol configurations."""
        return {
            ProtocolType.STANDARD: {
                "default_rounds": 3,
                "quality_weight": 0.6,
                "consensus_weight": 0.4,
                "timeout_multiplier": 1.0,
            },
            ProtocolType.FAST_TRACK: {
                "default_rounds": 2,
                "quality_weight": 0.5,
                "consensus_weight": 0.5,
                "timeout_multiplier": 0.5,
            },
            ProtocolType.DEEP_ANALYSIS: {
                "default_rounds": 5,
                "quality_weight": 0.7,
                "consensus_weight": 0.3,
                "timeout_multiplier": 2.0,
            },
            ProtocolType.COLLABORATIVE: {
                "default_rounds": 3,
                "quality_weight": 0.4,
                "consensus_weight": 0.6,
                "timeout_multiplier": 1.0,
            },
            ProtocolType.SUPERVISED: {
                "default_rounds": 3,
                "quality_weight": 0.5,
                "consensus_weight": 0.5,
                "timeout_multiplier": 1.0,
                "supervisor_weight": 2.0,
            },
        }

    async def get_routing_decision(
        self,
        query: str,
        domains: list[str],
    ) -> RoutingDecision:
        """Get routing decision from MASR."""
        return await self.masr_router.route(query, context={"domains": domains})

    async def create_supervisor(
        self,
        routing_decision: RoutingDecision,
        requested_type: str | None,
    ) -> BaseSupervisor | None:
        """Create supervisor based on routing decision."""
        supervisor_type = self.resolve_supervisor_type(routing_decision, requested_type)

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

    def resolve_supervisor_type(
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

    async def determine_participants(
        self,
        requested: list[str] | None,
        routing_decision: RoutingDecision,
        supervisor: BaseSupervisor | None,
    ) -> list[ParticipantInfo]:
        """Determine session participants."""
        participants = []
        agent_ids = requested or []

        if not agent_ids and routing_decision.agent_allocation:
            agent_ids = routing_decision.agent_allocation.worker_types
            if not isinstance(agent_ids, list):
                agent_ids = [
                    agent.agent_type
                    for agent in getattr(routing_decision.agent_allocation, "agents", [])
                    if isinstance(getattr(agent, "agent_type", None), str)
                ]

        for agent_id in agent_ids:
            participants.append(ParticipantInfo(
                agent_id=agent_id,
                agent_type=agent_id,
                role=MessageRole.WORKER,
                confidence=0.5,
                rounds_participated=0,
                quality_scores=[],
            ))

        if supervisor:
            participants.append(ParticipantInfo(
                agent_id="supervisor",
                agent_type=supervisor.__class__.__name__,
                role=MessageRole.SUPERVISOR,
                confidence=0.8,
                rounds_participated=0,
                quality_scores=[],
            ))

        return participants

    def estimate_duration(
        self,
        max_rounds: int,
        participant_count: int,
        protocol_config: dict[str, Any],
    ) -> int:
        """Estimate session duration in seconds."""
        base_duration = 30
        participant_factor = participant_count * 10
        round_duration = base_duration + participant_factor
        total_duration = max_rounds * round_duration
        timeout_multiplier = protocol_config.get("timeout_multiplier", 1.0)
        return int(total_duration * timeout_multiplier)


__all__ = ["TalkHierSessionCoordinator"]
