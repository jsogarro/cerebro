"""
Agent dispatch node for executing agent tasks.

This node dispatches tasks to the appropriate agents and manages their execution,
including parallel execution when possible.
"""

import asyncio
import logging
from typing import Any

from src.agents.factory import AgentFactory
from src.agents.models import AgentTask
from src.orchestration.state import (
    AgentExecutionStatus,
    AgentTaskState,
    ResearchState,
    WorkflowPhase,
)

logger = logging.getLogger(__name__)


async def agent_dispatch_node(state: ResearchState) -> ResearchState:
    """
    Dispatch and execute agent tasks.

    This node:
    1. Identifies agents ready for execution
    2. Dispatches tasks to agents (parallel when possible)
    3. Collects and validates results
    4. Handles agent failures and retries

    Args:
        state: Current workflow state

    Returns:
        Updated state with agent results
    """
    logger.info("Dispatching agent tasks")

    try:
        # Get agent factory
        agent_factory = AgentFactory()

        # Identify agents ready for execution
        ready_agents = identify_ready_agents(state)

        if not ready_agents:
            logger.warning("No agents ready for execution")
            return state

        logger.info(f"Found {len(ready_agents)} agents ready for execution")

        # Check if parallel execution is possible
        can_parallel = can_execute_parallel(ready_agents, state)

        if can_parallel and len(ready_agents) > 1:
            # Execute agents in parallel
            results = await execute_agents_parallel(ready_agents, state, agent_factory)
        else:
            # Execute agents sequentially
            results = await execute_agents_sequential(
                ready_agents, state, agent_factory
            )

        # Update state with results
        for agent_type, result in results.items():
            if result["status"] == "success":
                task_id = result["task_id"]
                state.complete_agent_task(task_id, result["result"])
                logger.info(f"Agent {agent_type} completed successfully")
            else:
                task_id = result["task_id"]
                state.fail_agent_task(task_id, result["error"])
                logger.error(f"Agent {agent_type} failed: {result['error']}")

        # Check if we need to retry any failed agents
        failed_critical = check_critical_failures(state)
        if failed_critical:
            state.transition_to_phase(WorkflowPhase.FAILED)
            logger.error("Critical agent failure detected, workflow failed")

    except Exception as e:
        logger.error(f"Error in agent dispatch: {e}")
        state.validation_errors.append(f"Agent dispatch failed: {e!s}")
        state.error_count += 1

    return state


def identify_ready_agents(state: ResearchState) -> list[AgentTaskState]:
    """
    Identify agents that are ready for execution.

    Args:
        state: Current workflow state

    Returns:
        List of agent tasks ready for execution
    """
    ready = []

    # Get dependency graph from research plan
    dependencies = (
        state.research_plan.get("dependencies", {}) if state.research_plan else {}
    )

    for task_id, task in state.agent_tasks.items():
        # Skip if not pending
        if task.status != AgentExecutionStatus.PENDING:
            continue

        # Check dependencies
        agent_deps = dependencies.get(task.agent_type, [])

        # Check if all dependencies are completed
        deps_satisfied = all(dep in state.completed_agents for dep in agent_deps)

        if deps_satisfied:
            ready.append(task)

    return ready


def can_execute_parallel(agents: list[AgentTaskState], state: ResearchState) -> bool:
    """
    Determine if agents can be executed in parallel.

    Args:
        agents: List of agent tasks
        state: Current workflow state

    Returns:
        True if parallel execution is allowed
    """
    # Check if parallel execution is enabled in context
    if not state.context.get("enable_parallel", True):
        return False

    # Check resource constraints
    max_parallel = state.context.get("max_parallel_agents", 3)
    if len(agents) > max_parallel:
        return False

    # Check if agents are in the same phase
    phases = set()
    for agent in agents:
        phase_info = agent.input_data.get("phase", "unknown")
        phases.add(phase_info)

    # Only allow parallel if all agents are in the same phase
    return len(phases) == 1


async def execute_agents_parallel(
    agents: list[AgentTaskState], state: ResearchState, agent_factory: AgentFactory
) -> dict[str, Any]:
    """
    Execute multiple agents in parallel.

    Args:
        agents: List of agent tasks to execute
        state: Current workflow state
        agent_factory: Factory for creating agents

    Returns:
        Dictionary of results by agent type
    """
    logger.info(f"Executing {len(agents)} agents in parallel")

    # Create tasks for parallel execution
    tasks = []
    agent_map = {}

    for agent_task in agents:
        # Create coroutine for agent execution
        coro = execute_single_agent(agent_task, state, agent_factory)
        task = asyncio.create_task(coro)
        tasks.append(task)
        agent_map[task] = agent_task.agent_type

    # Wait for all tasks to complete
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    execution_results = {}
    for task, result in zip(tasks, results, strict=False):
        agent_type = agent_map[task]

        if isinstance(result, Exception):
            execution_results[agent_type] = {
                "status": "error",
                "error": str(result),
                "task_id": next(
                    t.task_id for t in agents if t.agent_type == agent_type
                ),
            }
        else:
            execution_results[agent_type] = result

    return execution_results


async def execute_agents_sequential(
    agents: list[AgentTaskState], state: ResearchState, agent_factory: AgentFactory
) -> dict[str, Any]:
    """
    Execute agents sequentially.

    Args:
        agents: List of agent tasks to execute
        state: Current workflow state
        agent_factory: Factory for creating agents

    Returns:
        Dictionary of results by agent type
    """
    logger.info(f"Executing {len(agents)} agents sequentially")

    results = {}

    for agent_task in agents:
        result = await execute_single_agent(agent_task, state, agent_factory)
        results[agent_task.agent_type] = result

        # Stop if critical agent fails
        if result["status"] != "success" and is_critical_agent(agent_task.agent_type):
            logger.error(
                f"Critical agent {agent_task.agent_type} failed, stopping execution"
            )
            break

    return results


async def execute_single_agent(
    agent_task: AgentTaskState, state: ResearchState, agent_factory: AgentFactory
) -> dict[str, Any]:
    """
    Execute a single agent task.

    Args:
        agent_task: Agent task to execute
        state: Current workflow state
        agent_factory: Factory for creating agents

    Returns:
        Execution result dictionary
    """
    logger.info(f"Executing agent: {agent_task.agent_type}")

    try:
        # Update task status
        agent_task = agent_task.with_status(AgentExecutionStatus.IN_PROGRESS)

        # Create agent instance
        agent = agent_factory.create_agent(agent_task.agent_type)

        # Prepare input for agent
        agent_input = AgentTask(
            id=agent_task.task_id,
            agent_type=agent_task.agent_type,
            input_data={
                **agent_task.input_data,
                "research_plan": state.research_plan,
                "previous_results": {
                    k: v.to_dict() for k, v in state.agent_results.items()
                },
            },
            context=state.context,
        )

        # Execute agent
        result = await agent.execute(agent_input)

        # Validate result
        is_valid = await agent.validate_result(result)

        if is_valid:
            return {
                "status": "success",
                "result": result,
                "task_id": agent_task.task_id,
            }
        else:
            return {
                "status": "error",
                "error": "Agent result validation failed",
                "task_id": agent_task.task_id,
            }

    except Exception as e:
        logger.error(f"Agent execution failed: {e}")
        return {"status": "error", "error": str(e), "task_id": agent_task.task_id}


def is_critical_agent(agent_type: str) -> bool:
    """
    Check if an agent is critical for workflow success.

    Args:
        agent_type: Type of agent

    Returns:
        True if agent is critical
    """
    critical_agents = {"literature_review", "synthesis"}
    return agent_type in critical_agents


def check_critical_failures(state: ResearchState) -> bool:
    """
    Check if any critical agents have failed.

    Args:
        state: Current workflow state

    Returns:
        True if critical failure detected
    """
    critical_agents = {"literature_review", "synthesis"}

    for agent_type in critical_agents:
        if agent_type in state.failed_agents:
            # Check retry count
            task = next(
                (t for t in state.agent_tasks.values() if t.agent_type == agent_type),
                None,
            )

            if task and task.retry_count >= 3:
                return True

    return False


async def retry_failed_agent(
    agent_task: AgentTaskState, state: ResearchState, agent_factory: AgentFactory
) -> dict[str, Any]:
    """
    Retry a failed agent task.

    Args:
        agent_task: Failed agent task
        state: Current workflow state
        agent_factory: Factory for creating agents

    Returns:
        Retry result
    """
    logger.info(
        f"Retrying agent: {agent_task.agent_type} (attempt {agent_task.retry_count + 1})"
    )

    # Exponential backoff
    wait_time = min(2**agent_task.retry_count, 30)
    await asyncio.sleep(wait_time)

    # Execute agent again
    return await execute_single_agent(agent_task, state, agent_factory)


__all__ = ["agent_dispatch_node"]
