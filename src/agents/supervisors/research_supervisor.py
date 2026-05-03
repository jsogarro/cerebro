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

from typing import Any

from langgraph.graph import END, StateGraph
from structlog import get_logger

from ..communication.talkhier_message import TalkHierContent
from ..models import AgentTask
from .base_supervisor import (
    BaseSupervisor,
    SupervisionState,
)
from .research_agent_selector import ResearchAgentSelector
from .research_execution_coordinator import ResearchExecutionCoordinator
from .research_quality_validator import ResearchQualityValidator
from .research_query_planner import ResearchQueryPlanner

logger = get_logger()


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
        self.agent_selector = ResearchAgentSelector()
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
        self.query_planner = ResearchQueryPlanner(
            self.research_depth,
            self.max_sources,
            self.citation_style,
        )
        self.execution_coordinator = ResearchExecutionCoordinator(
            self.send_talkhier_message,
            self.citation_style,
        )
        self.quality_validator = ResearchQualityValidator(
            self.gemini_service,
            self.communication_protocol,
            self.get_agent_type,
            self.quality_threshold,
        )

    def _register_worker_types(self) -> None:
        """Register research worker types."""
        self.worker_definitions.update(
            self.agent_selector.build_worker_definitions()
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
        state.context.update(self.query_planner.build_research_context())

        return state

    async def _plan_research_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Plan research execution and worker allocation."""

        state = langgraph_state["supervision_state"]
        task = langgraph_state["original_task"]

        logger.info("research_supervisor_phase_started", phase="planning")

        # Plan research approach
        state.current_phase = "planning"
        state = await self._coordinate_workers(state, task)

        # Initialize worker tasks
        research_query = state.original_query

        state.worker_tasks = self.query_planner.build_worker_tasks(
            research_query,
            task,
        )

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _coordinate_literature_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Coordinate literature review worker."""
        return await self.execution_coordinator.coordinate_literature(langgraph_state)

    async def _validate_sources_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Validate literature sources to catch hallucinated papers."""
        return await self.quality_validator.validate_sources(langgraph_state)

    async def _coordinate_methodology_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Coordinate methodology worker."""
        return await self.execution_coordinator.coordinate_methodology(langgraph_state)

    async def _coordinate_analysis_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Coordinate comparative analysis worker."""
        return await self.execution_coordinator.coordinate_analysis(langgraph_state)

    async def _coordinate_synthesis_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Coordinate synthesis worker."""
        return await self.execution_coordinator.coordinate_synthesis(langgraph_state)

    async def _coordinate_citation_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Coordinate citation worker."""
        return await self.execution_coordinator.coordinate_citation(langgraph_state)

    async def _draft_paper_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Draft a graduate-level research paper from all worker results."""

        state = langgraph_state["supervision_state"]

        logger.info("research_supervisor_phase_started", phase="draft_paper")
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

        # Generate paper section-by-section to avoid token limit truncation
        try:
            if self.gemini_service:
                paper_dict = await self._generate_paper_sections(
                    state.original_query,
                    literature_findings,
                    methodology_design,
                    synthesis_findings,
                    citations,
                )

                state.worker_results["draft_paper"] = TalkHierContent(
                    content=f"Graduate-level research paper: {paper_dict.get('title', '')}",
                    background="Paper drafted section-by-section from research findings",
                    intermediate_outputs=paper_dict,
                    confidence_score=0.85,
                )

                logger.info(
                    "research_paper_drafted",
                    title=paper_dict.get("title", "Untitled"),
                )
            else:
                logger.warning("research_paper_drafting_gemini_unavailable")

        except Exception as e:
            logger.error("research_paper_drafting_failed", error=str(e))
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

    async def _generate_paper_sections(
        self,
        query: str,
        literature_findings: str,
        methodology_design: str,
        synthesis_findings: str,
        citations: list[str],
    ) -> dict[str, Any]:
        """Generate paper section-by-section to avoid output token truncation."""
        context = f"""Research Question: {query}

LITERATURE FINDINGS:
{literature_findings[:3000]}

METHODOLOGY:
{methodology_design[:2000]}

SYNTHESIS:
{synthesis_findings[:2000]}

REFERENCES:
{chr(10).join(citations[:15])}"""

        sections: dict[str, Any] = {"revision_count": 0, "references": citations[:15]}

        section_prompts = [
            ("title", "Write a concise, descriptive academic title for a research paper on this topic. Return ONLY the title text, nothing else."),
            ("abstract", "Write a 200-300 word academic abstract. Include: research question, methodology, key findings, and implications. Write in formal academic prose. Return ONLY the abstract text."),
            ("introduction", "Write a 3-4 paragraph introduction. Establish the significance of the research, provide context, state the research questions, and outline the paper structure. Use formal academic prose with (Author, Year) citations. Return ONLY the introduction text."),
            ("literature_review", "Write a critical literature review of 5-7 paragraphs. Do NOT just list studies — synthesize themes, identify debates between researchers, show how studies relate to each other, and identify gaps. Use (Author, Year) citations throughout. Return ONLY the literature review text."),
            ("methodology", "Write a detailed methodology section of 3-5 paragraphs. Justify the research design, explain data collection and analysis methods, address validity and ethical considerations. Return ONLY the methodology text."),
            ("findings", "Write a findings section of 3-5 paragraphs. Present key findings with supporting evidence. Use specific data points and citations. Organize thematically. Return ONLY the findings text."),
            ("discussion", "Write a discussion section of 4-6 paragraphs. Interpret findings in relation to the research questions and existing literature. Discuss implications, acknowledge limitations, and suggest future research. Return ONLY the discussion text."),
            ("conclusion", "Write a 2-3 paragraph conclusion. Summarize key contributions, restate the significance of the findings, and suggest directions for future research. Return ONLY the conclusion text."),
        ]

        for section_name, instruction in section_prompts:
            prompt = f"""{instruction}

CONTEXT:
{context}

Write in formal, graduate-level academic prose. No bullet points. Connected paragraphs only."""

            try:
                text = await self.gemini_service.generate_content(prompt)
                sections[section_name] = text.strip()
                logger.info(
                    "research_paper_section_generated",
                    section_name=section_name,
                    char_count=len(text),
                )
            except Exception as e:
                logger.error(
                    "research_paper_section_generation_failed",
                    section_name=section_name,
                    error=str(e),
                )
                sections[section_name] = f"[Section generation failed: {e}]"

        return sections

    async def _graduate_review_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Graduate-level critical review of the drafted paper."""
        return await self.quality_validator.graduate_review(langgraph_state)

    async def _revise_paper_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Revise the paper based on reviewer feedback."""
        return await self.quality_validator.revise_paper(langgraph_state)

    def _should_revise_paper(self, langgraph_state: dict[str, Any]) -> str:
        """Determine if the paper needs revision based on review."""
        return self.quality_validator.should_revise_paper(langgraph_state)

    async def _evaluate_consensus_phase(self, langgraph_state: dict[str, Any]) -> dict[str, Any]:
        """Evaluate consensus across all workers."""
        return await self.quality_validator.evaluate_consensus(langgraph_state)

    def _should_continue_refinement(self, langgraph_state: dict[str, Any]) -> str:
        """Determine if another refinement round is needed."""

        state = langgraph_state["supervision_state"]

        # Continue if consensus below threshold and rounds available
        if (
            state.consensus_score < self.quality_threshold
            and state.refinement_round < 1
        ):
            logger.info(
                "research_workflow_refinement_continuing",
                consensus_score=state.consensus_score,
                threshold=self.consensus_threshold,
                refinement_round=state.refinement_round,
            )
            return "continue"
        else:
            logger.info("research_workflow_completed")
            return "complete"

    async def get_research_quality_assessment(
        self, state: SupervisionState
    ) -> dict[str, Any]:
        """Get comprehensive research quality assessment."""
        return await self.quality_validator.get_research_quality_assessment(state)


__all__ = ["ResearchSupervisor"]
