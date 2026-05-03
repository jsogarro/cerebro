"""
Main research orchestrator using LangGraph.

This module provides the main orchestrator that builds and executes
the research workflow graph, coordinating all agents and managing
the overall research process.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, cast

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.agents.supervisors.supervisor_factory import SupervisorFactory
from src.ai_brain.config.supervisor_config import SupervisorConfigurationManager
from src.ai_brain.integration.masr_supervisor_bridge import MASRSupervisorBridge
from src.ai_brain.router.hierarchical_cost_model import HierarchicalCostOptimizer

# MASR Integration imports
from src.ai_brain.router.masr import MASRouter
from src.orchestration.checkpointer import (
    FileCheckpointStorage,
    MemoryCheckpointStorage,
    WorkflowCheckpointer,
)
from src.orchestration.edges import RouterConfig, WorkflowRouter
from src.orchestration.graph_builder import (
    GraphConfig,
    ResearchGraphBuilder,
)
from src.orchestration.nodes import (
    agent_dispatch_node,
    plan_generation_node,
    quality_check_node,
    query_analysis_node,
    report_generation_node,
    result_aggregation_node,
)
from src.orchestration.state import (
    AgentExecutionStatus,
    ResearchState,
    WorkflowMetadata,
    WorkflowPhase,
)

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorConfig:
    """Configuration for the research orchestrator."""

    enable_checkpointing: bool = True
    checkpoint_storage: str = "memory"  # memory, file, or redis
    checkpoint_path: str | None = None
    enable_parallel_execution: bool = True
    max_parallel_agents: int = 3
    enable_human_in_loop: bool = False
    quality_threshold: float = 0.7
    max_workflow_errors: int = 3
    max_iterations: int = 10
    timeout_seconds: int = 1800  # 30 minutes
    enable_monitoring: bool = True
    enable_visualization: bool = True
    
    # MASR Integration configuration
    enable_masr_routing: bool = True
    masr_config: dict[str, Any] | None = None
    enable_hierarchical_costs: bool = True
    supervisor_bridge_config: dict[str, Any] | None = None
    enable_cost_feedback: bool = True


@dataclass
class WorkflowResult:
    """Result of workflow execution."""

    success: bool
    workflow_id: str
    project_id: str
    final_state: ResearchState
    report: dict[str, Any] | None = None
    outputs: dict[str, str] | None = None
    quality_score: float = 0.0
    execution_time: float = 0.0
    errors: list[str] | None = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


class ResearchOrchestrator:
    """
    Main orchestrator for research workflows.

    Builds and executes the LangGraph workflow for multi-agent research.
    """

    def __init__(self, config: OrchestratorConfig | None = None):
        """
        Initialize research orchestrator.

        Args:
            config: Orchestrator configuration
        """
        self.config = config or OrchestratorConfig()
        self._graph: StateGraph[ResearchState] | None = None
        self._compiled_graph: CompiledStateGraph[ResearchState] | None = None
        self._checkpointer: WorkflowCheckpointer | None = None
        self._router: WorkflowRouter | None = None

        # MASR Integration components
        self._masr_router: MASRouter | None = None
        self._supervisor_bridge: MASRSupervisorBridge | None = None
        self._supervisor_factory: SupervisorFactory | None = None
        self._configuration_manager: SupervisorConfigurationManager | None = None
        self._hierarchical_cost_optimizer: HierarchicalCostOptimizer | None = None

        # Initialize components
        self._initialize_components()

    def _initialize_components(self) -> None:
        """Initialize orchestrator components."""
        # Initialize router
        router_config = RouterConfig(
            enable_parallel_execution=self.config.enable_parallel_execution,
            max_parallel_agents=self.config.max_parallel_agents,
            quality_threshold=self.config.quality_threshold,
            enable_human_in_loop=self.config.enable_human_in_loop,
        )
        self._router = WorkflowRouter(router_config)

        # Initialize checkpointer
        if self.config.enable_checkpointing:
            storage = self._create_checkpoint_storage()
            self._checkpointer = WorkflowCheckpointer(storage)

        # Initialize MASR integration components
        if self.config.enable_masr_routing:
            self._initialize_masr_components()

    def _initialize_masr_components(self) -> None:
        """Initialize MASR integration components."""
        logger.info("Initializing MASR integration components")
        
        # Initialize MASR router
        masr_config = self.config.masr_config or {}
        self._masr_router = MASRouter(
            config=masr_config,
            model_config_manager=None  # Would be injected in production
        )
        
        # Initialize supervisor factory
        self._supervisor_factory = SupervisorFactory(
            config=masr_config.get("supervisor_factory", {})
        )
        
        # Initialize configuration manager
        self._configuration_manager = SupervisorConfigurationManager(
            config=masr_config.get("configuration_manager", {})
        )
        
        # Initialize hierarchical cost optimizer if enabled
        if self.config.enable_hierarchical_costs:
            from src.ai_brain.router.cost_optimizer import CostOptimizer
            
            # Create base cost optimizer (simplified initialization)
            base_optimizer = CostOptimizer(
                config=masr_config.get("cost_optimizer", {}),
                model_config_manager=None
            )
            
            self._hierarchical_cost_optimizer = HierarchicalCostOptimizer(
                base_cost_optimizer=base_optimizer,
                config=masr_config.get("hierarchical_cost", {})
            )
        
        # Initialize MASR-Supervisor bridge
        bridge_config = self.config.supervisor_bridge_config or {}
        self._supervisor_bridge = MASRSupervisorBridge(config=bridge_config)
        
        logger.info("MASR integration components initialized successfully")

    def _create_checkpoint_storage(
        self,
    ) -> FileCheckpointStorage | MemoryCheckpointStorage:
        """Create checkpoint storage based on configuration."""
        if self.config.checkpoint_storage == "file":
            return FileCheckpointStorage(
                self.config.checkpoint_path or "/tmp/langgraph_checkpoints"
            )
        elif self.config.checkpoint_storage == "redis":
            # Would need Redis client configuration
            return MemoryCheckpointStorage()  # Fallback for now
        else:
            return MemoryCheckpointStorage()

    def build_graph(self) -> StateGraph[ResearchState]:
        """
        Build the research workflow graph.

        Returns:
            Constructed workflow graph
        """
        logger.info("Building research workflow graph")

        assert self._router is not None
        # Create graph builder
        graph_config = GraphConfig(
            name="research_workflow",
            enable_checkpointing=self.config.enable_checkpointing,
            enable_parallel_execution=self.config.enable_parallel_execution,
            max_parallel_nodes=self.config.max_parallel_agents,
            max_iterations=self.config.max_iterations,
            enable_visualization=self.config.enable_visualization,
            router_config=self._router.config,
        )

        builder = ResearchGraphBuilder(graph_config)

        # Add workflow nodes
        builder.add_node(
            "initialization",
            cast(Callable[[ResearchState], ResearchState], self._initialization_node),
            WorkflowPhase.INITIALIZATION,
            description="Initialize workflow state",
        )

        builder.add_node(
            "query_analysis",
            cast(Callable[[ResearchState], ResearchState], query_analysis_node),
            WorkflowPhase.QUERY_ANALYSIS,
            description="Analyze research query",
        )

        builder.add_node(
            "plan_generation",
            cast(Callable[[ResearchState], ResearchState], plan_generation_node),
            WorkflowPhase.PLAN_GENERATION,
            description="Generate research plan",
        )

        builder.add_node(
            "agent_dispatch",
            cast(Callable[[ResearchState], ResearchState], agent_dispatch_node),
            WorkflowPhase.LITERATURE_REVIEW,  # Will change based on agents
            description="Dispatch tasks to agents",
        )

        builder.add_node(
            "result_aggregation",
            cast(Callable[[ResearchState], ResearchState], result_aggregation_node),
            WorkflowPhase.SYNTHESIS,
            description="Aggregate agent results",
        )

        builder.add_node(
            "quality_check",
            cast(Callable[[ResearchState], ResearchState], quality_check_node),
            WorkflowPhase.QUALITY_CHECK,
            description="Check research quality",
        )

        builder.add_node(
            "report_generation",
            cast(Callable[[ResearchState], ResearchState], report_generation_node),
            WorkflowPhase.REPORT_GENERATION,
            description="Generate final report",
        )

        builder.add_node(
            "error_handler",
            cast(Callable[[ResearchState], ResearchState], self._error_handler_node),
            WorkflowPhase.FAILED,
            description="Handle workflow errors",
        )

        # Add edges with routing logic
        builder.add_edge("initialization", "query_analysis")

        builder.add_conditional_edges(
            "query_analysis",
            self._route_from_query_analysis,
            {"plan_generation": "plan_generation", "error": "error_handler"},
        )

        builder.add_conditional_edges(
            "plan_generation",
            self._route_from_plan_generation,
            {"agent_dispatch": "agent_dispatch", "error": "error_handler"},
        )

        builder.add_conditional_edges(
            "agent_dispatch",
            self._route_from_agent_dispatch,
            {
                "more_agents": "agent_dispatch",
                "aggregation": "result_aggregation",
                "error": "error_handler",
            },
        )

        builder.add_edge("result_aggregation", "quality_check")

        builder.add_conditional_edges(
            "quality_check",
            self._route_from_quality_check,
            {
                "report": "report_generation",
                "improve": "agent_dispatch",
                "error": "error_handler",
            },
        )

        builder.add_conditional_edges(
            "report_generation",
            lambda state: END if state.is_terminal() else "quality_check",
            {},
        )

        builder.add_edge("error_handler", END)

        # Build and store graph
        self._graph = builder.build()

        # Generate visualization if enabled
        if self.config.enable_visualization:
            visualization = builder.visualize()
            logger.debug(f"Graph visualization:\n{visualization}")

        return self._graph

    def compile_graph(self) -> CompiledStateGraph[ResearchState]:
        """Compile the workflow graph."""
        if not self._graph:
            self.build_graph()

        assert self._graph is not None
        # Compile with checkpointing if enabled
        if self.config.enable_checkpointing:
            from langgraph.checkpoint.memory import MemorySaver

            checkpointer = MemorySaver()
            self._compiled_graph = self._graph.compile(checkpointer=checkpointer)
        else:
            self._compiled_graph = self._graph.compile()

        logger.info("Research workflow graph compiled")

        return self._compiled_graph

    async def execute(
        self,
        project_id: str,
        query: str,
        domains: list[str],
        context: dict[str, Any] | None = None,
        resume_from: str | None = None,
    ) -> WorkflowResult:
        """
        Execute the research workflow.

        Args:
            project_id: Project identifier
            query: Research query
            domains: Research domains
            context: Additional context
            resume_from: Checkpoint ID to resume from

        Returns:
            Workflow execution result
        """
        logger.info(f"Starting research workflow for project {project_id}")

        start_time = datetime.now(UTC)
        workflow_id = f"workflow-{uuid.uuid4().hex[:8]}"

        try:
            # Create initial state
            initial_state = ResearchState(
                project_id=project_id,
                workflow_id=workflow_id,
                query=query,
                domains=domains,
                context=context or {},
                max_errors=self.config.max_workflow_errors,
                max_iterations=self.config.max_iterations,
                metadata=WorkflowMetadata(
                    workflow_id=workflow_id,
                    started_at=start_time,
                    updated_at=start_time,
                ),
            )

            # Resume from checkpoint if specified
            if resume_from and self._checkpointer:
                success = await self._checkpointer.restore_checkpoint(
                    resume_from, initial_state
                )
                if success:
                    logger.info(f"Resumed from checkpoint {resume_from}")

            # Compile graph if needed
            if not self._compiled_graph:
                self.compile_graph()

            # Execute workflow with timeout
            final_state = await asyncio.wait_for(
                self._run_workflow(initial_state), timeout=self.config.timeout_seconds
            )

            # Calculate execution time
            execution_time = (datetime.now(UTC) - start_time).total_seconds()

            # Create result
            result = WorkflowResult(
                success=final_state.current_phase == WorkflowPhase.COMPLETED,
                workflow_id=workflow_id,
                project_id=project_id,
                final_state=final_state,
                report=final_state.context.get("final_report"),
                outputs=final_state.context.get("report_outputs"),
                quality_score=final_state.quality_score,
                execution_time=execution_time,
                errors=final_state.validation_errors,
            )

            # Clean up checkpoints if successful
            if result.success and self._checkpointer:
                await self._checkpointer.cleanup_workflow(workflow_id)

            logger.info(
                f"Workflow completed. Success: {result.success}, Quality: {result.quality_score:.2f}"
            )

            return result

        except TimeoutError:
            logger.error(
                f"Workflow timed out after {self.config.timeout_seconds} seconds"
            )
            return WorkflowResult(
                success=False,
                workflow_id=workflow_id,
                project_id=project_id,
                final_state=initial_state,
                errors=[
                    f"Workflow timed out after {self.config.timeout_seconds} seconds"
                ],
            )

        except Exception as e:
            logger.error(f"Workflow execution failed: {e}")
            return WorkflowResult(
                success=False,
                workflow_id=workflow_id,
                project_id=project_id,
                final_state=initial_state,
                errors=[str(e)],
            )

    async def _run_workflow(self, initial_state: ResearchState) -> ResearchState:
        """
        Run the compiled workflow.

        Args:
            initial_state: Initial workflow state

        Returns:
            Final workflow state
        """
        assert self._compiled_graph is not None
        # Execute the graph
        async for output in self._compiled_graph.astream(initial_state):
            # The output is the state after each node execution
            if isinstance(output, dict):
                for node_name, node_state_val in output.items():
                    node_state = cast(ResearchState, node_state_val)
                    logger.info(f"Executed node: {node_name}")

                    # Check if we should create a checkpoint
                    if self._checkpointer and node_state.should_checkpoint():
                        checkpoint_id = await self._checkpointer.create_checkpoint(
                            node_state
                        )
                        logger.info(f"Created checkpoint: {checkpoint_id}")

                    # Check for terminal state
                    if node_state.is_terminal():
                        return node_state

        # Return the final state
        return initial_state

    async def _initialization_node(self, state: ResearchState) -> ResearchState:
        """Initialize workflow state."""
        logger.info("Initializing workflow")

        # Set initial context values
        state.context.update(
            {
                "enable_parallel": self.config.enable_parallel_execution,
                "max_parallel_agents": self.config.max_parallel_agents,
                "quality_threshold": self.config.quality_threshold,
                "workflow_config": {
                    "checkpointing": self.config.enable_checkpointing,
                    "monitoring": self.config.enable_monitoring,
                    "human_in_loop": self.config.enable_human_in_loop,
                },
            }
        )

        return state

    async def _error_handler_node(self, state: ResearchState) -> ResearchState:
        """Handle workflow errors."""
        logger.error(f"Workflow failed with {state.error_count} errors")

        # Log all errors
        for error in state.error_history:
            logger.error(
                f"Error in {error.get('node', 'unknown')}: {error.get('error', 'unknown')}"
            )

        # Mark workflow as failed
        state.transition_to_phase(WorkflowPhase.FAILED)

        # Create error report
        state.context["error_report"] = {
            "total_errors": state.error_count,
            "error_history": state.error_history,
            "validation_errors": state.validation_errors,
            "failed_agents": list(state.failed_agents),
        }

        return state

    def _route_from_query_analysis(self, state: ResearchState) -> str:
        """Route from query analysis node."""
        if state.query and state.domains:
            return "plan_generation"
        return "error"

    def _route_from_plan_generation(self, state: ResearchState) -> str:
        """Route from plan generation node."""
        if state.research_plan and state.agent_tasks:
            return "agent_dispatch"
        return "error"

    def _route_from_agent_dispatch(self, state: ResearchState) -> str:
        """Route from agent dispatch node."""
        # Check if there are more agents to execute
        pending_ready = [
            task
            for task in state.agent_tasks.values()
            if task.status == AgentExecutionStatus.PENDING
        ]

        if pending_ready and state.research_plan is not None:
            # Check dependencies
            dependencies = state.research_plan.get("dependencies", {})
            for task in pending_ready:
                deps = dependencies.get(task.agent_type, [])
                if all(dep in state.completed_agents for dep in deps):
                    return "more_agents"

        # All agents executed or blocked
        if state.completed_agents:
            return "aggregation"

        return "error"

    def _route_from_quality_check(self, state: ResearchState) -> str:
        """Route from quality check node."""
        quality_report = state.context.get("quality_report", {})

        if quality_report.get("passed", False):
            return "report"
        elif state.error_count < state.max_errors:
            # Try to improve quality
            return "improve"
        else:
            return "error"

    async def resume_workflow(
        self, checkpoint_id: str, updates: dict[str, Any] | None = None
    ) -> WorkflowResult:
        """
        Resume a workflow from a checkpoint.

        Args:
            checkpoint_id: Checkpoint to resume from
            updates: Optional state updates

        Returns:
            Workflow execution result
        """
        if not self._checkpointer:
            raise RuntimeError("Checkpointing not enabled")

        # Load checkpoint
        checkpoint = await self._checkpointer.storage.load(checkpoint_id)

        if not checkpoint:
            raise ValueError(f"Checkpoint {checkpoint_id} not found")

        # Create state from checkpoint
        state = ResearchState(
            project_id=checkpoint.state_data["project_id"],
            workflow_id=checkpoint.checkpoint_id.split("-")[0],
            query=checkpoint.state_data["query"],
            domains=checkpoint.state_data["domains"],
            max_iterations=self.config.max_iterations,
        )

        # Restore from checkpoint
        state.restore_from_checkpoint(checkpoint)

        # Apply updates if provided
        if updates:
            state.context.update(updates)

        # Resume execution
        return await self.execute(
            project_id=state.project_id,
            query=state.query,
            domains=state.domains,
            context=state.context,
            resume_from=checkpoint_id,
        )

    async def get_workflow_status(self, workflow_id: str) -> dict[str, Any]:
        """
        Get status of a workflow.

        Args:
            workflow_id: Workflow identifier

        Returns:
            Workflow status information
        """
        if not self._checkpointer:
            return {"error": "Checkpointing not enabled"}

        # Get latest checkpoint
        checkpoint = await self._checkpointer.get_latest_checkpoint(workflow_id)

        if not checkpoint:
            return {"error": f"No checkpoints found for workflow {workflow_id}"}

        return {
            "workflow_id": workflow_id,
            "current_phase": checkpoint.phase.value,
            "timestamp": checkpoint.timestamp.isoformat(),
            "completed_agents": checkpoint.metadata.get("completed_agents", []),
            "failed_agents": checkpoint.metadata.get("failed_agents", []),
            "pending_agents": checkpoint.metadata.get("pending_agents", []),
            "quality_score": checkpoint.state_data.get("quality_score", 0),
            "error_count": checkpoint.state_data.get("error_count", 0),
        }
    
    async def _masr_enabled_agent_dispatch(
        self, state: ResearchState
    ) -> ResearchState:
        """
        MASR-enabled agent dispatch node that uses intelligent routing.

        This method replaces the traditional agent dispatch with MASR routing
        decisions that automatically select optimal supervisors and coordinate
        hierarchical execution.
        """
        if not self.config.enable_masr_routing or not self._masr_router:
            # Fallback to traditional agent dispatch
            logger.warning(
                "MASR routing not available, falling back to traditional dispatch"
            )
            return await agent_dispatch_node(state)

        try:
            # Prepare context for MASR routing
            routing_context = {
                "query": state.query,
                "domains": state.domains,
                "project_id": state.project_id,
                "workflow_id": state.workflow_id,
                "user_id": state.context.get("user_id"),
                "session_id": state.context.get("session_id"),
            }

            # Get MASR routing decision
            logger.info(
                f"Getting MASR routing decision for query: {state.query[:100]}..."
            )
            routing_decision = await self._masr_router.route(
                query=state.query, context=routing_context
            )

            # Record routing decision in state
            state.context["masr_routing_decision"] = asdict(routing_decision)

            # Create agent task for supervisor execution
            from src.agents.models import AgentTask

            agent_task = AgentTask(
                id=f"research-{state.workflow_id}",
                agent_type="research",
                input_data={
                    "query": state.query,
                    "domains": state.domains,
                    "context": state.context,
                    "complexity_analysis": routing_decision.complexity_analysis.__dict__,
                    "routing_decision": routing_decision.__dict__,
                },
            )

            assert self._supervisor_factory is not None
            assert self._supervisor_bridge is not None
            # Get supervisor registry (in production, this would be injected)
            supervisor_registry = {
                "research": self._supervisor_factory.supervisor_registry[
                    "research"
                ].supervisor_class
            }

            # Execute via MASR-Supervisor bridge
            logger.info(
                f"Executing task via {routing_decision.agent_allocation.supervisor_type} supervisor"
            )
            execution_result = await self._supervisor_bridge.execute_routing_decision(
                routing_decision=routing_decision,
                task=agent_task,
                supervisor_registry=supervisor_registry,
            )

            # Record execution result in state
            state.context["supervisor_execution_result"] = {
                "execution_id": execution_result.execution_id,
                "supervisor_type": execution_result.supervisor_type,
                "status": execution_result.status.value,
                "quality_score": execution_result.quality_score,
                "consensus_score": execution_result.consensus_score,
                "execution_time_seconds": execution_result.execution_time_seconds,
                "workers_used": execution_result.workers_used,
                "refinement_rounds": execution_result.refinement_rounds,
            }

            # Update research state with results
            if execution_result.agent_result:
                state.agent_results["supervisor_research"] = execution_result.agent_result
                state.quality_score = max(
                    state.quality_score, execution_result.quality_score
                )

            # Record cost feedback if enabled
            if self.config.enable_cost_feedback and self._supervisor_factory:
                self._supervisor_factory.record_execution_result(
                    execution_result.supervisor_type,
                    execution_result.status.value == "completed",
                    int(execution_result.execution_time_seconds * 1000),
                )

            # Set next phase based on result
            if execution_result.status.value == "completed":
                state.current_phase = WorkflowPhase.SYNTHESIS
                logger.info("MASR-supervised execution completed successfully")
            else:
                state.error_count += 1
                state.error_history.append(
                    {
                        "node": "agent_dispatch",
                        "error": f"Supervisor execution failed: {execution_result.errors}",
                        "phase": WorkflowPhase.LITERATURE_REVIEW.value,
                    }
                )
                logger.error(
                    f"MASR-supervised execution failed: {execution_result.errors}"
                )

            return state

        except Exception as e:
            logger.error(f"MASR-enabled agent dispatch failed: {e}")
            state.error_count += 1
            state.error_history.append(
                {
                    "node": "agent_dispatch",
                    "error": f"MASR routing failed: {e!s}",
                    "phase": WorkflowPhase.LITERATURE_REVIEW.value,
                }
            )
            
            # Fallback to traditional agent dispatch
            logger.info("Falling back to traditional agent dispatch")
            return await agent_dispatch_node(state)
    
    async def get_masr_stats(self) -> dict[str, Any]:
        """Get MASR integration statistics."""
        if not self.config.enable_masr_routing:
            return {"masr_enabled": False}

        stats: dict[str, Any] = {"masr_enabled": True}

        if self._masr_router:
            stats["masr_router"] = await self._masr_router.get_metrics()

        if self._supervisor_bridge:
            stats["supervisor_bridge"] = await self._supervisor_bridge.get_bridge_stats()

        if self._supervisor_factory:
            stats["supervisor_factory"] = await self._supervisor_factory.get_factory_stats()

        if self._hierarchical_cost_optimizer:
            stats[
                "cost_optimizer"
            ] = await self._hierarchical_cost_optimizer.get_cost_model_stats()

        return stats
    
    async def health_check(self) -> dict[str, Any]:
        """Perform comprehensive health check including MASR components."""
        health: dict[str, Any] = {
            "status": "healthy",
            "components": {
                "workflow_router": "healthy",
                "graph_builder": "healthy",
            },
        }

        if self.config.enable_checkpointing and self._checkpointer:
            health["components"]["checkpointer"] = "healthy"

        if self.config.enable_masr_routing:
            masr_health: dict[str, str] = {
                "masr_router": "healthy" if self._masr_router else "unavailable",
                "supervisor_bridge": "healthy" if self._supervisor_bridge else "unavailable",
                "supervisor_factory": "healthy" if self._supervisor_factory else "unavailable",
            }

            if self._hierarchical_cost_optimizer:
                masr_health["cost_optimizer"] = "healthy"

            health["components"]["masr_integration"] = masr_health

        return health


__all__ = [
    "OrchestratorConfig",
    "ResearchOrchestrator",
    "WorkflowResult",
]
