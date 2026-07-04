"""Content Supervisor Agent

Coordinates content creation teams for writing, editing, and optimizing
various forms of content. Implements proven content workflows with
TalkHier protocol and LangGraph orchestration.

Coordinates:
- Content Planning Agent: Topic research and content strategy
- Writing Agent: Draft creation and content generation
- Editing Agent: Quality review and improvement
- Optimization Agent: SEO and readability optimization
"""

from typing import Any

from langgraph.graph import END, StateGraph
from structlog import get_logger

from ..communication.talkhier_message import MessageType, TalkHierContent
from ..content_agents import (
    ContentPlanningAgent,
    DraftingAgent,
    EditingAgent,
    OptimizationAgent,
)
from ..models import AgentTask
from .base_supervisor import BaseSupervisor, SupervisionState, WorkerDefinition

logger = get_logger()


class ContentSupervisor(BaseSupervisor):
    """Content team supervisor implementing content creation workflows.

    Manages the complete content lifecycle:
    1. Content planning and strategy
    2. Draft creation and writing
    3. Editing and quality improvement
    4. Optimization for target audience and platform

    Uses LangGraph for state management and TalkHier for quality assurance.
    """

    def __init__(
        self,
        supervisor_type: str = "content",
        domain: str = "content",
        gemini_service: Any | None = None,
        cache_client: Any | None = None,
        config: dict[str, Any] | None = None,
    ):
        """Initialize content supervisor.

        Accepts ``supervisor_type`` and ``domain`` to match the
        ``BaseSupervisor`` signature used by the MASR supervisor bridge and
        factory, which instantiate every supervisor uniformly. They default to
        ``"content"`` so direct construction (and tests) need not pass them.
        """
        super().__init__(
            supervisor_type=supervisor_type,
            domain=domain,
            gemini_service=gemini_service,
            cache_client=cache_client,
            config=config,
        )

        # Content-specific configuration
        self.content_format = (
            config.get("content_format", "article") if config else "article"
        )
        self.target_audience = (
            config.get("target_audience", "general") if config else "general"
        )
        self.max_editing_rounds = config.get("max_editing_rounds", 2) if config else 2

    def _register_worker_types(self) -> None:
        """Register content worker types."""
        self.worker_definitions.update(
            {
                "content_planning": WorkerDefinition(
                    worker_type="content_planning",
                    agent_class=ContentPlanningAgent,
                    specialization="Content strategy and topic research",
                    capabilities=[
                        "topic_research",
                        "audience_analysis",
                        "structure_planning",
                    ],
                    required_for=["content", "content_creation"],
                    optimal_for=["blog_post", "article", "documentation"],
                    avg_execution_time_ms=20000,
                    reliability_score=0.93,
                    quality_score=0.88,
                ),
                "drafting": WorkerDefinition(
                    worker_type="drafting",
                    agent_class=DraftingAgent,
                    specialization="Content creation and writing",
                    capabilities=[
                        "writing",
                        "tone_matching",
                        "structure_implementation",
                    ],
                    required_for=["content", "writing"],
                    optimal_for=["article", "blog_post", "copy"],
                    avg_execution_time_ms=35000,
                    reliability_score=0.91,
                    quality_score=0.87,
                ),
                "editing": WorkerDefinition(
                    worker_type="editing",
                    agent_class=EditingAgent,
                    specialization="Content editing and improvement",
                    capabilities=[
                        "grammar_check",
                        "clarity_improvement",
                        "fact_checking",
                    ],
                    required_for=["content", "quality_assurance"],
                    optimal_for=["polishing", "quality_review"],
                    avg_execution_time_ms=25000,
                    reliability_score=0.95,
                    quality_score=0.92,
                ),
                "optimization": WorkerDefinition(
                    worker_type="optimization",
                    agent_class=OptimizationAgent,
                    specialization="SEO and readability optimization",
                    capabilities=[
                        "seo_optimization",
                        "readability_analysis",
                        "keyword_research",
                    ],
                    required_for=["content", "optimization"],
                    optimal_for=["seo_content", "web_content"],
                    avg_execution_time_ms=20000,
                    reliability_score=0.90,
                    quality_score=0.85,
                ),
            },
        )

    def _build_workflow_graph(self) -> None:
        """Build LangGraph workflow for content supervision."""
        self.workflow_graph = StateGraph(dict)

        # Add nodes for each phase
        self.workflow_graph.add_node(
            "plan_content",
            self._create_langgraph_node("plan_content", self._plan_content_phase),
        )
        self.workflow_graph.add_node(
            "coordinate_drafting",
            self._create_langgraph_node(
                "coordinate_drafting", self._coordinate_drafting_phase,
            ),
        )
        self.workflow_graph.add_node(
            "coordinate_editing",
            self._create_langgraph_node(
                "coordinate_editing", self._coordinate_editing_phase,
            ),
        )
        self.workflow_graph.add_node(
            "coordinate_optimization",
            self._create_langgraph_node(
                "coordinate_optimization", self._coordinate_optimization_phase,
            ),
        )
        self.workflow_graph.add_node(
            "evaluate_quality",
            self._create_langgraph_node(
                "evaluate_quality", self._evaluate_quality_phase,
            ),
        )

        # Add edges for content workflow
        self.workflow_graph.set_entry_point("plan_content")
        self.workflow_graph.add_edge("plan_content", "coordinate_drafting")
        self.workflow_graph.add_edge("coordinate_drafting", "coordinate_editing")
        self.workflow_graph.add_edge("coordinate_editing", "coordinate_optimization")
        self.workflow_graph.add_edge("coordinate_optimization", "evaluate_quality")
        self.workflow_graph.add_edge("evaluate_quality", END)

        # Compile the graph
        self.workflow_graph = self.workflow_graph.compile()  # type: Any

    async def _coordinate_workers(
        self, state: SupervisionState, task: AgentTask,
    ) -> SupervisionState:
        """Content-specific worker coordination."""
        allocated_workers = await self.allocate_workers(task)
        state.allocated_workers = allocated_workers
        state.context.update(
            {
                "content_format": self.content_format,
                "target_audience": self.target_audience,
            },
        )
        return state

    async def _plan_content_phase(
        self, langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Plan content creation strategy."""
        state = langgraph_state["supervision_state"]
        task = langgraph_state["original_task"]

        logger.info("content_supervisor_phase_started", phase="planning")
        state.current_phase = "planning"
        state = await self._coordinate_workers(state, task)

        # Initialize worker tasks for content creation
        content_query = state.original_query
        state.worker_tasks = {
            "content_planning": {
                "query": content_query,
                "task_type": "content_planning",
                "context": {
                    "format": self.content_format,
                    "audience": self.target_audience,
                },
            },
        }

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _coordinate_drafting_phase(
        self, langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Coordinate drafting worker."""
        state = langgraph_state["supervision_state"]
        logger.info("content_supervisor_phase_started", phase="drafting")
        state.current_phase = "drafting"

        # Execute drafting via TalkHier message
        if "drafting" in self.active_workers:
            message_content = TalkHierContent(
                content=f"Create content draft for: {state.original_query}",
                background=state.worker_results.get("content_planning", {}).get(
                    "content", "",
                )
                if "content_planning" in state.worker_results
                else "",
                intermediate_outputs=state.context,
            )
            response = await self.send_talkhier_message(
                "drafting", MessageType.SUPERVISOR_ASSIGNMENT, message_content,
            )
            if response:
                state.worker_results["drafting"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _coordinate_editing_phase(
        self, langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Coordinate editing worker."""
        state = langgraph_state["supervision_state"]
        logger.info("content_supervisor_phase_started", phase="editing")
        state.current_phase = "editing"

        # Execute editing via TalkHier message
        if "editing" in self.active_workers:
            draft_content = state.worker_results.get("drafting", {})
            message_content = TalkHierContent(
                content="Review and improve content quality",
                background=draft_content.content
                if hasattr(draft_content, "content")
                else "",
                intermediate_outputs={"editing_round": 1},
            )
            response = await self.send_talkhier_message(
                "editing", MessageType.SUPERVISOR_ASSIGNMENT, message_content,
            )
            if response:
                state.worker_results["editing"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _coordinate_optimization_phase(
        self, langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Coordinate optimization worker."""
        state = langgraph_state["supervision_state"]
        logger.info("content_supervisor_phase_started", phase="optimization")
        state.current_phase = "optimization"

        # Execute optimization via TalkHier message
        if "optimization" in self.active_workers:
            edited_content = state.worker_results.get("editing", {})
            message_content = TalkHierContent(
                content="Optimize content for SEO and readability",
                background=edited_content.content
                if hasattr(edited_content, "content")
                else "",
                intermediate_outputs={
                    "target_keywords": state.context.get("keywords", []),
                },
            )
            response = await self.send_talkhier_message(
                "optimization", MessageType.SUPERVISOR_ASSIGNMENT, message_content,
            )
            if response:
                state.worker_results["optimization"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _evaluate_quality_phase(
        self, langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Evaluate final content quality."""
        state = langgraph_state["supervision_state"]
        logger.info("content_supervisor_phase_started", phase="quality_evaluation")
        state.current_phase = "quality_evaluation"

        # Aggregate worker outputs for verification
        aggregated_content = self._aggregate_worker_content(state)

        # Run verification QA gate
        verification_result = await self._run_verification(aggregated_content)
        state.context["verification"] = verification_result
        state.worker_results["verification"] = verification_result

        # Quality scoring based on completion
        completed_phases = len(state.worker_results) - 1  # exclude verification
        base_score = min(0.9, 0.5 + (completed_phases * 0.1))

        # Reduce score if verification found issues
        if verification_result["verdict"] == "revise":
            state.quality_score = base_score * 0.85
            logger.info(
                "content_supervisor_verification_flagged_issues",
                base_score=base_score,
                adjusted_score=state.quality_score,
            )
        else:
            state.quality_score = base_score

        state.consensus_score = state.quality_score

        langgraph_state["supervision_state"] = state
        return langgraph_state

    def _aggregate_worker_content(self, state: SupervisionState) -> str:
        """Aggregate worker outputs into a single text for verification."""
        parts = []
        for worker_type in ["drafting", "editing", "optimization"]:
            result = state.worker_results.get(worker_type)
            if result:
                content = result.get("content", "") if isinstance(result, dict) else ""
                if hasattr(result, "content"):
                    content = result.content
                if content:
                    parts.append(f"{worker_type.upper()}: {content}")
        return "\n\n".join(parts) if parts else ""


__all__ = ["ContentSupervisor"]
