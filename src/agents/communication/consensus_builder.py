"""
Consensus Builder for TalkHier Protocol

Implements consensus building and validation mechanisms from TalkHier research,
enabling multi-agent systems to achieve high-quality agreements through
structured refinement processes.

Key Features:
- Multi-agent consensus aggregation
- Confidence score combination algorithms
- Conflict detection and resolution
- Quality threshold validation
- Evidence-based decision making
"""

import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum

from .talkhier_message import TalkHierMessage, TalkHierContent, MessageType

logger = logging.getLogger(__name__)


class ConsensusMethod(Enum):
    """Methods for calculating consensus."""

    SIMPLE_AVERAGE = "simple_average"  # Average of all confidence scores
    WEIGHTED_AVERAGE = "weighted_average"  # Weighted by agent expertise
    MEDIAN = "median"  # Median confidence score
    VOTING = "voting"  # Democratic voting
    EVIDENCE_BASED = "evidence_based"  # Based on evidence quality


class ConflictType(Enum):
    """Types of conflicts between agent responses."""

    FACTUAL = "factual"  # Conflicting facts or data
    METHODOLOGICAL = "methodological"  # Different approaches
    INTERPRETIVE = "interpretive"  # Different interpretations
    CONFIDENCE = "confidence"  # Differing confidence levels
    SCOPE = "scope"  # Different scope of analysis


@dataclass
class ConsensusScore:
    """Consensus evaluation result."""

    overall_score: float = 0.0  # Overall consensus (0-1)
    confidence_variance: float = 0.0  # Variance in confidence scores
    agreement_areas: List[str] = field(default_factory=list)
    disagreement_areas: List[str] = field(default_factory=list)

    # Detailed scoring
    content_consensus: float = 0.0  # Agreement on main content
    methodology_consensus: float = 0.0  # Agreement on approach
    evidence_consensus: float = 0.0  # Agreement on evidence

    # Conflict analysis
    conflicts_detected: List[Dict[str, Any]] = field(default_factory=list)
    resolution_suggestions: List[str] = field(default_factory=list)

    # Quality metrics
    evidence_quality: float = 0.0  # Overall evidence quality
    response_completeness: float = 0.0  # Response completeness
    reasoning_soundness: float = 0.0  # Logical reasoning quality


@dataclass
class ValidationResult:
    """Result of response validation."""

    is_valid: bool = True
    quality_score: float = 0.0
    validation_errors: List[str] = field(default_factory=list)
    improvement_suggestions: List[str] = field(default_factory=list)

    # Specific validation checks
    factual_accuracy: float = 0.0
    logical_consistency: float = 0.0
    evidence_support: float = 0.0
    completeness: float = 0.0


class ConsensusBuilder:
    """
    Builds consensus from multiple agent responses using TalkHier protocol.

    Implements sophisticated consensus mechanisms including confidence aggregation,
    conflict resolution, and multi-round refinement coordination.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize consensus builder."""
        self.config = config or {}

        # Consensus configuration
        self.default_threshold = self.config.get("default_consensus_threshold", 0.95)
        self.max_refinement_rounds = self.config.get("max_refinement_rounds", 3)
        self.consensus_method = ConsensusMethod(
            self.config.get("consensus_method", "evidence_based")
        )

        # Validation configuration
        self.quality_threshold = self.config.get("quality_threshold", 0.8)
        self.evidence_weight = self.config.get("evidence_weight", 0.3)
        self.confidence_weight = self.config.get("confidence_weight", 0.3)
        self.consistency_weight = self.config.get("consistency_weight", 0.4)

        # Agent weighting (for weighted consensus)
        self.agent_weights = self.config.get("agent_weights", {})

        # Performance tracking
        self.consensus_attempts = 0
        self.successful_consensus = 0
        self.average_rounds_needed = 0.0

    async def evaluate_consensus(
        self, messages: List[TalkHierMessage], threshold: Optional[float] = None
    ) -> ConsensusScore:
        """
        Evaluate consensus across multiple agent messages.

        Args:
            messages: List of agent messages to evaluate
            threshold: Consensus threshold override

        Returns:
            ConsensusScore with detailed consensus analysis
        """

        threshold = threshold or self.default_threshold
        self.consensus_attempts += 1

        if not messages:
            return ConsensusScore()

        logger.info(f"Evaluating consensus across {len(messages)} agent responses")

        try:
            # Extract responses for analysis
            responses = [msg.talkhier_content for msg in messages]

            # Calculate overall consensus
            overall_score = await self._calculate_overall_consensus(responses)

            # Calculate confidence variance
            confidence_scores = [r.confidence_score for r in responses]
            confidence_variance = (
                statistics.variance(confidence_scores)
                if len(confidence_scores) > 1
                else 0.0
            )

            # Identify agreement and disagreement areas
            agreement_areas, disagreement_areas = await self._identify_agreement_areas(
                responses
            )

            # Calculate detailed consensus scores
            content_consensus = await self._calculate_content_consensus(responses)
            methodology_consensus = await self._calculate_methodology_consensus(
                responses
            )
            evidence_consensus = await self._calculate_evidence_consensus(responses)

            # Detect conflicts
            conflicts = await self._detect_conflicts(responses)

            # Generate resolution suggestions
            resolution_suggestions = await self._generate_resolution_suggestions(
                conflicts
            )

            # Assess quality metrics
            evidence_quality = await self._assess_evidence_quality(responses)
            completeness = await self._assess_response_completeness(responses)
            reasoning_soundness = await self._assess_reasoning_soundness(responses)

            consensus_score = ConsensusScore(
                overall_score=overall_score,
                confidence_variance=confidence_variance,
                agreement_areas=agreement_areas,
                disagreement_areas=disagreement_areas,
                content_consensus=content_consensus,
                methodology_consensus=methodology_consensus,
                evidence_consensus=evidence_consensus,
                conflicts_detected=conflicts,
                resolution_suggestions=resolution_suggestions,
                evidence_quality=evidence_quality,
                response_completeness=completeness,
                reasoning_soundness=reasoning_soundness,
            )

            if overall_score >= threshold:
                self.successful_consensus += 1

            logger.info(
                f"Consensus evaluation complete: {overall_score:.3f} "
                f"(threshold: {threshold:.3f})"
            )

            return consensus_score

        except Exception as e:
            logger.error(f"Consensus evaluation failed: {e}")
            return ConsensusScore()

    async def validate_response(
        self, message: TalkHierMessage, validation_criteria: Optional[List[str]] = None
    ) -> ValidationResult:
        """
        Validate individual agent response quality.

        Args:
            message: Agent message to validate
            validation_criteria: Specific validation criteria

        Returns:
            ValidationResult with quality assessment
        """

        validation_criteria = validation_criteria or [
            "factual_accuracy",
            "logical_consistency",
            "evidence_support",
            "completeness",
        ]

        try:
            response = message.talkhier_content

            # Basic validation checks
            is_valid = True
            validation_errors = []

            # Check content presence
            if not response.content.strip():
                is_valid = False
                validation_errors.append("Empty content")

            # Check confidence range
            if not 0.0 <= response.confidence_score <= 1.0:
                is_valid = False
                validation_errors.append("Invalid confidence score range")

            # Calculate quality scores
            factual_accuracy = await self._assess_factual_accuracy(response)
            logical_consistency = await self._assess_logical_consistency(response)
            evidence_support = await self._assess_evidence_support(response)
            completeness = await self._assess_completeness(response)

            # Overall quality score
            quality_score = (
                factual_accuracy * 0.3
                + logical_consistency * 0.3
                + evidence_support * 0.2
                + completeness * 0.2
            )

            # Generate improvement suggestions
            improvement_suggestions = []
            if factual_accuracy < 0.8:
                improvement_suggestions.append(
                    "Improve factual accuracy with better sources"
                )
            if logical_consistency < 0.8:
                improvement_suggestions.append("Enhance logical flow and reasoning")
            if evidence_support < 0.7:
                improvement_suggestions.append("Provide stronger evidence support")
            if completeness < 0.7:
                improvement_suggestions.append("Address all aspects of the query")

            return ValidationResult(
                is_valid=is_valid,
                quality_score=quality_score,
                validation_errors=validation_errors,
                improvement_suggestions=improvement_suggestions,
                factual_accuracy=factual_accuracy,
                logical_consistency=logical_consistency,
                evidence_support=evidence_support,
                completeness=completeness,
            )

        except Exception as e:
            logger.error(f"Response validation failed: {e}")
            return ValidationResult(
                is_valid=False,
                quality_score=0.0,
                validation_errors=[f"Validation error: {str(e)}"],
            )

    async def _calculate_overall_consensus(
        self, responses: List[TalkHierContent]
    ) -> float:
        """Calculate overall consensus score."""

        if not responses:
            return 0.0

        if len(responses) == 1:
            return responses[0].confidence_score

        if self.consensus_method == ConsensusMethod.SIMPLE_AVERAGE:
            return statistics.mean([r.confidence_score for r in responses])

        elif self.consensus_method == ConsensusMethod.WEIGHTED_AVERAGE:
            # Would implement agent-specific weighting
            return statistics.mean([r.confidence_score for r in responses])

        elif self.consensus_method == ConsensusMethod.MEDIAN:
            return statistics.median([r.confidence_score for r in responses])

        elif self.consensus_method == ConsensusMethod.EVIDENCE_BASED:
            # Weight by evidence quality
            total_weighted_score = 0.0
            total_weight = 0.0

            for response in responses:
                evidence_weight = (
                    len(response.evidence) / 10.0
                )  # Simple evidence weighting
                evidence_weight = min(evidence_weight, 1.0)

                total_weighted_score += response.confidence_score * evidence_weight
                total_weight += evidence_weight

            return total_weighted_score / max(total_weight, 1.0)

        else:  # VOTING
            # Simple majority voting based on high confidence
            high_confidence_count = sum(
                1 for r in responses if r.confidence_score > 0.7
            )
            return high_confidence_count / len(responses)

    async def _identify_agreement_areas(
        self, responses: List[TalkHierContent]
    ) -> Tuple[List[str], List[str]]:
        """Identify areas of agreement and disagreement."""

        if len(responses) < 2:
            return [], []

        # Simple keyword-based agreement detection
        # In production, this would use more sophisticated NLP

        all_contents = [r.content.lower() for r in responses]

        # Find common keywords/phrases
        agreement_areas = []
        disagreement_areas = []

        # Simple heuristic: if >75% of responses mention similar concepts
        for response in responses:
            words = set(response.content.lower().split())

            # Check overlap with other responses
            overlaps = []
            for other_response in responses:
                if other_response == response:
                    continue

                other_words = set(other_response.content.lower().split())
                overlap = (
                    len(words & other_words) / len(words | other_words)
                    if words | other_words
                    else 0
                )
                overlaps.append(overlap)

            avg_overlap = statistics.mean(overlaps) if overlaps else 0

            if avg_overlap > 0.75:
                agreement_areas.extend(list(words)[:3])  # Add some keywords
            elif avg_overlap < 0.3:
                disagreement_areas.extend(list(words)[:3])

        # Remove duplicates and limit length
        agreement_areas = list(set(agreement_areas))[:10]
        disagreement_areas = list(set(disagreement_areas))[:10]

        return agreement_areas, disagreement_areas

    async def _calculate_content_consensus(
        self, responses: List[TalkHierContent]
    ) -> float:
        """Calculate consensus on main content."""
        # Simplified content similarity calculation
        if len(responses) < 2:
            return 1.0

        similarities = []
        for i, response1 in enumerate(responses):
            for j, response2 in enumerate(responses[i + 1 :], i + 1):
                # Simple word overlap similarity
                words1 = set(response1.content.lower().split())
                words2 = set(response2.content.lower().split())

                if words1 | words2:
                    similarity = len(words1 & words2) / len(words1 | words2)
                else:
                    similarity = 1.0

                similarities.append(similarity)

        return statistics.mean(similarities) if similarities else 0.0

    async def _calculate_methodology_consensus(
        self, responses: List[TalkHierContent]
    ) -> float:
        """Calculate consensus on methodology and approach."""
        # Look for methodological agreement in background/intermediate outputs
        method_scores = []

        for response in responses:
            # Check if methodology is mentioned in background or intermediate outputs
            background_lower = response.background.lower()
            intermediate_text = str(response.intermediate_outputs).lower()

            method_indicators = [
                "methodology",
                "approach",
                "method",
                "framework",
                "analysis",
                "technique",
                "procedure",
            ]

            method_mentions = sum(
                1
                for indicator in method_indicators
                if indicator in background_lower or indicator in intermediate_text
            )

            method_scores.append(min(method_mentions / len(method_indicators), 1.0))

        return statistics.mean(method_scores) if method_scores else 0.5

    async def _calculate_evidence_consensus(
        self, responses: List[TalkHierContent]
    ) -> float:
        """Calculate consensus on evidence and citations."""
        evidence_scores = []

        for response in responses:
            # Count evidence items
            evidence_count = len(response.evidence)
            evidence_quality = min(
                evidence_count / 5.0, 1.0
            )  # Normalize to 5 pieces of evidence
            evidence_scores.append(evidence_quality)

        if not evidence_scores:
            return 0.0

        # Consensus based on similar evidence quality levels
        mean_evidence = statistics.mean(evidence_scores)
        variance_evidence = (
            statistics.variance(evidence_scores) if len(evidence_scores) > 1 else 0.0
        )

        # Higher variance = lower consensus
        consensus = max(0.0, 1.0 - variance_evidence)

        return consensus

    async def _detect_conflicts(
        self, responses: List[TalkHierContent]
    ) -> List[Dict[str, Any]]:
        """Detect conflicts between agent responses."""

        conflicts = []

        if len(responses) < 2:
            return conflicts

        # Confidence conflicts
        confidence_scores = [r.confidence_score for r in responses]
        if max(confidence_scores) - min(confidence_scores) > 0.4:
            conflicts.append(
                {
                    "type": ConflictType.CONFIDENCE.value,
                    "description": f"Large confidence variance: {max(confidence_scores):.2f} vs {min(confidence_scores):.2f}",
                    "severity": "medium",
                    "agents_involved": [f"response_{i}" for i in range(len(responses))],
                }
            )

        # Content length conflicts (may indicate different scope)
        content_lengths = [len(r.content.split()) for r in responses]
        if max(content_lengths) > min(content_lengths) * 3:
            conflicts.append(
                {
                    "type": ConflictType.SCOPE.value,
                    "description": "Significant difference in response scope/depth",
                    "severity": "medium",
                    "agents_involved": [f"response_{i}" for i in range(len(responses))],
                }
            )

        # Evidence conflicts
        evidence_counts = [len(r.evidence) for r in responses]
        if evidence_counts and max(evidence_counts) > 0 and min(evidence_counts) == 0:
            conflicts.append(
                {
                    "type": ConflictType.EVIDENCE.value,
                    "description": "Inconsistent evidence support across responses",
                    "severity": "high",
                    "agents_involved": [f"response_{i}" for i in range(len(responses))],
                }
            )

        return conflicts

    async def _generate_resolution_suggestions(
        self, conflicts: List[Dict[str, Any]]
    ) -> List[str]:
        """Generate suggestions for resolving identified conflicts."""

        suggestions = []

        for conflict in conflicts:
            conflict_type = conflict["type"]
            severity = conflict["severity"]

            if conflict_type == ConflictType.CONFIDENCE.value:
                suggestions.append(
                    "Request additional validation from agents with low confidence"
                )
                suggestions.append("Provide more specific evidence to support claims")

            elif conflict_type == ConflictType.SCOPE.value:
                suggestions.append("Clarify scope requirements for all agents")
                suggestions.append("Request standardized response depth")

            elif conflict_type == ConflictType.EVIDENCE.value:
                suggestions.append("Require evidence support from all agents")
                suggestions.append("Cross-validate evidence sources")

            # Add severity-based suggestions
            if severity == "high":
                suggestions.append("Consider additional expert consultation")

        return list(set(suggestions))  # Remove duplicates

    async def _assess_evidence_quality(self, responses: List[TalkHierContent]) -> float:
        """Assess overall evidence quality."""

        if not responses:
            return 0.0

        evidence_scores = []

        for response in responses:
            # Simple evidence quality heuristic
            evidence_count = len(response.evidence)

            # Quality based on evidence diversity and quantity
            if evidence_count == 0:
                score = 0.0
            elif evidence_count <= 2:
                score = 0.5
            elif evidence_count <= 5:
                score = 0.8
            else:
                score = 1.0

            evidence_scores.append(score)

        return statistics.mean(evidence_scores)

    async def _assess_response_completeness(
        self, responses: List[TalkHierContent]
    ) -> float:
        """Assess response completeness."""

        completeness_scores = []

        for response in responses:
            # Check if all sections are filled
            score = 0.0

            if response.content.strip():
                score += 0.4

            if response.background.strip():
                score += 0.3

            if response.intermediate_outputs:
                score += 0.3

            completeness_scores.append(score)

        return statistics.mean(completeness_scores) if completeness_scores else 0.0

    async def _assess_reasoning_soundness(
        self, responses: List[TalkHierContent]
    ) -> float:
        """Assess logical reasoning quality."""

        # Simple heuristic based on response structure and coherence
        reasoning_scores = []

        for response in responses:
            score = 0.5  # Base score

            # Check for logical indicators
            logical_indicators = [
                "because",
                "therefore",
                "thus",
                "however",
                "moreover",
                "consequently",
                "furthermore",
                "in conclusion",
            ]

            content_lower = response.content.lower()
            logical_count = sum(
                1 for indicator in logical_indicators if indicator in content_lower
            )

            # Normalize to 0-0.5 range and add to base
            logical_bonus = min(logical_count / len(logical_indicators), 0.5)
            score += logical_bonus

            reasoning_scores.append(score)

        return statistics.mean(reasoning_scores) if reasoning_scores else 0.5

    async def _assess_factual_accuracy(self, response: TalkHierContent) -> float:
        """Assess factual accuracy of response."""
        # Simplified implementation - in production would use fact-checking tools

        # Check for evidence support
        if response.evidence:
            return 0.8 + (len(response.evidence) / 10.0) * 0.2
        else:
            return 0.5  # Medium confidence without evidence

    async def _assess_logical_consistency(self, response: TalkHierContent) -> float:
        """Assess logical consistency."""
        # Check for contradictory statements (simplified)

        content_lower = response.content.lower()

        # Look for contradiction indicators
        contradiction_indicators = ["but", "however", "although", "despite", "contrary"]
        contradiction_count = sum(
            1 for indicator in contradiction_indicators if indicator in content_lower
        )

        # Some contradiction is normal, too much suggests inconsistency
        if contradiction_count == 0:
            return 0.8  # May be too simplistic
        elif contradiction_count <= 2:
            return 0.9  # Good balance
        else:
            return 0.6  # May be inconsistent

    async def _assess_evidence_support(self, response: TalkHierContent) -> float:
        """Assess evidence support quality."""

        evidence_count = len(response.evidence)

        # Quality based on evidence quantity and presence
        if evidence_count == 0:
            return 0.2
        elif evidence_count <= 2:
            return 0.6
        elif evidence_count <= 5:
            return 0.9
        else:
            return 1.0

    async def _assess_completeness(self, response: TalkHierContent) -> float:
        """Assess response completeness."""

        score = 0.0

        # Content completeness
        if len(response.content) > 100:
            score += 0.4
        elif len(response.content) > 50:
            score += 0.2

        # Background presence
        if response.background.strip():
            score += 0.3

        # Intermediate outputs presence
        if response.intermediate_outputs:
            score += 0.3

        return min(score, 1.0)

    async def get_consensus_stats(self) -> Dict[str, Any]:
        """Get consensus builder performance statistics."""

        success_rate = self.successful_consensus / max(self.consensus_attempts, 1)

        return {
            "consensus_attempts": self.consensus_attempts,
            "successful_consensus": self.successful_consensus,
            "success_rate": success_rate,
            "average_rounds_needed": self.average_rounds_needed,
            "consensus_method": self.consensus_method.value,
            "default_threshold": self.default_threshold,
        }


__all__ = [
    "ConsensusBuilder",
    "ConsensusScore",
    "ValidationResult",
    "ConsensusMethod",
    "ConflictType",
]
