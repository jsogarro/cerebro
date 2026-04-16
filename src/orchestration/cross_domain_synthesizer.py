"""
Cross-Domain Synthesizer

Synthesizes results across multiple domains in multi-supervisor orchestration.
Provides multiple synthesis strategies (comprehensive, prioritized, consensus-based,
weighted average) and handles cross-domain conflict resolution.
"""

import logging
from datetime import datetime
from typing import Dict, Optional, Any, Callable, Awaitable

from ..ai_brain.integration.masr_supervisor_bridge import SupervisorExecutionResult

logger = logging.getLogger(__name__)


class CrossDomainSynthesizer:
    """Synthesizes results across multiple domains."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize cross-domain synthesizer."""
        self.config = config or {}

        # Synthesis strategies
        self.synthesis_strategies: Dict[
            str, Callable[[Dict[str, SupervisorExecutionResult]], Awaitable[Dict[str, Any]]]
        ] = {
            "comprehensive": self._comprehensive_synthesis,
            "prioritized": self._prioritized_synthesis,
            "consensus_based": self._consensus_based_synthesis,
            "weighted_average": self._weighted_average_synthesis,
        }

    async def synthesize_supervisor_results(
        self,
        supervisor_results: Dict[str, SupervisorExecutionResult],
        synthesis_strategy: str = "comprehensive",
    ) -> Dict[str, Any]:
        """
        Synthesize results from multiple supervisors.

        Args:
            supervisor_results: Results from each supervisor
            synthesis_strategy: Strategy for synthesis

        Returns:
            Synthesized result across all domains
        """

        if not supervisor_results:
            return {"error": "No supervisor results to synthesize"}

        synthesis_func = self.synthesis_strategies.get(
            synthesis_strategy, self._comprehensive_synthesis
        )

        try:
            synthesized = await synthesis_func(supervisor_results)

            # Add synthesis metadata
            synthesized["synthesis_metadata"] = {
                "strategy": synthesis_strategy,
                "supervisor_count": len(supervisor_results),
                "domains_covered": list(supervisor_results.keys()),
                "synthesis_timestamp": datetime.now().isoformat(),
                "overall_confidence": self._calculate_overall_confidence(supervisor_results),
            }

            return synthesized

        except Exception as e:
            logger.error(f"Cross-domain synthesis failed: {e}")
            return {
                "error": f"Synthesis failed: {str(e)}",
                "fallback_results": {
                    supervisor: result.agent_result.output if result.agent_result else {}
                    for supervisor, result in supervisor_results.items()
                },
            }

    async def _comprehensive_synthesis(
        self, supervisor_results: Dict[str, SupervisorExecutionResult]
    ) -> Dict[str, Any]:
        """Comprehensive synthesis combining all supervisor outputs."""

        synthesized = {
            "synthesis_type": "comprehensive",
            "domain_results": {},
            "cross_domain_insights": [],
            "integrated_conclusions": [],
            "quality_assessment": {},
        }

        # Aggregate domain results
        for supervisor_type, result in supervisor_results.items():
            if result.agent_result and result.agent_result.output:
                synthesized["domain_results"][supervisor_type] = {
                    "output": result.agent_result.output,
                    "quality_score": result.quality_score,
                    "consensus_score": result.consensus_score,
                    "execution_time": result.execution_time_seconds,
                }

        # Identify cross-domain patterns (simplified)
        if len(supervisor_results) > 1:
            synthesized["cross_domain_insights"] = [
                "Multiple domains analyzed for comprehensive coverage",
                f"Quality scores range from {min(r.quality_score for r in supervisor_results.values()):.3f} "
                f"to {max(r.quality_score for r in supervisor_results.values()):.3f}",
            ]

        # Generate integrated conclusions
        successful_results = [
            r for r in supervisor_results.values() if r.status.value == "completed"
        ]

        if successful_results:
            avg_quality = sum(r.quality_score for r in successful_results) / len(
                successful_results
            )
            synthesized["integrated_conclusions"] = [
                f"Successfully processed {len(successful_results)} domains",
                f"Average quality score: {avg_quality:.3f}",
                "Cross-domain analysis provides comprehensive perspective",
            ]

        return synthesized

    async def _prioritized_synthesis(
        self, supervisor_results: Dict[str, SupervisorExecutionResult]
    ) -> Dict[str, Any]:
        """Prioritized synthesis focusing on highest-quality results."""

        # Sort by quality score
        sorted_results = sorted(
            supervisor_results.items(), key=lambda x: x[1].quality_score, reverse=True
        )

        synthesized = {
            "synthesis_type": "prioritized",
            "primary_result": None,
            "supporting_results": [],
            "quality_ranking": [],
        }

        if sorted_results:
            primary_supervisor, primary_result = sorted_results[0]
            synthesized["primary_result"] = {
                "supervisor": primary_supervisor,
                "output": primary_result.agent_result.output
                if primary_result.agent_result
                else {},
                "quality_score": primary_result.quality_score,
            }

            # Add supporting results
            for supervisor, result in sorted_results[1:]:
                synthesized["supporting_results"].append(
                    {
                        "supervisor": supervisor,
                        "output": result.agent_result.output if result.agent_result else {},
                        "quality_score": result.quality_score,
                    }
                )

            synthesized["quality_ranking"] = [
                {"supervisor": supervisor, "quality": result.quality_score}
                for supervisor, result in sorted_results
            ]

        return synthesized

    async def _consensus_based_synthesis(
        self, supervisor_results: Dict[str, SupervisorExecutionResult]
    ) -> Dict[str, Any]:
        """Consensus-based synthesis focusing on agreement between supervisors."""

        synthesized = {
            "synthesis_type": "consensus_based",
            "consensus_areas": [],
            "conflicting_areas": [],
            "consensus_score": 0.0,
        }

        # Calculate overall consensus (simplified)
        consensus_scores = [r.consensus_score for r in supervisor_results.values()]
        if consensus_scores:
            synthesized["consensus_score"] = sum(consensus_scores) / len(consensus_scores)

        # Identify high-consensus areas
        high_consensus_threshold = 0.8
        high_consensus_results = [
            (supervisor, result)
            for supervisor, result in supervisor_results.items()
            if result.consensus_score >= high_consensus_threshold
        ]

        synthesized["consensus_areas"] = [
            {
                "supervisor": supervisor,
                "consensus_score": result.consensus_score,
                "result": result.agent_result.output if result.agent_result else {},
            }
            for supervisor, result in high_consensus_results
        ]

        # Identify conflicts
        low_consensus_results = [
            (supervisor, result)
            for supervisor, result in supervisor_results.items()
            if result.consensus_score < high_consensus_threshold
        ]

        synthesized["conflicting_areas"] = [
            {
                "supervisor": supervisor,
                "consensus_score": result.consensus_score,
                "issues": result.errors if result.errors else ["Low consensus achieved"],
            }
            for supervisor, result in low_consensus_results
        ]

        return synthesized

    async def _weighted_average_synthesis(
        self, supervisor_results: Dict[str, SupervisorExecutionResult]
    ) -> Dict[str, Any]:
        """Weighted average synthesis based on quality scores."""

        synthesized = {
            "synthesis_type": "weighted_average",
            "weighted_results": {},
            "quality_weights": {},
            "overall_weighted_score": 0.0,
        }

        # Calculate weights based on quality scores
        total_quality = sum(r.quality_score for r in supervisor_results.values())

        if total_quality > 0:
            for supervisor, result in supervisor_results.items():
                weight = result.quality_score / total_quality
                synthesized["quality_weights"][supervisor] = weight

                if result.agent_result and result.agent_result.output:
                    synthesized["weighted_results"][supervisor] = {
                        "output": result.agent_result.output,
                        "weight": weight,
                        "quality_score": result.quality_score,
                    }

            # Calculate overall weighted score
            synthesized["overall_weighted_score"] = sum(
                result.quality_score * synthesized["quality_weights"][supervisor]
                for supervisor, result in supervisor_results.items()
                if supervisor in synthesized["quality_weights"]
            )

        return synthesized

    def _calculate_overall_confidence(
        self, supervisor_results: Dict[str, SupervisorExecutionResult]
    ) -> float:
        """Calculate overall confidence across all supervisor results."""

        if not supervisor_results:
            return 0.0

        # Weighted confidence by quality scores
        total_quality = sum(r.quality_score for r in supervisor_results.values())

        if total_quality == 0:
            return 0.0

        weighted_confidence = (
            sum(r.quality_score * r.confidence_score for r in supervisor_results.values())
            / total_quality
        )

        return weighted_confidence


__all__ = ["CrossDomainSynthesizer"]
