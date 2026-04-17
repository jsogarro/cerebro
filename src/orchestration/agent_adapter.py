"""
Adapter for integrating agents with LangGraph orchestration.

This module provides adapters and utilities for seamlessly integrating
the existing agent system with the LangGraph orchestration framework.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

from src.agents.base import BaseAgent
from src.agents.factory import AgentFactory
from src.agents.models import AgentResult, AgentTask
from src.mcp.client import MCPClient
from src.orchestration.state import (
    AgentExecutionStatus,
    AgentTaskState,
    ResearchState,
    WorkflowPhase,
)
from src.services.gemini_service import GeminiService

logger = logging.getLogger(__name__)


class AgentAdapter:
    """
    Adapter for integrating agents with LangGraph orchestration.

    Provides translation between LangGraph state and agent interfaces,
    manages agent lifecycle, and handles MCP tool integration.
    """

    def __init__(
        self,
        agent_factory: AgentFactory | None = None,
        mcp_client: MCPClient | None = None,
        gemini_service: GeminiService | None = None,
    ):
        """
        Initialize agent adapter.

        Args:
            agent_factory: Factory for creating agents
            mcp_client: MCP client for tool access
            gemini_service: Gemini service for AI capabilities
        """
        self.agent_factory = agent_factory or AgentFactory()
        self.mcp_client = mcp_client
        self.gemini_service = gemini_service
        self._agent_instances: dict[str, BaseAgent] = {}
        self._agent_metrics: dict[str, dict[str, Any]] = {}

    async def execute_agent(
        self, agent_task_state: AgentTaskState, research_state: ResearchState
    ) -> AgentResult:
        """
        Execute an agent task within LangGraph context.

        Args:
            agent_task_state: Agent task state from LangGraph
            research_state: Current research state

        Returns:
            Agent execution result
        """
        logger.info(f"Executing agent: {agent_task_state.agent_type}")

        start_time = datetime.utcnow()

        try:
            # Get or create agent instance
            agent = await self._get_or_create_agent(agent_task_state.agent_type)

            # Prepare agent task with LangGraph context
            agent_task = self._create_agent_task(agent_task_state, research_state)

            # Inject services if available
            if self.mcp_client:
                agent_task.context["mcp_client"] = self.mcp_client
            if self.gemini_service:
                agent_task.context["gemini_service"] = self.gemini_service

            # Execute agent
            result = await agent.execute(agent_task)

            # Validate result
            is_valid = await agent.validate_result(result)

            if not is_valid:
                logger.warning(
                    f"Agent {agent_task_state.agent_type} result validation failed"
                )
                result = AgentResult(
                    task_id=result.task_id,
                    status="validation_failed",
                    output=result.output,
                    confidence=result.confidence,
                    execution_time=result.execution_time,
                    metadata=result.metadata,
                )

            # Update metrics
            self._update_agent_metrics(
                agent_task_state.agent_type, start_time, result.status == "success"
            )

            return result

        except Exception as e:
            logger.error(f"Agent execution failed: {e}")

            # Create error result
            return AgentResult(
                task_id=agent_task_state.task_id,
                status="error",
                output={},
                confidence=0.0,
                execution_time=(datetime.utcnow() - start_time).total_seconds(),
                metadata={
                    "error": str(e),
                    "error_time": datetime.utcnow().isoformat(),
                    "retry_count": agent_task_state.retry_count,
                },
            )

    async def execute_agents_parallel(
        self,
        agent_tasks: list[AgentTaskState],
        research_state: ResearchState,
        max_parallel: int = 3,
    ) -> dict[str, AgentResult]:
        """
        Execute multiple agents in parallel.

        Args:
            agent_tasks: List of agent tasks to execute
            research_state: Current research state
            max_parallel: Maximum parallel executions

        Returns:
            Dictionary of results by agent type
        """
        logger.info(
            f"Executing {len(agent_tasks)} agents in parallel (max {max_parallel})"
        )

        results = {}
        semaphore = asyncio.Semaphore(max_parallel)

        async def execute_with_semaphore(task: AgentTaskState) -> AgentResult:
            async with semaphore:
                return await self.execute_agent(task, research_state)

        # Create tasks for parallel execution
        tasks = [execute_with_semaphore(agent_task) for agent_task in agent_tasks]

        # Execute and collect results
        agent_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        for agent_task, result in zip(agent_tasks, agent_results, strict=True):
            if isinstance(result, Exception):
                logger.error(
                    f"Agent {agent_task.agent_type} failed with exception: {result}"
                )
                results[agent_task.agent_type] = AgentResult(
                    task_id=agent_task.task_id,
                    status="error",
                    output={},
                    confidence=0.0,
                    execution_time=0.0,
                    metadata={"error": str(result)},
                )
            elif isinstance(result, AgentResult):
                results[agent_task.agent_type] = result

        return results

    async def _get_or_create_agent(self, agent_type: str) -> BaseAgent:
        """
        Get existing agent instance or create new one.

        Args:
            agent_type: Type of agent

        Returns:
            Agent instance
        """
        if agent_type not in self._agent_instances:
            agent = self.agent_factory.create_agent(agent_type)

            # Initialize agent with services if available
            if hasattr(agent, "set_mcp_integration") and self.mcp_client:
                from src.mcp.integration import MCPIntegration

                mcp_integration = MCPIntegration(self.mcp_client)
                agent.set_mcp_integration(mcp_integration)

            self._agent_instances[agent_type] = agent

        return self._agent_instances[agent_type]

    def _create_agent_task(
        self, agent_task_state: AgentTaskState, research_state: ResearchState
    ) -> AgentTask:
        """
        Create agent task from LangGraph state.

        Args:
            agent_task_state: Agent task state
            research_state: Research state

        Returns:
            Agent task for execution
        """
        # Prepare input data with context from research state
        input_data = {
            **agent_task_state.input_data,
            "query": research_state.query,
            "domains": research_state.domains,
            "research_plan": research_state.research_plan,
        }

        # Add previous agent results for context
        if research_state.agent_results:
            input_data["previous_results"] = {
                agent_type: result.to_dict() if hasattr(result, "to_dict") else result
                for agent_type, result in research_state.agent_results.items()
            }

        # Add aggregated results if available
        if research_state.context.get("aggregated_results"):
            input_data["aggregated_results"] = research_state.context[
                "aggregated_results"
            ]

        # Create agent task
        return AgentTask(
            id=agent_task_state.task_id,
            agent_type=agent_task_state.agent_type,
            input_data=input_data,
            context={
                **research_state.context,
                "workflow_id": research_state.workflow_id,
                "project_id": research_state.project_id,
                "current_phase": research_state.current_phase.value,
                "quality_threshold": research_state.context.get(
                    "quality_threshold", 0.7
                ),
                "retry_count": agent_task_state.retry_count,
            },
            timeout=agent_task_state.input_data.get("timeout", 300),
        )

    def _update_agent_metrics(
        self, agent_type: str, start_time: datetime, success: bool
    ) -> None:
        """
        Update agent execution metrics.

        Args:
            agent_type: Type of agent
            start_time: Execution start time
            success: Whether execution was successful
        """
        if agent_type not in self._agent_metrics:
            self._agent_metrics[agent_type] = {
                "total_executions": 0,
                "successful_executions": 0,
                "failed_executions": 0,
                "total_execution_time": 0.0,
                "average_execution_time": 0.0,
            }

        metrics = self._agent_metrics[agent_type]
        execution_time = (datetime.utcnow() - start_time).total_seconds()

        metrics["total_executions"] += 1
        if success:
            metrics["successful_executions"] += 1
        else:
            metrics["failed_executions"] += 1

        metrics["total_execution_time"] += execution_time
        metrics["average_execution_time"] = (
            metrics["total_execution_time"] / metrics["total_executions"]
        )

    def get_agent_metrics(self, agent_type: str | None = None) -> dict[str, Any]:
        """
        Get agent execution metrics.

        Args:
            agent_type: Specific agent type or None for all

        Returns:
            Agent metrics
        """
        if agent_type:
            return self._agent_metrics.get(agent_type, {})
        return self._agent_metrics

    async def cleanup(self) -> None:
        """Clean up agent instances and resources."""
        for agent_type, agent in self._agent_instances.items():
            if hasattr(agent, "cleanup"):
                try:
                    await agent.cleanup()
                except Exception as e:
                    logger.error(f"Failed to cleanup agent {agent_type}: {e}")

        self._agent_instances.clear()
        logger.info("Agent adapter cleaned up")


class LangGraphAgentNode:
    """
    Base class for agent nodes in LangGraph workflow.

    Provides a standardized interface for agent execution within
    the LangGraph orchestration framework.
    """

    def __init__(self, agent_type: str, adapter: AgentAdapter, phase: WorkflowPhase):
        """
        Initialize agent node.

        Args:
            agent_type: Type of agent to execute
            adapter: Agent adapter instance
            phase: Workflow phase for this agent
        """
        self.agent_type = agent_type
        self.adapter = adapter
        self.phase = phase

    async def __call__(self, state: ResearchState) -> ResearchState:
        """
        Execute agent node.

        Args:
            state: Current research state

        Returns:
            Updated research state
        """
        logger.info(f"Executing {self.agent_type} agent node")

        # Find task for this agent
        agent_task = None
        for task in state.agent_tasks.values():
            if (
                task.agent_type == self.agent_type
                and task.status == AgentExecutionStatus.PENDING
            ):
                agent_task = task
                break

        if not agent_task:
            logger.warning(f"No pending task found for {self.agent_type}")
            return state

        # Update task status
        agent_task = agent_task.with_status(AgentExecutionStatus.IN_PROGRESS)
        state.agent_tasks[agent_task.task_id] = agent_task

        # Update workflow phase
        state.transition_to_phase(self.phase)

        try:
            # Execute agent
            result = await self.adapter.execute_agent(agent_task, state)

            # Update state based on result
            if result.status == "success":
                state.complete_agent_task(agent_task.task_id, result)
                logger.info(f"{self.agent_type} completed successfully")
            else:
                error_msg = result.metadata.get("error", "Unknown error")
                state.fail_agent_task(
                    agent_task.task_id, error_msg
                )
                logger.error(f"{self.agent_type} failed: {error_msg}")

        except Exception as e:
            logger.error(f"Error executing {self.agent_type}: {e}")
            state.fail_agent_task(agent_task.task_id, str(e))

        return state


def create_agent_nodes(adapter: AgentAdapter) -> dict[str, LangGraphAgentNode]:
    """
    Create LangGraph nodes for all agent types.

    Args:
        adapter: Agent adapter instance

    Returns:
        Dictionary of agent nodes
    """
    agent_configs = {
        "literature_review": WorkflowPhase.LITERATURE_REVIEW,
        "comparative_analysis": WorkflowPhase.COMPARATIVE_ANALYSIS,
        "methodology": WorkflowPhase.METHODOLOGY_DESIGN,
        "synthesis": WorkflowPhase.SYNTHESIS,
        "citation_verification": WorkflowPhase.CITATION_VERIFICATION,
    }

    nodes = {}
    for agent_type, phase in agent_configs.items():
        nodes[f"{agent_type}_node"] = LangGraphAgentNode(
            agent_type=agent_type, adapter=adapter, phase=phase
        )

    return nodes


class MCPToolAdapter:
    """
    Adapter for integrating MCP tools with LangGraph nodes.

    Enables LangGraph nodes to access MCP tools for enhanced capabilities.
    """

    def __init__(self, mcp_client: MCPClient):
        """
        Initialize MCP tool adapter.

        Args:
            mcp_client: MCP client instance
        """
        self.mcp_client = mcp_client
        self._tool_cache: dict[str, Any] = {}

    async def call_tool(
        self, tool_name: str, parameters: dict[str, Any], cache_result: bool = True
    ) -> Any:
        """
        Call an MCP tool.

        Args:
            tool_name: Name of the tool
            parameters: Tool parameters
            cache_result: Whether to cache the result

        Returns:
            Tool execution result
        """
        # Check cache
        cache_key = f"{tool_name}:{parameters!s}"
        if cache_result and cache_key in self._tool_cache:
            logger.debug(f"Using cached result for {tool_name}")
            return self._tool_cache[cache_key]

        # Execute tool
        logger.info(f"Calling MCP tool: {tool_name}")
        result = await self.mcp_client.server.execute_tool(tool_name, **parameters)

        # Cache result if requested
        if cache_result:
            self._tool_cache[cache_key] = result

        return result

    async def get_available_tools(self) -> list[str]:
        """
        Get list of available MCP tools.

        Returns:
            List of tool names
        """
        tools = self.mcp_client.server.get_registered_tools()
        return tools

    def create_tool_node(self, tool_name: str) -> Any:
        """
        Create a LangGraph node that wraps an MCP tool.

        Args:
            tool_name: Name of the MCP tool

        Returns:
            Node function for LangGraph
        """

        async def tool_node(state: ResearchState) -> ResearchState:
            """Execute MCP tool as LangGraph node."""
            logger.info(f"Executing MCP tool node: {tool_name}")

            # Prepare parameters from state
            parameters = self._prepare_tool_parameters(tool_name, state)

            try:
                # Call tool
                result = await self.call_tool(tool_name, parameters)

                # Store result in state
                if "tool_results" not in state.context:
                    state.context["tool_results"] = {}

                state.context["tool_results"][tool_name] = result

                logger.info(f"MCP tool {tool_name} executed successfully")

            except Exception as e:
                logger.error(f"MCP tool {tool_name} failed: {e}")
                state.validation_errors.append(f"Tool {tool_name} failed: {e!s}")

            return state

        return tool_node

    def _prepare_tool_parameters(
        self, tool_name: str, state: ResearchState
    ) -> dict[str, Any]:
        """
        Prepare parameters for MCP tool from state.

        Args:
            tool_name: Name of the tool
            state: Current research state

        Returns:
            Tool parameters
        """
        # Default parameters
        parameters: dict[str, Any] = {"query": state.query, "domains": state.domains}

        # Tool-specific parameter mapping
        if tool_name == "search_academic":
            parameters["query"] = state.query
            parameters["limit"] = 20

        elif tool_name == "format_citation":
            citations = state.context.get("aggregated_results", {}).get("citations", [])
            parameters["citations"] = citations
            parameters["style"] = state.context.get("citation_style", "APA")

        elif tool_name == "analyze_statistics":
            parameters["data"] = state.context.get("aggregated_results", {}).get(
                "metrics", {}
            )

        elif tool_name == "build_knowledge_graph":
            parameters["entities"] = state.context.get("query_analysis", {}).get(
                "key_concepts", []
            )
            parameters["relationships"] = []

        return parameters

    def clear_cache(self) -> None:
        """Clear the tool result cache."""
        self._tool_cache.clear()
        logger.info("MCP tool cache cleared")


__all__ = [
    "AgentAdapter",
    "LangGraphAgentNode",
    "MCPToolAdapter",
    "create_agent_nodes",
]
