"""
Edge conditions and routing logic for LangGraph workflows.

This module defines the conditional logic that determines how the workflow
moves between different nodes based on the current state.
"""

from typing import Literal

from structlog import get_logger

from src.orchestration.state import AgentExecutionStatus, ResearchState, WorkflowPhase

logger = get_logger()


class EdgeConditions:
    """
    Collection of edge condition functions for workflow routing.

    Each method returns a routing decision based on the current state.
    """

    @staticmethod
    def should_proceed_to_planning(
        state: ResearchState,
    ) -> Literal["plan_generation", "error"]:
        """
        Determine if workflow should proceed to plan generation.

        Args:
            state: Current workflow state

        Returns:
            Next node to execute
        """
        if state.query and state.domains:
            return "plan_generation"
        return "error"

    @staticmethod
    def route_after_planning(
        state: ResearchState,
    ) -> Literal["literature_review", "methodology", "error"]:
        """
        Route workflow after research plan generation.

        Args:
            state: Current workflow state

        Returns:
            Next node based on research plan
        """
        if not state.research_plan:
            return "error"

        plan = state.research_plan

        # Check research type from plan
        research_type = plan.get("research_type", "comprehensive")

        if research_type == "literature_focused":
            return "literature_review"
        elif research_type == "methodology_focused":
            return "methodology"
        else:
            # Default to literature review for comprehensive research
            return "literature_review"

    @staticmethod
    def should_run_parallel_agents(state: ResearchState) -> list[str]:
        """
        Determine which agents can run in parallel.

        Args:
            state: Current workflow state

        Returns:
            List of agent nodes that can execute in parallel
        """
        parallel_agents = []

        # After literature review, we can run multiple agents
        if (state.current_phase == WorkflowPhase.LITERATURE_REVIEW and
            "literature_review" in state.completed_agents):
            # These agents can run in parallel after literature review
            if "comparative_analysis" not in state.completed_agents:
                parallel_agents.append("comparative_analysis")
            if "methodology" not in state.completed_agents:
                parallel_agents.append("methodology")

        return parallel_agents

    @staticmethod
    def check_quality_threshold(
        state: ResearchState,
    ) -> Literal["report_generation", "quality_improvement", "synthesis"]:
        """
        Check if quality threshold is met for report generation.

        Args:
            state: Current workflow state

        Returns:
            Next node based on quality score
        """
        MIN_QUALITY_SCORE = 0.7  # noqa: N806

        if state.quality_score >= MIN_QUALITY_SCORE:
            return "report_generation"
        elif state.error_count < state.max_errors:
            # Try to improve quality
            return "quality_improvement"
        else:
            # Fall back to synthesis with current results
            return "synthesis"

    @staticmethod
    def handle_agent_failure(state: ResearchState) -> Literal["retry", "skip", "fail"]:
        """
        Determine how to handle agent failure.

        Args:
            state: Current workflow state

        Returns:
            Action to take for failed agent
        """
        # Check retry count for failed agents
        for _task_id, task in state.agent_tasks.items():
            if task.status == AgentExecutionStatus.FAILED:
                if task.retry_count < 3:
                    return "retry"
                elif task.agent_type in ["literature_review", "synthesis"]:
                    # Critical agents - fail the workflow
                    return "fail"
                else:
                    # Non-critical agents - skip and continue
                    return "skip"

        return "skip"

    @staticmethod
    def should_checkpoint(state: ResearchState) -> bool:
        """
        Determine if state should be checkpointed.

        Args:
            state: Current workflow state

        Returns:
            True if checkpoint should be created
        """
        return state.should_checkpoint()

    @staticmethod
    def is_workflow_complete(state: ResearchState) -> bool:
        """
        Check if workflow is complete.

        Args:
            state: Current workflow state

        Returns:
            True if workflow is complete
        """
        required_agents = {"literature_review", "synthesis"}
        return (
            required_agents.issubset(state.completed_agents)
            and state.research_plan is not None
            and state.quality_score > 0
        )

    @staticmethod
    def route_conflict_resolution(
        state: ResearchState,
    ) -> Literal["auto_resolve", "human_review", "continue"]:
        """
        Route conflicts to appropriate resolution strategy.

        Args:
            state: Current workflow state

        Returns:
            Conflict resolution strategy
        """
        if not state.conflicts:
            return "continue"

        # Check conflict severity
        high_severity_conflicts = [
            c for c in state.conflicts if c.get("severity", "low") == "high"
        ]

        if high_severity_conflicts:
            return "human_review"
        else:
            return "auto_resolve"


class RouterConfig:
    """Configuration for workflow routing."""

    def __init__(
        self,
        enable_parallel_execution: bool = True,
        max_parallel_agents: int = 3,
        quality_threshold: float = 0.7,
        checkpoint_frequency: int = 5,
        enable_human_in_loop: bool = False,
    ):
        """
        Initialize router configuration.

        Args:
            enable_parallel_execution: Whether to allow parallel agent execution
            max_parallel_agents: Maximum number of agents to run in parallel
            quality_threshold: Minimum quality score for report generation
            checkpoint_frequency: How often to create checkpoints
            enable_human_in_loop: Whether to enable human review for conflicts
        """
        self.enable_parallel_execution = enable_parallel_execution
        self.max_parallel_agents = max_parallel_agents
        self.quality_threshold = quality_threshold
        self.checkpoint_frequency = checkpoint_frequency
        self.enable_human_in_loop = enable_human_in_loop


class WorkflowRouter:
    """
    Main router for workflow execution.

    Manages the flow of execution through the graph based on state and conditions.
    """

    def __init__(self, config: RouterConfig | None = None):
        """
        Initialize workflow router.

        Args:
            config: Router configuration
        """
        self.config = config or RouterConfig()
        self.conditions = EdgeConditions()

    def route_from_phase(self, state: ResearchState) -> str:
        """
        Determine next node based on current phase.

        Args:
            state: Current workflow state

        Returns:
            Name of next node to execute
        """
        phase_routing = {
            WorkflowPhase.INITIALIZATION: self._route_from_init,
            WorkflowPhase.QUERY_ANALYSIS: self._route_from_query_analysis,
            WorkflowPhase.PLAN_GENERATION: self._route_from_planning,
            WorkflowPhase.LITERATURE_REVIEW: self._route_from_literature,
            WorkflowPhase.COMPARATIVE_ANALYSIS: self._route_from_comparison,
            WorkflowPhase.METHODOLOGY_DESIGN: self._route_from_methodology,
            WorkflowPhase.SYNTHESIS: self._route_from_synthesis,
            WorkflowPhase.CITATION_VERIFICATION: self._route_from_citation,
            WorkflowPhase.QUALITY_CHECK: self._route_from_quality,
            WorkflowPhase.REPORT_GENERATION: self._route_from_report,
        }

        router = phase_routing.get(state.current_phase, self._route_default)
        return router(state)

    def _route_from_init(self, state: ResearchState) -> str:
        """Route from initialization phase."""
        return "query_analysis"

    def _route_from_query_analysis(self, state: ResearchState) -> str:
        """Route from query analysis phase."""
        return self.conditions.should_proceed_to_planning(state)

    def _route_from_planning(self, state: ResearchState) -> str:
        """Route from planning phase."""
        return self.conditions.route_after_planning(state)

    def _route_from_literature(self, state: ResearchState) -> str:
        """Route from literature review phase."""
        if self.config.enable_parallel_execution:
            parallel_agents = self.conditions.should_run_parallel_agents(state)
            if parallel_agents:
                return "parallel_execution"
        return "comparative_analysis"

    def _route_from_comparison(self, state: ResearchState) -> str:
        """Route from comparative analysis phase."""
        return "methodology"

    def _route_from_methodology(self, state: ResearchState) -> str:
        """Route from methodology phase."""
        return "synthesis"

    def _route_from_synthesis(self, state: ResearchState) -> str:
        """Route from synthesis phase."""
        return "citation_verification"

    def _route_from_citation(self, state: ResearchState) -> str:
        """Route from citation verification phase."""
        return "quality_check"

    def _route_from_quality(self, state: ResearchState) -> str:
        """Route from quality check phase."""
        return self.conditions.check_quality_threshold(state)

    def _route_from_report(self, state: ResearchState) -> str:
        """Route from report generation phase."""
        if self.conditions.is_workflow_complete(state):
            return "END"
        return "quality_check"

    def _route_default(self, state: ResearchState) -> str:
        """Default routing when phase is not recognized."""
        logger.warning(f"Unknown phase: {state.current_phase}, routing to error")
        return "error"

    def get_parallel_nodes(self, state: ResearchState) -> list[str]:
        """
        Get list of nodes that can execute in parallel.

        Args:
            state: Current workflow state

        Returns:
            List of node names for parallel execution
        """
        if not self.config.enable_parallel_execution:
            return []

        parallel_nodes = self.conditions.should_run_parallel_agents(state)

        # Limit number of parallel executions
        if len(parallel_nodes) > self.config.max_parallel_agents:
            parallel_nodes = parallel_nodes[: self.config.max_parallel_agents]

        return parallel_nodes

    def should_create_checkpoint(self, state: ResearchState) -> bool:
        """
        Determine if checkpoint should be created.

        Args:
            state: Current workflow state

        Returns:
            True if checkpoint should be created
        """
        return self.conditions.should_checkpoint(state)


__all__ = [
    "EdgeConditions",
    "RouterConfig",
    "WorkflowRouter",
]
