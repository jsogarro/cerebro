"""
Bridge between LangGraph orchestration and Temporal workflows.

This module provides integration between the LangGraph orchestration system
and the existing Temporal workflow infrastructure, allowing both systems
to work together seamlessly.
"""

import asyncio
import logging
import uuid
from datetime import timedelta
from typing import Any, cast

from temporalio import activity, workflow
from temporalio.client import Client as TemporalClient
from temporalio.common import RetryPolicy

from src.orchestration.research_orchestrator import (
    OrchestratorConfig,
    ResearchOrchestrator,
    WorkflowResult,
)
from src.orchestration.state import ResearchState, WorkflowPhase
from src.temporal.client import get_temporal_client
from src.temporal.workflows.research_workflow import ResearchWorkflow

logger = logging.getLogger(__name__)


class TemporalBridge:
    """
    Bridge between LangGraph and Temporal orchestration systems.

    Enables running LangGraph workflows as Temporal activities and
    vice versa, providing flexibility in orchestration approach.
    """

    def __init__(
        self,
        temporal_client: TemporalClient | None = None,
        orchestrator: ResearchOrchestrator | None = None,
    ):
        """
        Initialize Temporal bridge.

        Args:
            temporal_client: Temporal client instance
            orchestrator: LangGraph orchestrator instance
        """
        self.temporal_client = temporal_client
        self.orchestrator = orchestrator or ResearchOrchestrator()
        self._activity_functions: dict[str, Any] = {}
        self._workflow_mappings: dict[str, Any] = {}

        # Register activity functions
        self._register_activities()

    def _register_activities(self) -> None:
        """Register LangGraph nodes as Temporal activities."""
        # Map LangGraph nodes to Temporal activities
        from src.orchestration.nodes import (
            agent_dispatch_node,
            plan_generation_node,
            quality_check_node,
            query_analysis_node,
            report_generation_node,
            result_aggregation_node,
        )

        self._activity_functions = {
            "query_analysis_activity": self._wrap_as_activity(query_analysis_node),
            "plan_generation_activity": self._wrap_as_activity(plan_generation_node),
            "agent_dispatch_activity": self._wrap_as_activity(agent_dispatch_node),
            "result_aggregation_activity": self._wrap_as_activity(
                result_aggregation_node
            ),
            "quality_check_activity": self._wrap_as_activity(quality_check_node),
            "report_generation_activity": self._wrap_as_activity(
                report_generation_node
            ),
        }

    def _wrap_as_activity(self, node_func: Any) -> Any:
        """
        Wrap a LangGraph node function as a Temporal activity.

        Args:
            node_func: LangGraph node function

        Returns:
            Temporal activity function
        """

        @activity.defn(name=f"{node_func.__name__}_activity")
        async def temporal_activity(state_dict: dict[str, Any]) -> dict[str, Any]:
            """Execute LangGraph node as Temporal activity."""
            try:
                # Reconstruct ResearchState from dictionary
                state = self._dict_to_state(state_dict)

                # Execute node function
                result_state = await node_func(state)

                # Convert back to dictionary
                return self._state_to_dict(result_state)

            except Exception as e:
                logger.error(f"Activity execution failed: {e}")
                raise

        return cast(Any, temporal_activity)

    def _state_to_dict(self, state: ResearchState) -> dict[str, Any]:
        """
        Convert ResearchState to dictionary for Temporal.

        Args:
            state: Research state object

        Returns:
            Dictionary representation
        """
        return {
            "project_id": state.project_id,
            "workflow_id": state.workflow_id,
            "query": state.query,
            "domains": state.domains,
            "research_plan": state.research_plan,
            "current_phase": state.current_phase.value,
            "previous_phase": (
                state.previous_phase.value if state.previous_phase else None
            ),
            "agent_tasks": {
                k: {
                    "task_id": v.task_id,
                    "agent_type": v.agent_type,
                    "status": v.status.value,
                    "input_data": v.input_data,
                    "retry_count": v.retry_count,
                }
                for k, v in state.agent_tasks.items()
            },
            "agent_results": {
                k: {
                    "task_id": v.task_id,
                    "status": v.status,
                    "output": v.output,
                    "confidence": v.confidence,
                    "execution_time": v.execution_time,
                    "metadata": v.metadata,
                } for k, v in state.agent_results.items()
            },
            "completed_agents": list(state.completed_agents),
            "failed_agents": list(state.failed_agents),
            "pending_agents": list(state.pending_agents),
            "quality_score": state.quality_score,
            "validation_errors": state.validation_errors,
            "conflicts": state.conflicts,
            "error_count": state.error_count,
            "context": state.context,
        }

    def _dict_to_state(self, state_dict: dict[str, Any]) -> ResearchState:
        """
        Convert dictionary to ResearchState.

        Args:
            state_dict: Dictionary representation

        Returns:
            Research state object
        """
        from src.orchestration.state import AgentExecutionStatus, AgentTaskState

        state = ResearchState(
            project_id=state_dict["project_id"],
            workflow_id=state_dict["workflow_id"],
            query=state_dict["query"],
            domains=state_dict["domains"],
        )

        # Restore fields
        state.research_plan = state_dict.get("research_plan")
        state.current_phase = WorkflowPhase(state_dict["current_phase"])

        if state_dict.get("previous_phase"):
            state.previous_phase = WorkflowPhase(state_dict["previous_phase"])

        # Restore agent tasks
        for task_id, task_data in state_dict.get("agent_tasks", {}).items():
            state.agent_tasks[task_id] = AgentTaskState(
                task_id=task_data["task_id"],
                agent_type=task_data["agent_type"],
                status=AgentExecutionStatus(task_data["status"]),
                input_data=task_data["input_data"],
                retry_count=task_data.get("retry_count", 0),
            )

        # Restore agent results (simplified - would need proper AgentResult reconstruction)
        state.agent_results = state_dict.get("agent_results", {})

        # Restore sets
        state.completed_agents = set(state_dict.get("completed_agents", []))
        state.failed_agents = set(state_dict.get("failed_agents", []))
        state.pending_agents = set(state_dict.get("pending_agents", []))

        # Restore other fields
        state.quality_score = state_dict.get("quality_score", 0.0)
        state.validation_errors = state_dict.get("validation_errors", [])
        state.conflicts = state_dict.get("conflicts", [])
        state.error_count = state_dict.get("error_count", 0)
        state.context = state_dict.get("context", {})

        return state

    async def run_langgraph_in_temporal(
        self, project_data: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Run LangGraph orchestration within Temporal workflow.

        This allows Temporal to manage the overall workflow while
        using LangGraph for intelligent agent coordination.

        Args:
            project_data: Project configuration

        Returns:
            Workflow result
        """
        logger.info("Running LangGraph orchestration in Temporal")

        # Create workflow handle
        if not self.temporal_client:
            self.temporal_client = await get_temporal_client()

        # Start Temporal workflow that wraps LangGraph
        workflow_id = f"langgraph-{uuid.uuid4().hex[:8]}"

        handle = await self.temporal_client.start_workflow(
            LangGraphTemporalWorkflow.run,
            project_data,
            id=workflow_id,
            task_queue="research-queue",
            retry_policy=RetryPolicy(
                maximum_attempts=3,
                initial_interval=timedelta(seconds=1),
                maximum_interval=timedelta(seconds=30),
                backoff_coefficient=2,
            ),
        )

        # Wait for result
        result = await handle.result()

        return cast(dict[str, Any], result)

    async def run_temporal_in_langgraph(self, state: ResearchState) -> ResearchState:
        """
        Run Temporal workflow as part of LangGraph orchestration.

        This allows LangGraph to delegate specific tasks to Temporal
        workflows for reliability and fault tolerance.

        Args:
            state: Current LangGraph state

        Returns:
            Updated state
        """
        logger.info("Running Temporal workflow from LangGraph")

        # Convert state to project data for Temporal
        project_data = {
            "id": state.project_id,
            "title": state.query,
            "query": state.query,
            "domains": state.domains,
            "research_plan": state.research_plan,
            "context": state.context,
        }

        # Start Temporal workflow
        if not self.temporal_client:
            self.temporal_client = await get_temporal_client()

        workflow_id = f"temporal-{state.workflow_id}"

        handle = await self.temporal_client.start_workflow(
            ResearchWorkflow.run,
            project_data,
            id=workflow_id,
            task_queue="research-queue",
        )

        # Wait for result
        temporal_result = await handle.result()

        # Update LangGraph state with Temporal results
        state.context["temporal_result"] = temporal_result

        # Extract relevant data from Temporal result
        if temporal_result.get("literature_result"):
            state.agent_results["literature_review"] = temporal_result[
                "literature_result"
            ]
            state.completed_agents.add("literature_review")

        if temporal_result.get("synthesis_result"):
            state.agent_results["synthesis"] = temporal_result["synthesis_result"]
            state.completed_agents.add("synthesis")

        return state

    def create_hybrid_workflow(self) -> "HybridWorkflow":
        """
        Create a hybrid workflow that combines LangGraph and Temporal.

        Returns:
            Hybrid workflow instance
        """
        return HybridWorkflow(self)

    async def sync_state_with_temporal(
        self, langgraph_state: ResearchState, temporal_workflow_id: str
    ) -> ResearchState:
        """
        Synchronize LangGraph state with Temporal workflow state.

        Args:
            langgraph_state: Current LangGraph state
            temporal_workflow_id: Temporal workflow ID

        Returns:
            Synchronized state
        """
        if not self.temporal_client:
            self.temporal_client = await get_temporal_client()

        # Query Temporal workflow state
        handle = self.temporal_client.get_workflow_handle(temporal_workflow_id)

        try:
            # Query workflow progress
            progress = await handle.query("get_progress")

            # Update LangGraph state with Temporal progress
            langgraph_state.context["temporal_progress"] = progress

            # Sync completed tasks
            if progress.get("completed_tasks"):
                for task in progress["completed_tasks"]:
                    if task in ["literature_review", "synthesis", "report_generation"]:
                        langgraph_state.completed_agents.add(task)

        except Exception as e:
            logger.warning(f"Failed to sync with Temporal: {e}")

        return langgraph_state


@workflow.defn
class LangGraphTemporalWorkflow:
    """
    Temporal workflow that wraps LangGraph orchestration.

    This allows Temporal to manage the overall workflow lifecycle
    while leveraging LangGraph for intelligent agent coordination.
    """

    @workflow.run
    async def run(self, project_data: dict[str, Any]) -> dict[str, Any]:
        """
        Execute LangGraph orchestration within Temporal.

        Args:
            project_data: Project configuration

        Returns:
            Workflow result
        """
        workflow.logger.info(
            f"Starting LangGraph workflow for project: {project_data.get('id')}"
        )

        # Execute LangGraph orchestration as activity
        result = await workflow.execute_activity(
            "langgraph_orchestration_activity",
            project_data,
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=RetryPolicy(
                maximum_attempts=3,
                initial_interval=timedelta(seconds=5),
                maximum_interval=timedelta(seconds=60),
                backoff_coefficient=2,
            ),
        )

        workflow.logger.info("LangGraph orchestration complete")

        return cast(dict[str, Any], result)


@activity.defn(name="langgraph_orchestration_activity")
async def langgraph_orchestration_activity(
    project_data: dict[str, Any],
) -> dict[str, Any]:
    """
    Execute LangGraph orchestration as a Temporal activity.

    Args:
        project_data: Project configuration

    Returns:
        Orchestration result
    """
    orchestrator = ResearchOrchestrator()

    # Execute LangGraph workflow
    result = await orchestrator.execute(
        project_id=project_data["id"],
        query=project_data["query"],
        domains=project_data.get("domains", []),
        context=project_data.get("context", {}),
    )

    # Convert result to dictionary
    return {
        "success": result.success,
        "workflow_id": result.workflow_id,
        "project_id": result.project_id,
        "quality_score": result.quality_score,
        "execution_time": result.execution_time,
        "report": result.report,
        "outputs": result.outputs,
        "errors": result.errors,
    }


class HybridWorkflow:
    """
    Hybrid workflow that intelligently combines LangGraph and Temporal.

    Uses LangGraph for complex decision-making and agent coordination,
    while leveraging Temporal for reliability and fault tolerance.
    """

    def __init__(self, bridge: TemporalBridge):
        """
        Initialize hybrid workflow.

        Args:
            bridge: Temporal bridge instance
        """
        self.bridge = bridge
        self.execution_mode = "hybrid"  # hybrid, langgraph_primary, temporal_primary

    async def execute(
        self,
        project_id: str,
        query: str,
        domains: list[str],
        mode: str | None = None,
    ) -> WorkflowResult:
        """
        Execute hybrid workflow.

        Args:
            project_id: Project identifier
            query: Research query
            domains: Research domains
            mode: Execution mode override

        Returns:
            Workflow execution result
        """
        execution_mode = mode or self.execution_mode

        if execution_mode == "langgraph_primary":
            # Use LangGraph as primary with Temporal for specific tasks
            return await self._execute_langgraph_primary(project_id, query, domains)

        elif execution_mode == "temporal_primary":
            # Use Temporal as primary with LangGraph for coordination
            return await self._execute_temporal_primary(project_id, query, domains)

        else:  # hybrid mode
            # Intelligently choose based on requirements
            return await self._execute_hybrid(project_id, query, domains)

    async def _execute_langgraph_primary(
        self, project_id: str, query: str, domains: list[str]
    ) -> WorkflowResult:
        """Execute with LangGraph as primary orchestrator."""
        logger.info("Executing with LangGraph as primary")

        # Create custom orchestrator that uses Temporal for critical tasks
        config = OrchestratorConfig(
            enable_checkpointing=True,
            checkpoint_storage="file",
            enable_parallel_execution=True,
        )

        orchestrator = ResearchOrchestrator(config)

        # Add Temporal integration to context
        context = {
            "use_temporal_for": ["literature_review", "report_generation"],
            "temporal_client": self.bridge.temporal_client,
        }

        # Execute workflow
        result = await orchestrator.execute(
            project_id=project_id, query=query, domains=domains, context=context
        )

        return result

    async def _execute_temporal_primary(
        self, project_id: str, query: str, domains: list[str]
    ) -> WorkflowResult:
        """Execute with Temporal as primary orchestrator."""
        logger.info("Executing with Temporal as primary")

        # Prepare project data
        project_data = {
            "id": project_id,
            "title": query,
            "query": query,
            "domains": domains,
            "use_langgraph_for": ["agent_coordination", "conflict_resolution"],
        }

        # Run through Temporal with LangGraph integration
        result = await self.bridge.run_langgraph_in_temporal(project_data)

        # Convert to WorkflowResult
        return WorkflowResult(
            success=result.get("success", False),
            workflow_id=result.get("workflow_id", ""),
            project_id=project_id,
            final_state=ResearchState(
                workflow_id=result.get("workflow_id", ""),
                project_id=project_id,
                query="",
                domains=[],
            ),
            report=result.get("report"),
            outputs=result.get("outputs"),
            quality_score=result.get("quality_score", 0.0),
            execution_time=result.get("execution_time", 0.0),
            errors=result.get("errors", []),
        )

    async def _execute_hybrid(
        self, project_id: str, query: str, domains: list[str]
    ) -> WorkflowResult:
        """Execute with intelligent hybrid approach."""
        logger.info("Executing in hybrid mode")

        # Analyze requirements to determine best approach
        complexity = self._assess_complexity(query, domains)

        if complexity == "high":
            # Complex queries benefit from LangGraph's intelligence
            logger.info("High complexity detected, using LangGraph primary")
            return await self._execute_langgraph_primary(project_id, query, domains)

        elif complexity == "critical":
            # Critical queries need Temporal's reliability
            logger.info("Critical query detected, using Temporal primary")
            return await self._execute_temporal_primary(project_id, query, domains)

        else:
            # Standard queries use balanced approach
            logger.info("Standard query, using balanced hybrid approach")

            # Start both systems in parallel
            langgraph_task = asyncio.create_task(
                self._execute_langgraph_primary(project_id, query, domains)
            )

            temporal_task = asyncio.create_task(
                self._execute_temporal_primary(project_id, query, domains)
            )

            # Wait for first successful completion
            done, pending = await asyncio.wait(
                [langgraph_task, temporal_task], return_when=asyncio.FIRST_COMPLETED
            )

            # Cancel pending task
            for task in pending:
                task.cancel()

            # Return first successful result
            for task in done:
                try:
                    return await task
                except Exception as e:
                    logger.error(f"Task failed: {e}")

            # If both failed, return error
            return WorkflowResult(
                success=False,
                workflow_id=f"hybrid-{project_id}",
                project_id=project_id,
                final_state=ResearchState(
                    workflow_id=f"hybrid-{project_id}",
                    project_id=project_id,
                    query=query,
                    domains=domains,
                ),
                errors=["Both orchestration approaches failed"],
            )

    def _assess_complexity(self, query: str, domains: list[str]) -> str:
        """
        Assess query complexity to determine execution strategy.

        Args:
            query: Research query
            domains: Research domains

        Returns:
            Complexity level
        """
        # Simple heuristic - in production would use more sophisticated analysis
        query_length = len(query.split())
        domain_count = len(domains)

        if "critical" in query.lower() or "urgent" in query.lower():
            return "critical"

        if query_length > 50 or domain_count > 3:
            return "high"

        return "standard"


__all__ = [
    "HybridWorkflow",
    "LangGraphTemporalWorkflow",
    "TemporalBridge",
    "langgraph_orchestration_activity",
]
