"""Analytics Supervisor Agent

Coordinates analytics teams for data analysis, statistical modeling, and insight
generation. Implements proven analytics workflows with TalkHier protocol and
LangGraph orchestration.

Coordinates:
- Data Analysis Agent: Data exploration and statistical analysis
- Statistical Modeling Agent: Model building and validation
- Insight Synthesis Agent: Pattern recognition and insight generation
"""

from typing import Any

from langgraph.graph import END, StateGraph
from structlog import get_logger

from ..analytics_agents import (
    DataAnalysisAgent,
    InsightSynthesisAgent,
    StatisticalModelingAgent,
)
from ..communication.talkhier_message import MessageType, TalkHierContent
from ..models import AgentTask
from .base_supervisor import BaseSupervisor, SupervisionState, WorkerDefinition

logger = get_logger()


class AnalyticsSupervisor(BaseSupervisor):
    """Analytics team supervisor implementing data analysis workflows.

    Manages the complete analytics lifecycle:
    1. Data exploration and statistical analysis
    2. Statistical modeling and validation
    3. Insight synthesis and pattern recognition

    Uses LangGraph for state management and TalkHier for quality assurance.
    """

    def __init__(
        self,
        supervisor_type: str = "analytics",
        domain: str = "analytics",
        gemini_service: Any | None = None,
        cache_client: Any | None = None,
        config: dict[str, Any] | None = None,
    ):
        """Initialize analytics supervisor.

        Accepts ``supervisor_type`` and ``domain`` to match the
        ``BaseSupervisor`` signature used by the MASR supervisor bridge and
        factory, which instantiate every supervisor uniformly. They default to
        ``"analytics"`` so direct construction (and tests) need not pass them.
        """
        super().__init__(
            supervisor_type=supervisor_type,
            domain=domain,
            gemini_service=gemini_service,
            cache_client=cache_client,
            config=config,
        )

        # Analytics-specific configuration
        self.analysis_depth = (
            config.get("analysis_depth", "comprehensive") if config else "comprehensive"
        )
        self.confidence_level = config.get("confidence_level", 0.95) if config else 0.95
        self.max_modeling_iterations = (
            config.get("max_modeling_iterations", 3) if config else 3
        )

    def _register_worker_types(self) -> None:
        """Register analytics worker types."""
        self.worker_definitions.update(
            {
                "data_analysis": WorkerDefinition(
                    worker_type="data_analysis",
                    agent_class=DataAnalysisAgent,
                    specialization="Data exploration and statistical analysis",
                    capabilities=[
                        "descriptive_statistics",
                        "exploratory_analysis",
                        "data_visualization",
                    ],
                    required_for=["analytics", "data_analysis"],
                    optimal_for=["statistical_analysis", "data_exploration"],
                    avg_execution_time_ms=30000,
                    reliability_score=0.94,
                    quality_score=0.90,
                ),
                "statistical_modeling": WorkerDefinition(
                    worker_type="statistical_modeling",
                    agent_class=StatisticalModelingAgent,
                    specialization="Statistical model building and validation",
                    capabilities=["model_building", "validation", "hypothesis_testing"],
                    required_for=["analytics", "modeling"],
                    optimal_for=["predictive_analytics", "statistical_inference"],
                    avg_execution_time_ms=40000,
                    reliability_score=0.92,
                    quality_score=0.88,
                ),
                "insight_synthesis": WorkerDefinition(
                    worker_type="insight_synthesis",
                    agent_class=InsightSynthesisAgent,
                    specialization="Pattern recognition and insight generation",
                    capabilities=[
                        "pattern_recognition",
                        "insight_generation",
                        "recommendation",
                    ],
                    required_for=["analytics", "insights"],
                    optimal_for=["business_intelligence", "data_storytelling"],
                    avg_execution_time_ms=25000,
                    reliability_score=0.91,
                    quality_score=0.87,
                ),
            },
        )

    def _build_workflow_graph(self) -> None:
        """Build LangGraph workflow for analytics supervision."""
        self.workflow_graph = StateGraph(dict)

        # Add nodes for each phase
        self.workflow_graph.add_node(
            "plan_analysis",
            self._create_langgraph_node("plan_analysis", self._plan_analysis_phase),
        )
        self.workflow_graph.add_node(
            "coordinate_data_analysis",
            self._create_langgraph_node(
                "coordinate_data_analysis", self._coordinate_data_analysis_phase,
            ),
        )
        self.workflow_graph.add_node(
            "coordinate_modeling",
            self._create_langgraph_node(
                "coordinate_modeling", self._coordinate_modeling_phase,
            ),
        )
        self.workflow_graph.add_node(
            "coordinate_insights",
            self._create_langgraph_node(
                "coordinate_insights", self._coordinate_insights_phase,
            ),
        )
        self.workflow_graph.add_node(
            "evaluate_confidence",
            self._create_langgraph_node(
                "evaluate_confidence", self._evaluate_confidence_phase,
            ),
        )

        # Add edges for analytics workflow
        self.workflow_graph.set_entry_point("plan_analysis")
        self.workflow_graph.add_edge("plan_analysis", "coordinate_data_analysis")
        self.workflow_graph.add_edge("coordinate_data_analysis", "coordinate_modeling")
        self.workflow_graph.add_edge("coordinate_modeling", "coordinate_insights")
        self.workflow_graph.add_edge("coordinate_insights", "evaluate_confidence")
        self.workflow_graph.add_edge("evaluate_confidence", END)

        # Compile the graph
        self.workflow_graph = self.workflow_graph.compile()  # type: Any

    async def _coordinate_workers(
        self, state: SupervisionState, task: AgentTask,
    ) -> SupervisionState:
        """Analytics-specific worker coordination."""
        allocated_workers = await self.allocate_workers(task)
        state.allocated_workers = allocated_workers
        state.context.update(
            {
                "analysis_depth": self.analysis_depth,
                "confidence_level": self.confidence_level,
            },
        )
        return state

    async def _plan_analysis_phase(
        self, langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Plan analytics execution strategy."""
        state = langgraph_state["supervision_state"]
        task = langgraph_state["original_task"]

        logger.info("analytics_supervisor_phase_started", phase="planning")
        state.current_phase = "planning"
        state = await self._coordinate_workers(state, task)

        # Initialize worker tasks for analytics
        analytics_query = state.original_query
        state.worker_tasks = {
            "data_analysis": {
                "query": analytics_query,
                "task_type": "data_analysis",
                "context": {
                    "analysis_depth": self.analysis_depth,
                    "confidence_level": self.confidence_level,
                },
            },
        }

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _coordinate_data_analysis_phase(
        self, langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Coordinate data analysis worker."""
        state = langgraph_state["supervision_state"]
        logger.info("analytics_supervisor_phase_started", phase="data_analysis")
        state.current_phase = "data_analysis"

        # Execute data analysis via TalkHier message
        if "data_analysis" in self.active_workers:
            message_content = TalkHierContent(
                content=f"Perform data analysis for: {state.original_query}",
                background="Conduct exploratory data analysis and descriptive statistics",
                intermediate_outputs=state.context,
            )
            response = await self.send_talkhier_message(
                "data_analysis", MessageType.SUPERVISOR_ASSIGNMENT, message_content,
            )
            if response:
                state.worker_results["data_analysis"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _coordinate_modeling_phase(
        self, langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Coordinate statistical modeling worker."""
        state = langgraph_state["supervision_state"]
        logger.info("analytics_supervisor_phase_started", phase="modeling")
        state.current_phase = "modeling"

        # Execute modeling via TalkHier message
        if "statistical_modeling" in self.active_workers:
            analysis_results = state.worker_results.get("data_analysis", {})
            message_content = TalkHierContent(
                content="Build and validate statistical models",
                background=analysis_results.content
                if hasattr(analysis_results, "content")
                else "",
                intermediate_outputs={
                    "iteration": 1,
                    "max_iterations": self.max_modeling_iterations,
                },
            )
            response = await self.send_talkhier_message(
                "statistical_modeling",
                MessageType.SUPERVISOR_ASSIGNMENT,
                message_content,
            )
            if response:
                state.worker_results["statistical_modeling"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _coordinate_insights_phase(
        self, langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Coordinate insight synthesis worker."""
        state = langgraph_state["supervision_state"]
        logger.info("analytics_supervisor_phase_started", phase="insights")
        state.current_phase = "insights"

        # Execute insight synthesis via TalkHier message
        if "insight_synthesis" in self.active_workers:
            modeling_results = state.worker_results.get("statistical_modeling", {})
            message_content = TalkHierContent(
                content="Generate insights and recommendations from analysis",
                background=modeling_results.content
                if hasattr(modeling_results, "content")
                else "",
                intermediate_outputs={"focus": "actionable_insights"},
            )
            response = await self.send_talkhier_message(
                "insight_synthesis", MessageType.SUPERVISOR_ASSIGNMENT, message_content,
            )
            if response:
                state.worker_results["insight_synthesis"] = response.talkhier_content

        langgraph_state["supervision_state"] = state
        return langgraph_state

    async def _evaluate_confidence_phase(
        self, langgraph_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Evaluate confidence in analytics results."""
        state = langgraph_state["supervision_state"]
        logger.info("analytics_supervisor_phase_started", phase="confidence_evaluation")
        state.current_phase = "confidence_evaluation"

        # Aggregate worker outputs for verification
        aggregated_content = self._aggregate_worker_content(state)

        # Run verification QA gate
        verification_result = await self._run_verification(aggregated_content)
        state.context["verification"] = verification_result
        state.worker_results["verification"] = verification_result

        # Confidence scoring based on completion
        completed_phases = len(state.worker_results) - 1  # exclude verification
        base_score = min(0.92, 0.6 + (completed_phases * 0.11))

        # Reduce score if verification found issues
        if verification_result["verdict"] == "revise":
            state.quality_score = base_score * 0.85
            logger.info(
                "analytics_supervisor_verification_flagged_issues",
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
        for worker_type in [
            "data_analysis",
            "statistical_modeling",
            "insight_synthesis",
        ]:
            result = state.worker_results.get(worker_type)
            if result:
                content = result.get("content", "") if isinstance(result, dict) else ""
                if hasattr(result, "content"):
                    content = result.content
                if content:
                    parts.append(f"{worker_type.upper()}: {content}")
        return "\n\n".join(parts) if parts else ""


__all__ = ["AnalyticsSupervisor"]
