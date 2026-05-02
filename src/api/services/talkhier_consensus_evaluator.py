"""Consensus evaluation for TalkHier sessions."""

from datetime import UTC, datetime
from typing import Any

from src.agents.communication.consensus_builder import ConsensusBuilder
from src.agents.communication.talkhier_message import (
    MessageType,
    TalkHierContent,
    TalkHierMessage,
)
from src.models.talkhier_api_models import (
    ConsensusCheckRequest,
    ConsensusResult,
    SessionStatus,
)


class TalkHierConsensusEvaluator:
    """Evaluates session consensus and consensus diagnostics."""

    def __init__(self, consensus_builder: ConsensusBuilder | None = None) -> None:
        self.consensus_builder = consensus_builder or ConsensusBuilder()

    async def check_consensus(
        self,
        session_id: str,
        session: Any,
        request: ConsensusCheckRequest,
    ) -> ConsensusResult:
        """Check consensus status in a session."""
        previous_status = session.status
        session.status = SessionStatus.CONSENSUS_CHECKING

        consensus_messages = []
        for result in request.round_results:
            msg = TalkHierMessage(
                from_agent=result.get("agent", "unknown"),
                to_agent="consensus_checker",
                content=TalkHierContent(
                    content=str(result.get("content", "")),
                    confidence_score=result.get("confidence", 0.5),
                ),
                message_type=MessageType.RESPONSE,
            )
            consensus_messages.append(msg)

        consensus_result = await self.consensus_builder.evaluate_consensus(
            consensus_messages,
            threshold=session.consensus_threshold,
        )
        has_consensus = consensus_result.overall_score >= session.consensus_threshold
        consensus_score = consensus_result.overall_score

        agreement_matrix = await self.calculate_agreement_matrix(request.round_results)

        quality_scores = {}
        for result in request.round_results:
            agent_id = result.get("agent", "unknown")
            quality_scores[agent_id] = result.get("confidence", 0.0)

        minority_reports = None
        if request.include_minority_report and not has_consensus:
            minority_reports = await self.generate_minority_reports(
                request.round_results,
                consensus_score,
            )

        recommendation = self.generate_consensus_recommendation(
            has_consensus,
            consensus_score,
            session,
        )
        reasoning = self.generate_consensus_reasoning(
            has_consensus,
            consensus_score,
            agreement_matrix,
            session,
        )

        session.status = previous_status
        session.last_update = datetime.now(UTC)

        return ConsensusResult(
            has_consensus=has_consensus,
            consensus_type=session.consensus_type,
            consensus_score=consensus_score,
            agreement_matrix=agreement_matrix,
            quality_scores=quality_scores,
            minority_reports=minority_reports,
            recommendation=recommendation,
            reasoning=reasoning,
        )

    async def calculate_agreement_matrix(
        self,
        results: list[dict[str, Any]],
    ) -> dict[str, dict[str, float]]:
        """Calculate pairwise agreement between participants."""
        matrix: dict[str, dict[str, float]] = {}

        for i, result1 in enumerate(results):
            agent1 = result1.get("agent", f"agent_{i}")
            matrix[agent1] = {}

            for j, result2 in enumerate(results):
                agent2 = result2.get("agent", f"agent_{j}")

                if i == j:
                    matrix[agent1][agent2] = 1.0
                else:
                    conf1 = result1.get("confidence", 0.5)
                    conf2 = result2.get("confidence", 0.5)
                    agreement = 1.0 - abs(conf1 - conf2)
                    matrix[agent1][agent2] = agreement

        return matrix

    async def generate_minority_reports(
        self,
        results: list[dict[str, Any]],
        consensus_score: float,
    ) -> list[dict[str, Any]]:
        """Generate minority opinion reports."""
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
                    "deviation": confidence - avg_confidence,
                })

        return minority_reports

    def generate_consensus_recommendation(
        self,
        has_consensus: bool,
        consensus_score: float,
        session: Any,
    ) -> str:
        """Generate recommendation based on consensus status."""
        if has_consensus:
            return "Consensus achieved - proceed with final result"

        if consensus_score >= session.consensus_threshold * 0.9:
            return "Near consensus - one more refinement round recommended"

        if consensus_score < 0.5:
            return "Low consensus - consider debate mode or supervisor intervention"

        return "Continue refinement to improve consensus"

    def generate_consensus_reasoning(
        self,
        has_consensus: bool,
        consensus_score: float,
        agreement_matrix: dict[str, dict[str, float]],
        session: Any,
    ) -> str:
        """Generate reasoning for consensus result."""
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

        high_agreement_pairs = []
        low_agreement_pairs = []

        for agent1, agreements in agreement_matrix.items():
            for agent2, score in agreements.items():
                if agent1 < agent2:
                    if score >= 0.8:
                        high_agreement_pairs.append((agent1, agent2))
                    elif score < 0.5:
                        low_agreement_pairs.append((agent1, agent2))

        if high_agreement_pairs:
            reasoning_parts.append(
                "Strong agreement between: "
                + ", ".join([f"{a1}-{a2}" for a1, a2 in high_agreement_pairs[:3]])
            )

        if low_agreement_pairs:
            reasoning_parts.append(
                "Disagreement between: "
                + ", ".join([f"{a1}-{a2}" for a1, a2 in low_agreement_pairs[:3]])
            )

        return " | ".join(reasoning_parts)


__all__ = ["TalkHierConsensusEvaluator"]
