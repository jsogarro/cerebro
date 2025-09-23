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

import asyncio
import logging
from typing import Dict, List, Optional, Any

from langgraph.graph import StateGraph, END

from .base_supervisor import (
    BaseSupervisor,
    SupervisionState,
    WorkerDefinition,
    SupervisionMode,
)
from ..models import AgentTask
from ..communication.talkhier_message import TalkHierContent, MessageType
from ..literature_review_agent import LiteratureReviewAgent
from ..methodology_agent import MethodologyAgent
from ..comparative_analysis_agent import ComparativeAnalysisAgent
from ..synthesis_agent import SynthesisAgent
from ..citation_agent import CitationAgent

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
        gemini_service: Optional[Any] = None,
        cache_client: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
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

    def _register_worker_types(self):
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

    def _build_workflow_graph(self):
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
            "coordinate_citation",
            self._create_langgraph_node(
                "coordinate_citation", self._coordinate_citation_phase
            ),
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
        self.workflow_graph.add_edge("coordinate_literature", "coordinate_methodology")
        self.workflow_graph.add_edge("coordinate_methodology", "coordinate_analysis")
        self.workflow_graph.add_edge("coordinate_analysis", "coordinate_synthesis")
        self.workflow_graph.add_edge("coordinate_synthesis", "coordinate_citation")
        self.workflow_graph.add_edge("coordinate_citation", "evaluate_consensus")

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
        self.workflow_graph = self.workflow_graph.compile()

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

    async def _plan_research_phase(self, langgraph_state: Dict) -> Dict:
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

    async def _coordinate_literature_phase(self, langgraph_state: Dict) -> Dict:
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

    async def _coordinate_methodology_phase(self, langgraph_state: Dict) -> Dict:
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

    async def _coordinate_analysis_phase(self, langgraph_state: Dict) -> Dict:
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

    async def _coordinate_synthesis_phase(self, langgraph_state: Dict) -> Dict:
        """Coordinate synthesis worker."""

        state = langgraph_state["supervision_state"]

        logger.info("Synthesis phase")
        state.current_phase = "synthesis"

        if "synthesis" in state.allocated_workers:
            # Prepare all agent outputs for synthesis
            agent_outputs = {}
            for worker_type, result in state.worker_results.items():
                if result and hasattr(result, "content"):
                    agent_outputs[worker_type] = {
                        "findings": result.content,
                        "confidence": result.confidence_score,
                        "background": result.background,
                    }

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

    async def _coordinate_citation_phase(self, langgraph_state: Dict) -> Dict:
        """Coordinate citation worker."""

        state = langgraph_state["supervision_state"]

        logger.info("Citation formatting phase")
        state.current_phase = "citation"

        if "citation" in state.allocated_workers:
            # Extract sources from literature review
            sources = []
            if "literature_review" in state.worker_results:
                lit_result = state.worker_results["literature_review"]
                if hasattr(lit_result, "intermediate_outputs"):
                    sources = lit_result.intermediate_outputs.get("sources", [])

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

    async def _evaluate_consensus_phase(self, langgraph_state: Dict) -> Dict:
        """Evaluate consensus across all workers."""

        state = langgraph_state["supervision_state"]

        logger.info("Consensus evaluation phase")
        state.current_phase = "consensus_evaluation"

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

        langgraph_state["supervision_state"] = state
        return langgraph_state

    def _should_continue_refinement(self, langgraph_state: Dict) -> str:
        """Determine if another refinement round is needed."""

        state = langgraph_state["supervision_state"]

        # Continue if consensus below threshold and rounds available
        if (
            state.consensus_score < self.quality_threshold
            and state.refinement_round < 3
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
    ) -> Dict[str, Any]:
        """Get comprehensive research quality assessment."""

        quality_assessment = {
            "overall_quality": state.quality_score,
            "consensus_score": state.consensus_score,
            "worker_contributions": {},
            "research_completeness": 0.0,
            "methodological_rigor": 0.0,
            "evidence_strength": 0.0,
        }

        # Assess each worker contribution
        for worker_type, result in state.worker_results.items():
            if result and hasattr(result, "confidence_score"):
                quality_assessment["worker_contributions"][worker_type] = {
                    "confidence": result.confidence_score,
                    "contribution_quality": result.confidence_score,  # Simplified
                }

        # Calculate research completeness
        expected_workers = ["literature_review", "methodology", "synthesis"]
        completed_workers = [w for w in expected_workers if w in state.worker_results]
        quality_assessment["research_completeness"] = len(completed_workers) / len(
            expected_workers
        )

        return quality_assessment


__all__ = ["ResearchSupervisor"]
