"""
Research Supervisor Agent

Coordinates research teams implementing the proven research workflow from the
original Cerebro research platform, enhanced with TalkHier protocol and
LangGraph orchestration.

Coordinates:
- Literature Review Agent: Academic source analysis
- Methodology Agent: Research design and validation
- Comparative Analysis Agent: Multi-perspective comparison
- Synthesis Agent: Integration and narrative building
- Citation Agent: Source verification and formatting
"""

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from ..citation_agent import CitationAgent
from ..communication.talkhier_message import (
    MessageType,
    TalkHierContent,
    TalkHierMessage,
)
from ..comparative_analysis_agent import ComparativeAnalysisAgent
from ..literature_review_agent import LiteratureReviewAgent
from ..methodology_agent import MethodologyAgent
from ..models import AgentTask
from ..synthesis_agent import SynthesisAgent
from .base_supervisor import (
    BaseSupervisor,
    SupervisionState,
    WorkerDefinition,
)

logger = logging.getLogger(__name__)


class ResearchSupervisor(BaseSupervisor):
    """
    Research team supervisor implementing proven research workflows.

    Manages the complete research lifecycle:
    1. Literature analysis and source gathering
    2. Methodology design and validation
    3. Comparative analysis across perspectives
    4. Synthesis and narrative building
    5. Citation verification and formatting

    Uses LangGraph for state management and TalkHier for quality assurance.
    """

    def __init__(
        self,
        gemini_service: Any | None = None,
        cache_client: Any | None = None,
        config: dict[str, Any] | None = None,
    ):
        """Initialize research supervisor."""
        super().__init__(
            supervisor_type="research",
            domain="research",
            gemini_service=gemini_service,
            cache_client=cache_client,
            config=config,
        )

        # Research-specific configuration
        self.research_depth = (
            config.get("research_depth", "comprehensive") if config else "comprehensive"
        )
        self.max_sources = config.get("max_sources", 50) if config else 50
        self.citation_style = config.get("citation_style", "APA") if config else "APA"

    def _register_worker_types(self) -> None:
        """Register research worker types."""

        # Literature Review Worker
        self.worker_definitions["literature_review"] = WorkerDefinition(
            worker_type="literature_review",
            agent_class=LiteratureReviewAgent,
            specialization="Academic source analysis and systematic reviews",
            capabilities=["database_search", "source_evaluation", "gap_analysis"],
            required_for=["research", "literature_analysis"],
            optimal_for=["academic_research", "systematic_review"],
            avg_execution_time_ms=45000,
            reliability_score=0.95,
            quality_score=0.90,
        )

        # Methodology Worker
        self.worker_definitions["methodology"] = WorkerDefinition(
            worker_type="methodology",
            agent_class=MethodologyAgent,
            specialization="Research design and methodological validation",
            capabilities=["research_design", "validity_assessment", "bias_detection"],
            required_for=["research", "methodology_design"],
            optimal_for=["experimental_design", "validation_studies"],
            avg_execution_time_ms=30000,
            reliability_score=0.92,
            quality_score=0.88,
        )

        # Comparative Analysis Worker
        self.worker_definitions["comparative_analysis"] = WorkerDefinition(
            worker_type="comparative_analysis",
            agent_class=ComparativeAnalysisAgent,
            specialization="Multi-perspective analysis and comparison",
            capabilities=[
                "framework_comparison",
                "strength_weakness_analysis",
                "evidence_synthesis",
            ],
            required_for=["comparative_studies", "multi_perspective_analysis"],
            optimal_for=["theory_comparison", "approach_evaluation"],
            avg_execution_time_ms=35000,
            reliability_score=0.90,
            quality_score=0.87,
        )

        # Synthesis Worker
        self.worker_definitions["synthesis"] = WorkerDefinition(
            worker_type="synthesis",
            agent_class=SynthesisAgent,
            specialization="Cross-agent integration and narrative synthesis",
            capabilities=[
                "integration",
                "narrative_building",
                "pattern_identification",
            ],
            required_for=["research", "synthesis"],
            optimal_for=["comprehensive_analysis", "meta_analysis"],
            avg_execution_time_ms=40000,
            reliability_score=0.93,
            quality_score=0.91,
        )

        # Citation Worker
        self.worker_definitions["citation"] = WorkerDefinition(
            worker_type="citation",
            agent_class=CitationAgent,
            specialization="Source verification and citation formatting",
            capabilities=[
                "citation_formatting",
                "source_verification",
                "plagiarism_check",
            ],
            required_for=["research", "academic_writing"],
            optimal_for=["publication_prep", "academic_validation"],
            avg_execution_time_ms=20000,
            reliability_score=0.98,
            quality_score=0.85,
        )

    def _build_workflow_graph(self) -> None:
        """Build LangGraph workflow for research supervision."""

        # Create workflow graph
        self.workflow_graph = StateGraph(dict)

        # Add nodes for each phase
        self.workflow_graph.add_node(
            "plan_research",
            self._create_langgraph_node("plan_research", self._plan_research_phase),
        )

        self.workflow_graph.add_node(
            "coordinate_literature",
            self._create_langgraph_node(
                "coordinate_literature", self._coordinate_literature_phase
            ),
        )

        self.workflow_graph.add_node(
            "coordinate_methodology",
            self._create_langgraph_node(
                "coordinate_methodology", self._coordinate_methodology_phase
            ),
        )

        self.workflow_graph.add_node(
            "coordinate_analysis",
            self._create_langgraph_node(
                "coordinate_analysis", self._coordinate_analysis_phase
            ),
        )

        self.workflow_graph.add_node(
            "coordinate_synthesis",
            self._create_langgraph_node(
                "coordinate_synthesis", self._coordinate_synthesis_phase
            ),
        )

        self.workflow_graph.add_node(
            "validate_sources",
            self._create_langgraph_node("validate_sources", self._validate_sources_phase),
        )

        self.workflow_graph.add_node(
            "coordinate_citation",
            self._create_langgraph_node(
                "coordinate_citation", self._coordinate_citation_phase
            ),
        )

        self.workflow_graph.add_node(
            "draft_paper",
            self._create_langgraph_node("draft_paper", self._draft_paper_phase),
        )

        self.workflow_graph.add_node(
            "graduate_review",
            self._create_langgraph_node("graduate_review", self._graduate_review_phase),
        )

        self.workflow_graph.add_node(
            "revise_paper",
            self._create_langgraph_node("revise_paper", self._revise_paper_phase),
        )

        self.workflow_graph.add_node(
            "evaluate_consensus",
            self._create_langgraph_node(
                "evaluate_consensus", self._evaluate_consensus_phase
            ),
        )

        # Add edges for research workflow
        self.workflow_graph.set_entry_point("plan_research")
        self.workflow_graph.add_edge("plan_research", "coordinate_literature")
        self.workflow_graph.add_edge("coordinate_literature", "validate_sources")
        self.workflow_graph.add_edge("validate_sources", "coordinate_methodology")
        self.workflow_graph.add_edge("coordinate_methodology", "coordinate_analysis")
        self.workflow_graph.add_edge("coordinate_analysis", "coordinate_synthesis")
        self.workflow_graph.add_edge("coordinate_synthesis", "coordinate_citation")
        self.workflow_graph.add_edge("coordinate_citation", "draft_paper")
        self.workflow_graph.add_edge("draft_paper", "graduate_review")

        # Conditional edge: review passes → consensus, review fails → revise
        self.workflow_graph.add_conditional_edges(
            "graduate_review",
            self._should_revise_paper,
            {
                "revise": "revise_paper",
                "accept": "evaluate_consensus",
            },
        )
        self.workflow_graph.add_edge("revise_paper", "graduate_review")  # Loop back to review

        # Conditional edge for refinement
        self.workflow_graph.add_conditional_edges(
            "evaluate_consensus",
            self._should_continue_refinement,
            {
                "continue": "coordinate_literature",  # Go back for another round
                "complete": END,
            },
        )

        # Compile the graph
        self.workflow_graph = self.workflow_graph.compile()  # type: Any

    async def _coordinate_workers(
        self, state: SupervisionState, task: AgentTask
    ) -> SupervisionState:
        """Research-specific worker coordination."""

        # Allocate workers based on research requirements
        allocated_workers = await self.allocate_workers(task)
        state.allocated_workers = allocated_workers

        # Set research-specific context
        state.context.update(
            {
                "research_depth": self.research_depth,
                "max_sources": self.max_sources,
                "citation_style": self.citation_style,
            }
        )

        return state

    async def _plan_research_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Plan research execution and worker allocation."""

        state = langgraph_state["supervision_state"]
        task = langgraph_state["original_task"]

        logger.info("Research planning phase")

        # Plan research approach
        state.current_phase = "planning"
        state = await self._coordinate_workers(state, task)

        # Initialize worker tasks
        research_query = state.original_query

        state.worker_tasks = {
            "literature_review": {
                "query": research_query,
                "domains": task.input_data.get("domains", []),
                "max_sources": self.max_sources,
            },
            "methodology": {
                "research_question": research_query,
                "type": task.input_data.get("research_type", "mixed"),
            },
            "comparative_analysis": {
                "query": research_query,
                "comparison_focus": "approaches_and_findings",
            },
            "synthesis": {"synthesis_focus": "comprehensive_integration"},
            "citation": {"style": self.citation_style},
        }

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _coordinate_literature_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Coordinate literature review worker."""

        state = langgraph_state["supervision_state"]

        logger.info("Literature review phase")
        state.current_phase = "literature_review"

        # Send task to literature worker if allocated
        if "literature_review" in state.allocated_workers:
            literature_task = state.worker_tasks["literature_review"]

            response = await self.send_talkhier_message(
                "literature_review",
                MessageType.SUPERVISOR_ASSIGNMENT,
                TalkHierContent(
                    content="Conduct systematic literature review",
                    background=f"Research supervision for: {state.original_query}",
                    intermediate_outputs=literature_task,
                ),
            )

            if response:
                state.worker_results["literature_review"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _validate_sources_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Validate literature sources to catch hallucinated papers."""
        state = langgraph_state["supervision_state"]
        logger.info("Source validation phase")
        state.current_phase = "source_validation"

        # Get literature results
        lit_result = state.worker_results.get("literature_review")
        if not lit_result or not hasattr(lit_result, "intermediate_outputs"):
            logger.warning("No literature results to validate")
            langgraph_state["supervision_state"] = state
            return langgraph_state

        intermediate = lit_result.intermediate_outputs
        sources = []
        if isinstance(intermediate, dict):
            sources = intermediate.get("sources_found", [])

        if not sources or not self.gemini_service:
            langgraph_state["supervision_state"] = state
            return langgraph_state

        # Build verification prompt
        from src.agents.schemas.literature_review import SourceValidationResult

        sources_text = "\n".join(
            f"{i+1}. \"{s.get('title', 'N/A')}\" by {', '.join(s.get('authors', ['Unknown'])[:3])} ({s.get('year', 'N/A')}) in {s.get('journal', 'N/A')}"
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

            # Filter sources based on validation
            verified_sources = []
            for i, source in enumerate(sources):
                if i < len(validation.verified_sources):
                    v = validation.verified_sources[i]
                    if v.exists and v.confidence >= 0.7:
                        # Apply corrections if any
                        if v.corrected_title:
                            source["title"] = v.corrected_title
                        if v.corrected_authors:
                            source["authors"] = v.corrected_authors
                        if v.corrected_year:
                            source["year"] = v.corrected_year
                        source["verification_confidence"] = v.confidence
                        verified_sources.append(source)
                    else:
                        logger.info(f"Rejected source: {source.get('title', 'N/A')} (confidence={v.confidence}, issues={v.issues})")
                else:
                    # Source wasn't verified (beyond validation list), keep it with lower confidence
                    source["verification_confidence"] = 0.5
                    verified_sources.append(source)

            # Update the literature results with verified sources
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

                # Rebuild the TalkHierContent with updated data
                from src.agents.communication.talkhier_message import TalkHierContent
                state.worker_results["literature_review"] = TalkHierContent(
                    content=lit_result.content if hasattr(lit_result, "content") else "",
                    background=lit_result.background if hasattr(lit_result, "background") else "",
                    intermediate_outputs=intermediate,
                    confidence_score=lit_result.confidence_score if hasattr(lit_result, "confidence_score") else 0.8,
                )

                logger.info(f"Source validation: {len(verified_sources)}/{len(sources)} sources verified, {rejected_count} rejected")

        except Exception as e:
            logger.error(f"Source validation failed: {e}")
            # Don't modify sources on validation failure — keep originals

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _coordinate_methodology_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Coordinate methodology worker."""

        state = langgraph_state["supervision_state"]

        logger.info("Methodology design phase")
        state.current_phase = "methodology"

        if "methodology" in state.allocated_workers:
            methodology_task = state.worker_tasks["methodology"]

            # Include literature results if available
            if "literature_review" in state.worker_results:
                methodology_task["literature_context"] = state.worker_results[
                    "literature_review"
                ]

            response = await self.send_talkhier_message(
                "methodology",
                MessageType.SUPERVISOR_ASSIGNMENT,
                TalkHierContent(
                    content="Design appropriate research methodology",
                    background=f"Methodology design for: {state.original_query}",
                    intermediate_outputs=methodology_task,
                ),
            )

            if response:
                state.worker_results["methodology"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _coordinate_analysis_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Coordinate comparative analysis worker."""

        state = langgraph_state["supervision_state"]

        logger.info("Comparative analysis phase")
        state.current_phase = "comparative_analysis"

        if "comparative_analysis" in state.allocated_workers:
            analysis_task = state.worker_tasks["comparative_analysis"]

            # Include previous results
            analysis_task["literature_findings"] = state.worker_results.get(
                "literature_review"
            )
            analysis_task["methodology_framework"] = state.worker_results.get(
                "methodology"
            )

            response = await self.send_talkhier_message(
                "comparative_analysis",
                MessageType.SUPERVISOR_ASSIGNMENT,
                TalkHierContent(
                    content="Conduct comparative analysis of approaches",
                    background=f"Analysis coordination for: {state.original_query}",
                    intermediate_outputs=analysis_task,
                ),
            )

            if response:
                state.worker_results["comparative_analysis"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _coordinate_synthesis_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Coordinate synthesis worker."""

        state = langgraph_state["supervision_state"]

        logger.info("Synthesis phase")
        state.current_phase = "synthesis"

        if "synthesis" in state.allocated_workers:
            # Prepare all agent outputs for synthesis
            agent_outputs = {}
            for worker_type, result in state.worker_results.items():
                if result and hasattr(result, "intermediate_outputs"):
                    agent_outputs[worker_type] = (
                        result.intermediate_outputs
                        if isinstance(result.intermediate_outputs, dict)
                        else {"findings": result.content}
                    )

            logger.info(
                f"Synthesis receiving outputs from {len(agent_outputs)} agents: "
                f"{list(agent_outputs.keys())}"
            )

            response = await self.send_talkhier_message(
                "synthesis",
                MessageType.SUPERVISOR_ASSIGNMENT,
                TalkHierContent(
                    content="Synthesize all research findings",
                    background=f"Synthesis coordination for: {state.original_query}",
                    intermediate_outputs={
                        "agent_outputs": agent_outputs,
                        "synthesis_focus": "comprehensive",
                    },
                ),
            )

            if response:
                state.worker_results["synthesis"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _coordinate_citation_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Coordinate citation worker."""

        state = langgraph_state["supervision_state"]

        logger.info("Citation formatting phase")
        state.current_phase = "citation"

        if "citation" in state.allocated_workers:
            # Extract sources from literature review results
            sources = []
            if "literature_review" in state.worker_results:
                lit_result = state.worker_results["literature_review"]
                if hasattr(lit_result, "intermediate_outputs") and isinstance(
                    lit_result.intermediate_outputs, dict
                ):
                    sources = (
                        lit_result.intermediate_outputs.get("sources_found")
                        or lit_result.intermediate_outputs.get("sources")
                        or []
                    )

            response = await self.send_talkhier_message(
                "citation",
                MessageType.SUPERVISOR_ASSIGNMENT,
                TalkHierContent(
                    content="Format citations and verify sources",
                    background=f"Citation coordination for: {state.original_query}",
                    intermediate_outputs={
                        "sources": sources,
                        "style": self.citation_style,
                    },
                ),
            )

            if response:
                state.worker_results["citation"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _draft_paper_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Draft a graduate-level research paper from all worker results."""
        from ..schemas.research_paper import ResearchPaper

        state = langgraph_state["supervision_state"]

        logger.info("Paper drafting phase")
        state.current_phase = "draft_paper"

        # Collect all worker outputs
        literature_findings = ""
        methodology_design = ""
        synthesis_findings = ""
        citations = []

        # Extract literature review findings
        if "literature_review" in state.worker_results:
            lit_result = state.worker_results["literature_review"]
            if hasattr(lit_result, "intermediate_outputs"):
                intermediate = lit_result.intermediate_outputs
                if isinstance(intermediate, dict):
                    sources = intermediate.get("sources_found", [])
                    for source in sources:
                        author_str = ", ".join(source.get("authors", ["Unknown"])[:2])
                        year = source.get("year", "N/A")
                        citations.append(f"{author_str}, {year}")
                literature_findings = lit_result.content if hasattr(lit_result, "content") else ""

        # Extract methodology design
        if "methodology" in state.worker_results:
            meth_result = state.worker_results["methodology"]
            methodology_design = meth_result.content if hasattr(meth_result, "content") else ""

        # Extract synthesis findings
        if "synthesis" in state.worker_results:
            synth_result = state.worker_results["synthesis"]
            synthesis_findings = synth_result.content if hasattr(synth_result, "content") else ""

        # Build drafting prompt
        prompt = f"""You are an academic researcher drafting a graduate-level research paper.

Based on the following research components:

LITERATURE REVIEW FINDINGS:
{literature_findings}

METHODOLOGY DESIGN:
{methodology_design}

SYNTHESIS OF FINDINGS:
{synthesis_findings}

CITATIONS:
{", ".join(citations[:20])}

Draft a complete research paper with these sections:
- Title (concise and descriptive)
- Abstract (150-300 words summarizing research question, methods, findings, implications)
- Introduction (establish context, significance, research questions)
- Literature Review (critical analysis, not just a list — synthesize themes, identify debates, show gaps)
- Methodology (justify choices, explain design)
- Findings (present evidence with analysis)
- Discussion (interpret findings, implications, limitations, compare with existing literature)
- Conclusion (contributions, future research)
- References (properly formatted citations)

IMPORTANT FORMATTING RULES:
- Write in academic prose, NOT bullet points
- Use formal academic language and third person
- Each section should flow as connected paragraphs
- Cite sources inline using (Author, Year) format
- The literature review should critically engage with the sources, not just list them
- The discussion should connect findings back to the research questions

Research Query: {state.original_query}
"""

        # Generate paper using Gemini
        try:
            if self.gemini_service:
                paper = await self.gemini_service.generate_structured_content(
                    prompt, ResearchPaper
                )

                # Store paper in worker_results
                state.worker_results["draft_paper"] = TalkHierContent(
                    content=f"Graduate-level research paper: {paper.title}",
                    background="Paper drafting from integrated research findings",
                    intermediate_outputs=paper.model_dump(),
                    confidence_score=0.85,
                )

                logger.info(f"Drafted paper: {paper.title}")
            else:
                logger.warning("No Gemini service available for paper drafting")

        except Exception as e:
            logger.error(f"Paper drafting failed: {e}")
            # Create minimal paper structure on failure
            state.worker_results["draft_paper"] = TalkHierContent(
                content="Paper drafting incomplete",
                background="Fallback due to drafting error",
                intermediate_outputs={
                    "title": state.original_query,
                    "abstract": "Error during paper generation",
                    "error": str(e),
                },
                confidence_score=0.3,
            )

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _graduate_review_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Graduate-level critical review of the drafted paper."""
        from ..schemas.research_paper import PaperReview

        state = langgraph_state["supervision_state"]

        logger.info("Graduate review phase")
        state.current_phase = "graduate_review"

        # Get drafted paper
        paper_data = state.worker_results.get("draft_paper")
        if not paper_data or not hasattr(paper_data, "intermediate_outputs"):
            logger.warning("No paper to review")
            langgraph_state["supervision_state"] = state
            return langgraph_state

        paper_dict = paper_data.intermediate_outputs
        if not isinstance(paper_dict, dict):
            logger.warning("Invalid paper format for review")
            langgraph_state["supervision_state"] = state
            return langgraph_state

        # Build full paper text for review
        paper_text = f"""
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

        # Build review prompt
        prompt = f"""You are a graduate thesis committee member conducting a critical review of an academic paper.

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
- Overall score (1-10)
- Whether the paper meets graduate-level standards (threshold: 7.0)
- Critical issues that must be addressed before acceptance
"""

        # Generate review using Gemini
        try:
            if self.gemini_service:
                review = await self.gemini_service.generate_structured_content(
                    prompt, PaperReview
                )

                # Store review in worker_results
                state.worker_results["graduate_review"] = TalkHierContent(
                    content=f"Graduate review: Overall score {review.overall_score}/10",
                    background="Critical review by graduate committee standards",
                    intermediate_outputs=review.model_dump(),
                    confidence_score=0.90,
                )

                logger.info(
                    f"Review complete: score={review.overall_score}, "
                    f"meets_standard={review.meets_graduate_standard}"
                )
            else:
                logger.warning("No Gemini service available for review")

        except Exception as e:
            logger.error(f"Graduate review failed: {e}")

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _revise_paper_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Revise the paper based on reviewer feedback."""
        from ..schemas.research_paper import ResearchPaper

        state = langgraph_state["supervision_state"]

        logger.info("Paper revision phase")
        state.current_phase = "revise_paper"

        # Get paper and review
        paper_data = state.worker_results.get("draft_paper")
        review_data = state.worker_results.get("graduate_review")

        if not paper_data or not review_data:
            logger.warning("Missing paper or review for revision")
            langgraph_state["supervision_state"] = state
            return langgraph_state

        paper_dict = paper_data.intermediate_outputs
        review_dict = review_data.intermediate_outputs

        if not isinstance(paper_dict, dict) or not isinstance(review_dict, dict):
            logger.warning("Invalid data format for revision")
            langgraph_state["supervision_state"] = state
            return langgraph_state

        # Build revision prompt
        critical_issues = review_dict.get("critical_issues", [])
        section_reviews = review_dict.get("section_reviews", [])

        required_changes_text = "\n\n".join(
            f"SECTION: {sr.get('section', 'N/A')}\nRequired changes:\n"
            + "\n".join(f"- {change}" for change in sr.get("required_changes", []))
            for sr in section_reviews
        )

        prompt = f"""You are an academic researcher revising a paper based on committee feedback.

ORIGINAL PAPER SECTIONS:
Title: {paper_dict.get('title', '')}
Abstract: {paper_dict.get('abstract', '')}
Introduction: {paper_dict.get('introduction', '')}
Literature Review: {paper_dict.get('literature_review', '')}
Methodology: {paper_dict.get('methodology', '')}
Findings: {paper_dict.get('findings', '')}
Discussion: {paper_dict.get('discussion', '')}
Conclusion: {paper_dict.get('conclusion', '')}

CRITICAL ISSUES:
{chr(10).join(f"- {issue}" for issue in critical_issues)}

REQUIRED CHANGES BY SECTION:
{required_changes_text}

Revise the paper addressing ALL reviewer feedback. Maintain academic prose style.
Focus especially on the critical issues and required changes.
"""

        # Generate revised paper using Gemini
        try:
            if self.gemini_service:
                revised_paper = await self.gemini_service.generate_structured_content(
                    prompt, ResearchPaper
                )

                # Increment revision count
                current_revision_count = paper_dict.get("revision_count", 0)
                revised_dict = revised_paper.model_dump()
                revised_dict["revision_count"] = current_revision_count + 1

                # Store revised paper
                state.worker_results["draft_paper"] = TalkHierContent(
                    content=f"Revised paper (round {current_revision_count + 1}): {revised_paper.title}",
                    background="Paper revision incorporating reviewer feedback",
                    intermediate_outputs=revised_dict,
                    confidence_score=0.90,
                )

                logger.info(
                    f"Paper revised (round {current_revision_count + 1}): {revised_paper.title}"
                )
            else:
                logger.warning("No Gemini service available for revision")

        except Exception as e:
            logger.error(f"Paper revision failed: {e}")

        langgraph_state["supervision_state"] = state
        return langgraph_state

    def _should_revise_paper(self, langgraph_state: dict[str, Any]) -> str:
        """Determine if the paper needs revision based on review."""
        state = langgraph_state["supervision_state"]
        review_data = state.worker_results.get("graduate_review")

        # Extract review from intermediate_outputs
        if review_data and hasattr(review_data, "intermediate_outputs"):
            review = review_data.intermediate_outputs
            if isinstance(review, dict):
                score = review.get("overall_score", 0)
                meets_standard = review.get("meets_graduate_standard", False)

                # Get current revision count
                paper_data = state.worker_results.get("draft_paper")
                revision_count = 0
                if (
                    paper_data
                    and hasattr(paper_data, "intermediate_outputs")
                    and isinstance(paper_data.intermediate_outputs, dict)
                ):
                    revision_count = paper_data.intermediate_outputs.get("revision_count", 0)

                if meets_standard or score >= 7.0:
                    logger.info(f"Paper accepted: score={score}, meets_standard={meets_standard}")
                    return "accept"
                if revision_count >= 2:  # Max 2 revision rounds
                    logger.warning(
                        f"Max revisions reached (score={score}, count={revision_count}), accepting"
                    )
                    return "accept"

                logger.info(
                    f"Paper needs revision: score={score}, round={revision_count + 1}"
                )
                return "revise"

        logger.warning("No valid review data, accepting paper")
        return "accept"

    async def _evaluate_consensus_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Evaluate consensus across all workers."""

        state = langgraph_state["supervision_state"]

        logger.info("Consensus evaluation phase")
        state.current_phase = "consensus_evaluation"

        # Store final paper in state if available
        if "draft_paper" in state.worker_results:
            paper_data = state.worker_results["draft_paper"]
            if hasattr(paper_data, "intermediate_outputs"):
                state.context["final_paper"] = paper_data.intermediate_outputs

        # Create messages from worker results for consensus evaluation
        worker_messages = []

        for worker_type, result in state.worker_results.items():
            if result:
                message = TalkHierMessage(
                    from_agent=worker_type,
                    to_agent=self.get_agent_type(),
                    message_type=MessageType.WORKER_REPORT,
                    content=result,
                    conversation_id=state.task_id,
                )
                worker_messages.append(message)

        if worker_messages:
            # Evaluate consensus using TalkHier protocol
            consensus_score = (
                await self.communication_protocol.consensus_builder.evaluate_consensus(
                    worker_messages
                )
            )

            state.consensus_score = consensus_score.overall_score
            state.quality_score = consensus_score.evidence_quality

            logger.info(f"Research consensus: {consensus_score.overall_score:.3f}")
        else:
            # No worker messages — set a default score to avoid infinite loop
            logger.warning("No worker results for consensus — defaulting to 1.0")
            state.consensus_score = 1.0
            state.quality_score = 0.0

        # Increment refinement round to prevent infinite loops
        state.refinement_round += 1

        langgraph_state["supervision_state"] = state
        return langgraph_state

    def _should_continue_refinement(self, langgraph_state: dict[str, Any]) -> str:
        """Determine if another refinement round is needed."""

        state = langgraph_state["supervision_state"]

        # Continue if consensus below threshold and rounds available
        if (
            state.consensus_score < self.quality_threshold
            and state.refinement_round < 1
        ):
            logger.info(
                f"Consensus {state.consensus_score:.3f} below threshold, continuing refinement"
            )
            return "continue"
        else:
            logger.info("Research workflow complete")
            return "complete"

    async def get_research_quality_assessment(
        self, state: SupervisionState
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

        # Assess each worker contribution
        for worker_type, result in state.worker_results.items():
            if result and hasattr(result, "confidence_score"):
                worker_contributions[worker_type] = {
                    "confidence": result.confidence_score,
                    "contribution_quality": result.confidence_score,
                }
                quality_assessment["worker_contributions"] = worker_contributions

        # Calculate research completeness
        expected_workers = ["literature_review", "methodology", "synthesis"]
        completed_workers = [w for w in expected_workers if w in state.worker_results]
        quality_assessment["research_completeness"] = len(completed_workers) / len(
            expected_workers
        )

        return quality_assessment


__all__ = ["ResearchSupervisor"]
