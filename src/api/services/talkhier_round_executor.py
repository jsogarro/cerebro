"""Round execution for TalkHier sessions."""

from datetime import UTC, datetime
from typing import Any

from structlog import get_logger

from src.api.services.talkhier_state_manager import TalkHierStateManager
from src.models.talkhier_api_models import (
    ConsensusType,
    MessageRole,
    RefinementRound,
    RefinementRoundRequest,
    RefinementRoundResponse,
    RefinementStrategy,
    SessionStatus,
)

logger = get_logger()


class TalkHierRoundExecutor:
    """Executes refinement rounds and computes round-level outcomes."""

    def __init__(self) -> None:
        self._gemini_service: Any | None = None

    async def execute_refinement_round(
        self,
        session_id: str,
        session: Any,
        request: RefinementRoundRequest,
        state_manager: TalkHierStateManager,
    ) -> RefinementRoundResponse:
        """Execute a refinement round in an active session."""
        if session.status != SessionStatus.ACTIVE:
            raise ValueError(
                f"Session {session_id} is not active (status: {session.status})"
            )
        if request.round_number > session.max_rounds:
            raise ValueError(
                f"Refinement round {request.round_number} exceeds max_rounds={session.max_rounds}"
            )

        session.status = SessionStatus.REFINING
        session.current_round = request.round_number
        round_start = datetime.now(UTC)

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
            result=None,
        )

        if session.supervisor:
            refinement_result = await self.execute_supervisor_refinement(
                session,
                request,
                round_record,
            )
            participant_responses = refinement_result["responses"]
        else:
            participant_responses = await self.execute_direct_refinement(
                session,
                request,
                round_record,
            )

        aggregated_result = await self.aggregate_refinement_results(
            participant_responses,
            session.refinement_strategy,
        )
        quality_score = await self.calculate_quality_score(
            aggregated_result,
            session.quality_threshold,
        )
        consensus_score = await self.calculate_consensus_score(
            participant_responses,
            session.consensus_type,
        )

        previous_quality = session.rounds[-1].quality_score if session.rounds else 0.0
        improvement_delta = quality_score - previous_quality

        round_record.completed_at = datetime.now(UTC)
        round_record.status = "completed"
        round_record.quality_score = quality_score
        round_record.consensus_score = consensus_score
        round_record.refinement_delta = improvement_delta
        round_record.result = aggregated_result
        session.rounds.append(round_record)

        session.current_result = aggregated_result
        session.current_quality = quality_score
        session.current_consensus = consensus_score
        session.status = SessionStatus.ACTIVE
        session.last_update = datetime.now(UTC)

        state_manager.record_round(session_id, quality_score, consensus_score)

        continue_refinement = self.should_continue_refinement(session)
        refinement_suggestion = None
        if continue_refinement:
            refinement_suggestion = await self.generate_refinement_suggestion(
                session,
                participant_responses,
                aggregated_result,
            )

        duration_ms = max(
            1,
            int((datetime.now(UTC) - round_start).total_seconds() * 1000),
        )

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
            refinement_suggestion=refinement_suggestion,
        )

    async def execute_supervisor_refinement(
        self,
        session: Any,
        request: RefinementRoundRequest,
        round_record: RefinementRound,
    ) -> dict[str, Any]:
        """Execute refinement by invoking each worker's real agent."""
        responses = await self._invoke_worker_agents(session, request)
        return {"responses": responses}

    async def execute_direct_refinement(
        self,
        session: Any,
        request: RefinementRoundRequest,
        round_record: RefinementRound,
    ) -> dict[str, dict[str, Any]]:
        """Execute direct agent refinement by invoking each worker's real agent."""
        return await self._invoke_worker_agents(session, request)

    async def _invoke_worker_agents(
        self, session: Any, request: RefinementRoundRequest
    ) -> dict[str, dict[str, Any]]:
        """Run every worker participant's real agent for this round."""
        responses: dict[str, dict[str, Any]] = {}
        for participant in session.participants:
            if participant.role != MessageRole.WORKER:
                continue
            responses[participant.agent_id] = await self._invoke_worker_agent(
                session, participant, request
            )
        return responses

    async def _invoke_worker_agent(
        self, session: Any, participant: Any, request: RefinementRoundRequest
    ) -> dict[str, Any]:
        """Invoke a single worker's agent and extract its refinement response."""
        from src.agents.factory import AgentFactory
        from src.agents.models import AgentTask

        agent_type = self._resolve_agent_type(participant.agent_type)
        previous = ""
        if session.current_result:
            previous = str(session.current_result.get("content", ""))

        try:
            agent = AgentFactory.create_agent(
                agent_type, {"gemini_service": self._get_gemini_service()}
            )
            task = AgentTask(
                id=f"talkhier_{session.session_id}_{participant.agent_id}_r{request.round_number}",
                agent_type=agent_type,
                input_data={
                    "query": session.query,
                    "previous_result": previous,
                    "refinement_focus": request.refinement_focus or "",
                    "round_number": request.round_number,
                },
            )
            result = await agent.execute(task)
            return {
                "content": self._extract_content(result.output),
                "confidence": float(getattr(result, "confidence", 0.7)),
                "evidence": self._extract_evidence(result.output),
            }
        except Exception as exc:
            logger.warning(
                "talkhier_worker_invocation_failed",
                agent_id=participant.agent_id,
                agent_type=agent_type,
                error=str(exc),
            )
            return {
                "content": "",
                "confidence": 0.0,
                "evidence": [],
                "error": str(exc),
            }

    @staticmethod
    def _resolve_agent_type(agent_type: str) -> str:
        """Map a participant's agent type to a concrete agent-registry key."""
        valid = {
            "literature_review",
            "comparative_analysis",
            "methodology",
            "synthesis",
            "citation",
        }
        if agent_type in valid:
            return agent_type
        lowered = (agent_type or "").lower()
        for key in valid:
            if key.split("_")[0] in lowered:
                return key
        if "analy" in lowered:
            return "comparative_analysis"
        if "cit" in lowered:
            return "citation"
        if "method" in lowered:
            return "methodology"
        if "synth" in lowered:
            return "synthesis"
        # Default to a self-contained agent (literature review works standalone;
        # synthesis needs prior agent outputs and would fail for a solo worker).
        return "literature_review"

    @staticmethod
    def _extract_content(output: Any) -> str:
        """Pull a human-readable content string out of an agent's output."""
        if isinstance(output, str):
            return output
        if isinstance(output, dict):
            for key in ("content", "summary", "narrative", "synthesis", "analysis"):
                value = output.get(key)
                if isinstance(value, str) and value:
                    return value
            findings = output.get("key_findings")
            if isinstance(findings, list) and findings:
                return " ".join(str(f) for f in findings)
            return str(output)
        return str(output)

    @staticmethod
    def _extract_evidence(output: Any) -> list[Any]:
        """Pull an evidence/source list out of an agent's output, if present."""
        if isinstance(output, dict):
            for key in ("evidence", "sources", "sources_found", "citations"):
                value = output.get(key)
                if isinstance(value, list):
                    return value
        return []

    def _get_gemini_service(self) -> Any:
        """Lazily build a Gemini service for worker agents; None on failure."""
        if self._gemini_service is None:
            try:
                from src.core.config import settings
                from src.services.gemini_service import GeminiService

                self._gemini_service = GeminiService(api_key=settings.GEMINI_API_KEY)
            except Exception as exc:  # pragma: no cover - env-dependent
                logger.warning("gemini_service_unavailable", error=str(exc))
                self._gemini_service = None
        return self._gemini_service

    async def aggregate_refinement_results(
        self,
        responses: dict[str, dict[str, Any]],
        strategy: RefinementStrategy,
    ) -> dict[str, Any]:
        """Aggregate participant responses based on strategy."""
        if not responses:
            return {
                "content": "",
                "confidence": 0.0,
                "aggregation_method": "empty",
            }

        if strategy == RefinementStrategy.QUALITY_FOCUSED:
            best_response = max(
                responses.items(),
                key=lambda x: x[1].get("confidence", 0),
            )
            return best_response[1]

        if strategy == RefinementStrategy.CONSENSUS_DRIVEN:
            merged_content = "\n".join(
                [r.get("content", "") for r in responses.values()]
            )
            avg_confidence = sum(
                r.get("confidence", 0) for r in responses.values()
            ) / max(1, len(responses))

            return {
                "content": merged_content,
                "confidence": avg_confidence,
                "aggregation_method": "consensus",
            }

        return {
            "responses": responses,
            "aggregation_method": strategy.value,
        }

    async def calculate_quality_score(
        self,
        result: dict[str, Any],
        threshold: float,
    ) -> float:
        """Calculate quality score for result."""
        base_quality = float(result.get("confidence", 0.5))
        evidence_bonus = 0.1 if result.get("evidence") else 0.0
        content = str(result.get("content", ""))
        length_factor = min(1.0, len(content) / 1000)
        quality = (base_quality + evidence_bonus) * (0.7 + 0.3 * length_factor)
        return float(min(1.0, quality))

    async def calculate_consensus_score(
        self,
        responses: dict[str, dict[str, Any]],
        consensus_type: ConsensusType,
    ) -> float:
        """Calculate consensus score among responses."""
        if len(responses) <= 1:
            return 1.0

        confidences = [r.get("confidence", 0) for r in responses.values()]

        if consensus_type == ConsensusType.MAJORITY:
            avg_confidence = sum(confidences) / len(confidences)
            variance = sum((c - avg_confidence) ** 2 for c in confidences) / len(
                confidences
            )
            consensus = 1.0 - min(1.0, variance)
        elif consensus_type == ConsensusType.WEIGHTED:
            weighted_sum = sum(c * c for c in confidences)
            total_weight = sum(c for c in confidences)
            consensus = weighted_sum / max(1, total_weight)
        elif consensus_type == ConsensusType.UNANIMOUS:
            consensus = 1.0 if all(c >= 0.8 for c in confidences) else 0.0
        else:
            high_confidence_count = sum(1 for c in confidences if c >= 0.7)
            consensus = float(high_confidence_count) / float(len(confidences))

        return float(consensus)

    def should_continue_refinement(self, session: Any) -> bool:
        """Determine if refinement should continue."""
        if session.current_round >= session.max_rounds:
            return False

        if session.current_round < session.min_rounds:
            return True

        if (
            session.current_quality >= session.quality_threshold
            and session.current_consensus >= session.consensus_threshold
        ):
            return False

        if len(session.rounds) >= 2:
            recent_improvements = [r.refinement_delta for r in session.rounds[-2:]]
            if all(delta < 0.01 for delta in recent_improvements):
                return False

        return True

    async def generate_refinement_suggestion(
        self,
        session: Any,
        responses: dict[str, dict[str, Any]],
        result: dict[str, Any],
    ) -> str:
        """Generate suggestion for next refinement round."""
        suggestions = []

        quality_gap = session.quality_threshold - session.current_quality
        if quality_gap > 0.2:
            suggestions.append("Focus on improving evidence and supporting data")
        elif quality_gap > 0.1:
            suggestions.append("Refine clarity and strengthen key arguments")

        consensus_gap = session.consensus_threshold - session.current_consensus
        if consensus_gap > 0.2:
            suggestions.append("Address disagreements between participants")
        elif consensus_gap > 0.1:
            suggestions.append("Align perspectives on key points")

        low_confidence = [
            agent_id
            for agent_id, resp in responses.items()
            if resp.get("confidence", 0) < 0.7
        ]
        if low_confidence:
            suggestions.append(
                f"Strengthen responses from: {', '.join(low_confidence)}"
            )

        return " | ".join(suggestions) if suggestions else "Continue general refinement"


__all__ = ["TalkHierRoundExecutor"]
