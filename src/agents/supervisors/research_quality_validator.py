"""Quality validation helpers for the research supervisor."""

from collections.abc import Callable
from typing import Any

from structlog import get_logger

from ..communication.talkhier_message import (
    MessageType,
    TalkHierContent,
    TalkHierMessage,
)
from .base_supervisor import SupervisionState

logger = get_logger()


class ResearchQualityValidator:
    """Validates research sources, paper quality, and consensus."""

    def __init__(
        self,
        gemini_service: Any | None,
        communication_protocol: Any,
        get_agent_type: Callable[[], str],
        quality_threshold: float,
    ) -> None:
        self.gemini_service = gemini_service
        self.communication_protocol = communication_protocol
        self.get_agent_type = get_agent_type
        self.quality_threshold = quality_threshold

    async def validate_sources(
        self,
        langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate literature sources to catch hallucinated papers."""
        state = langgraph_state["supervision_state"]
        logger.info("research_quality_phase_started", phase="source_validation")
        state.current_phase = "source_validation"

        lit_result = state.worker_results.get("literature_review")
        if not lit_result or not hasattr(lit_result, "intermediate_outputs"):
            logger.warning("research_source_validation_missing_literature_results")
            langgraph_state["supervision_state"] = state
            return langgraph_state

        intermediate = lit_result.intermediate_outputs
        sources = []
        if isinstance(intermediate, dict):
            sources = intermediate.get("sources_found", [])

        if not sources or not self.gemini_service:
            langgraph_state["supervision_state"] = state
            return langgraph_state

        from src.agents.schemas.literature_review import SourceValidationResult

        sources_text = "\n".join(
            f'{i + 1}. "{s.get("title", "N/A")}" by '
            f"{', '.join(s.get('authors', ['Unknown'])[:3])} "
            f"({s.get('year', 'N/A')}) in {s.get('journal', 'N/A')}"
            for i, s in enumerate(sources[:10])
        )

        prompt = f"""You are an academic fact-checker. Verify whether each of these papers actually exists as described.

For each paper, check:
- Does a paper with this exact or very similar title exist?
- Are the listed authors correct?
- Is the publication year correct?
- Is the venue/journal correct?

Papers to verify:
{sources_text}

Be strict: if you are not confident a paper exists with these exact details, mark exists=false.
If a paper exists but with slightly different details, mark exists=true and provide corrections."""

        try:
            validation = await self.gemini_service.generate_structured_content(
                prompt, SourceValidationResult
            )

            verified_sources = []
            for i, source in enumerate(sources):
                if i < len(validation.verified_sources):
                    verified = validation.verified_sources[i]
                    if verified.exists and verified.confidence >= 0.7:
                        if verified.corrected_title:
                            source["title"] = verified.corrected_title
                        if verified.corrected_authors:
                            source["authors"] = verified.corrected_authors
                        if verified.corrected_year:
                            source["year"] = verified.corrected_year
                        source["verification_confidence"] = verified.confidence
                        verified_sources.append(source)
                    else:
                        logger.info(
                            "research_source_rejected",
                            title=source.get("title", "N/A"),
                            confidence=verified.confidence,
                            issues=verified.issues,
                        )
                else:
                    source["verification_confidence"] = 0.5
                    verified_sources.append(source)

            if isinstance(intermediate, dict):
                rejected_count = len(sources) - len(verified_sources)
                intermediate["sources_found"] = verified_sources
                intermediate["total_sources"] = len(verified_sources)
                intermediate["validation"] = {
                    "total_checked": len(sources),
                    "verified": len(verified_sources),
                    "rejected": rejected_count,
                    "notes": validation.validation_notes,
                }

                state.worker_results["literature_review"] = TalkHierContent(
                    content=lit_result.content
                    if hasattr(lit_result, "content")
                    else "",
                    background=lit_result.background
                    if hasattr(lit_result, "background")
                    else "",
                    intermediate_outputs=intermediate,
                    confidence_score=lit_result.confidence_score
                    if hasattr(lit_result, "confidence_score")
                    else 0.8,
                )

                logger.info(
                    "research_source_validation_completed",
                    verified_count=len(verified_sources),
                    source_count=len(sources),
                    rejected_count=rejected_count,
                )

        except Exception as e:
            logger.error("research_source_validation_failed", error=str(e))

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def graduate_review(
        self,
        langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Graduate-level critical review of the drafted paper."""
        from ..schemas.research_paper import PaperReview

        state = langgraph_state["supervision_state"]

        logger.info("research_quality_phase_started", phase="graduate_review")
        state.current_phase = "graduate_review"

        paper_data = state.worker_results.get("draft_paper")
        if not paper_data or not hasattr(paper_data, "intermediate_outputs"):
            logger.warning("research_review_missing_paper")
            langgraph_state["supervision_state"] = state
            return langgraph_state

        paper_dict = paper_data.intermediate_outputs
        if not isinstance(paper_dict, dict):
            logger.warning("research_review_invalid_paper_format")
            langgraph_state["supervision_state"] = state
            return langgraph_state

        paper_text = self._build_paper_text(paper_dict)
        prompt = self._build_review_prompt(paper_text)

        try:
            if self.gemini_service:
                review = await self.gemini_service.generate_structured_content(
                    prompt, PaperReview
                )

                state.worker_results["graduate_review"] = TalkHierContent(
                    content=f"Graduate review: Overall score {review.overall_score}/10",
                    background="Critical review by graduate committee standards",
                    intermediate_outputs=review.model_dump(),
                    confidence_score=0.90,
                )

                logger.info(
                    "research_graduate_review_completed",
                    overall_score=review.overall_score,
                    meets_graduate_standard=review.meets_graduate_standard,
                )
            else:
                logger.warning("research_review_gemini_unavailable")

        except Exception as e:
            logger.error("research_graduate_review_failed", error=str(e))

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def revise_paper(
        self,
        langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Revise the paper based on reviewer feedback."""
        state = langgraph_state["supervision_state"]

        logger.info("research_quality_phase_started", phase="revise_paper")
        state.current_phase = "revise_paper"

        paper_data = state.worker_results.get("draft_paper")
        review_data = state.worker_results.get("graduate_review")

        if not paper_data or not review_data:
            logger.warning("research_revision_missing_inputs")
            langgraph_state["supervision_state"] = state
            return langgraph_state

        paper_dict = paper_data.intermediate_outputs
        review_dict = review_data.intermediate_outputs

        if not isinstance(paper_dict, dict) or not isinstance(review_dict, dict):
            logger.warning("research_revision_invalid_data_format")
            langgraph_state["supervision_state"] = state
            return langgraph_state

        critical_issues = review_dict.get("critical_issues", [])
        section_reviews = review_dict.get("section_reviews", [])
        required_changes_text = self._build_required_changes_text(section_reviews)

        try:
            if self.gemini_service:
                revised_dict = await self._build_revised_paper(
                    paper_dict,
                    critical_issues,
                    section_reviews,
                    required_changes_text,
                )
                current_revision_count = paper_dict.get("revision_count", 0)

                state.worker_results["draft_paper"] = TalkHierContent(
                    content=(
                        f"Revised paper (round {current_revision_count + 1}): "
                        f"{revised_dict.get('title', '')}"
                    ),
                    background="Paper revision incorporating reviewer feedback",
                    intermediate_outputs=revised_dict,
                    confidence_score=0.90,
                )

                logger.info(
                    "research_paper_revised",
                    revision_round=current_revision_count + 1,
                )
            else:
                logger.warning("research_revision_gemini_unavailable")

        except Exception as e:
            logger.error("research_paper_revision_failed", error=str(e))

        langgraph_state["supervision_state"] = state
        return langgraph_state

    def should_revise_paper(self, langgraph_state: dict[str, Any]) -> str:
        """Determine if the paper needs revision based on review."""
        state = langgraph_state["supervision_state"]
        review_data = state.worker_results.get("graduate_review")

        if review_data and hasattr(review_data, "intermediate_outputs"):
            review = review_data.intermediate_outputs
            if isinstance(review, dict):
                score = review.get("overall_score", 0)
                revision_count = self._get_revision_count(state)

                if score >= 9.0:
                    logger.info("research_paper_accepted", score=score)
                    return "accept"
                if revision_count >= 5:
                    logger.warning(
                        "research_revision_max_reached",
                        score=score,
                        revision_count=revision_count,
                    )
                    return "accept"

                logger.info(
                    "research_paper_revision_needed",
                    score=score,
                    next_revision_round=revision_count + 1,
                )
                return "revise"

        logger.warning("research_review_missing_valid_data_accepting")
        return "accept"

    async def evaluate_consensus(
        self,
        langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Evaluate consensus across all workers."""
        state = langgraph_state["supervision_state"]

        logger.info("research_quality_phase_started", phase="consensus_evaluation")
        state.current_phase = "consensus_evaluation"

        if "draft_paper" in state.worker_results:
            paper_data = state.worker_results["draft_paper"]
            if hasattr(paper_data, "intermediate_outputs"):
                state.context["final_paper"] = paper_data.intermediate_outputs

        worker_messages = []
        for worker_type, result in state.worker_results.items():
            if result:
                worker_messages.append(
                    TalkHierMessage(
                        from_agent=worker_type,
                        to_agent=self.get_agent_type(),
                        message_type=MessageType.WORKER_REPORT,
                        content=result,
                        conversation_id=state.task_id,
                    )
                )

        if worker_messages:
            consensus_score = (
                await self.communication_protocol.consensus_builder.evaluate_consensus(
                    worker_messages
                )
            )

            state.consensus_score = consensus_score.overall_score
            state.quality_score = consensus_score.evidence_quality

            logger.info(
                "research_consensus_evaluated",
                consensus_score=consensus_score.overall_score,
                evidence_quality=consensus_score.evidence_quality,
            )
        else:
            logger.warning("research_consensus_missing_worker_results")
            state.consensus_score = 1.0
            state.quality_score = 0.0

        state.refinement_round += 1

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def get_research_quality_assessment(
        self,
        state: SupervisionState,
    ) -> dict[str, Any]:
        """Get comprehensive research quality assessment."""
        worker_contributions: dict[str, Any] = {}

        quality_assessment = {
            "overall_quality": state.quality_score,
            "consensus_score": state.consensus_score,
            "worker_contributions": worker_contributions,
            "research_completeness": 0.0,
            "methodological_rigor": 0.0,
            "evidence_strength": 0.0,
        }

        for worker_type, result in state.worker_results.items():
            if result and hasattr(result, "confidence_score"):
                worker_contributions[worker_type] = {
                    "confidence": result.confidence_score,
                    "contribution_quality": result.confidence_score,
                }
                quality_assessment["worker_contributions"] = worker_contributions

        expected_workers = ["literature_review", "methodology", "synthesis"]
        completed_workers = [w for w in expected_workers if w in state.worker_results]
        quality_assessment["research_completeness"] = len(completed_workers) / len(
            expected_workers
        )

        return quality_assessment

    def _build_paper_text(self, paper_dict: dict[str, Any]) -> str:
        return f"""
TITLE: {paper_dict.get('title', 'N/A')}

ABSTRACT:
{paper_dict.get('abstract', '')}

INTRODUCTION:
{paper_dict.get('introduction', '')}

LITERATURE REVIEW:
{paper_dict.get('literature_review', '')}

METHODOLOGY:
{paper_dict.get('methodology', '')}

FINDINGS:
{paper_dict.get('findings', '')}

DISCUSSION:
{paper_dict.get('discussion', '')}

CONCLUSION:
{paper_dict.get('conclusion', '')}

REFERENCES:
{chr(10).join(paper_dict.get('references', []))}
"""

    def _build_review_prompt(self, paper_text: str) -> str:
        return f"""You are a graduate thesis committee member conducting a critical review of an academic paper.

{paper_text}

Evaluate this paper on:
1. Academic rigor and depth of analysis
2. Quality of literature review (critical engagement, not just summary)
3. Methodology appropriateness and justification
4. Strength of findings and evidence
5. Quality of discussion and interpretation
6. Writing quality (academic prose, flow, coherence)
7. Proper citation and referencing

For each section, provide:
- Score (1-10)
- Strengths
- Weaknesses
- Required changes (things that MUST be fixed)

Also provide:
- Overall score (1-10, where 9+ means publication-ready graduate quality)
- Whether the paper meets graduate-level standards (threshold: 9.0)
- Critical issues that must be addressed before acceptance

Be rigorous. A score of 9+ means the paper could be submitted to a graduate seminar or academic workshop. Demand strong argumentation, proper evidence, critical analysis (not just summarization), and polished academic prose.
"""

    def _build_required_changes_text(
        self,
        section_reviews: list[dict[str, Any]],
    ) -> str:
        return "\n\n".join(
            f"SECTION: {sr.get('section', 'N/A')}\nRequired changes:\n"
            + "\n".join(f"- {change}" for change in sr.get("required_changes", []))
            for sr in section_reviews
        )

    async def _build_revised_paper(
        self,
        paper_dict: dict[str, Any],
        critical_issues: list[str],
        section_reviews: list[dict[str, Any]],
        required_changes_text: str,
    ) -> dict[str, Any]:
        current_revision_count = paper_dict.get("revision_count", 0)
        revised_dict: dict[str, Any] = {
            "revision_count": current_revision_count + 1,
            "references": paper_dict.get("references", []),
        }

        feedback_context = f"""CRITICAL ISSUES:
{chr(10).join(f'- {issue}' for issue in critical_issues)}

REQUIRED CHANGES:
{required_changes_text}"""
        assert self.gemini_service is not None

        sections = [
            "title",
            "abstract",
            "introduction",
            "literature_review",
            "methodology",
            "findings",
            "discussion",
            "conclusion",
        ]
        for section in sections:
            original = paper_dict.get(section, "")
            section_feedback = self._build_section_feedback(section, section_reviews)
            revision_prompt = f"""Revise the following {section.replace('_', ' ')} section of an academic paper.
Target quality: 9/10 (publication-ready graduate level).

ORIGINAL {section.upper().replace('_', ' ')}:
{original}

REVIEWER FEEDBACK FOR THIS SECTION:{section_feedback}

{feedback_context}

Revise this section addressing all feedback. Strengthen argumentation, add evidence and citations, ensure critical analysis. Write in polished academic prose. Return ONLY the revised section text."""

            try:
                revised_text = await self.gemini_service.generate_content(
                    revision_prompt
                )
                revised_dict[section] = revised_text.strip()
            except Exception as e:
                logger.warning(
                    "research_section_revision_failed",
                    section=section,
                    error=str(e),
                )
                revised_dict[section] = original

        return revised_dict

    def _build_section_feedback(
        self,
        section: str,
        section_reviews: list[dict[str, Any]],
    ) -> str:
        for section_review in section_reviews:
            if (
                isinstance(section_review, dict)
                and section_review.get("section", "").lower().replace(" ", "_")
                == section
            ):
                weaknesses = section_review.get("weaknesses", [])
                changes = section_review.get("required_changes", [])
                if weaknesses or changes:
                    return (
                        f"\nWeaknesses: {'; '.join(weaknesses)}"
                        f"\nRequired changes: {'; '.join(changes)}"
                    )

        return ""

    def _get_revision_count(self, state: SupervisionState) -> int:
        paper_data = state.worker_results.get("draft_paper")
        if (
            paper_data
            and hasattr(paper_data, "intermediate_outputs")
            and isinstance(paper_data.intermediate_outputs, dict)
        ):
            return int(paper_data.intermediate_outputs.get("revision_count", 0))

        return 0
