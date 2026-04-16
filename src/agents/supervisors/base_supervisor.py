"""
Base Supervisor Agent

Abstract base class for all supervisor agents implementing LangGraph orchestration
with TalkHier communication protocols. Provides the foundation for coordinating
teams of specialized worker agents.

Key Features:
- LangGraph state management and workflow orchestration
- TalkHier multi-round refinement protocols
- Dynamic worker allocation and coordination
- Quality assurance and consensus building
- Integration with MASR routing decisions
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Type, Union
from enum import Enum

from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolExecutor

from src.core.types import SupervisionStatsDict

from ..base import BaseAgent
from ..models import AgentTask, AgentResult
from ..communication.communication_protocol import (
    CommunicationProtocol,
    RefinementResult,
    CommunicationMode,
)
from ..communication.talkhier_message import (
    TalkHierMessage,
    TalkHierContent,
    MessageType,
    HierarchyMetadata,
    RefinementMetadata,
)
from ...prompts.manager import get_prompt_manager

logger = logging.getLogger(__name__)


class SupervisionMode(Enum):
    """Modes of supervision for different scenarios."""

    SEQUENTIAL = "sequential"  # Workers execute in sequence
    PARALLEL = "parallel"  # Workers execute simultaneously
    HYBRID = "hybrid"  # Mix of sequential and parallel
    ADAPTIVE = "adaptive"  # Dynamically determined based on task


class WorkerAllocation(Enum):
    """Worker allocation strategies."""

    ALL_WORKERS = "all_workers"  # Use all available workers
    OPTIMAL_SET = "optimal_set"  # Use optimal worker subset
    MINIMAL_VIABLE = "minimal_viable"  # Use minimum workers needed
    DYNAMIC = "dynamic"  # Dynamically allocate based on progress


@dataclass
class SupervisionState:
    """State for supervisor agent workflows."""

    # Task information
    task_id: str = ""
    original_query: str = ""
    task_type: str = ""
    domain: str = ""

    # Worker management
    allocated_workers: List[str] = field(default_factory=list)
    worker_tasks: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    worker_results: Dict[str, Any] = field(default_factory=dict)

    # Coordination state
    current_phase: str = "initialization"
    supervision_mode: SupervisionMode = SupervisionMode.PARALLEL

    # TalkHier refinement
    refinement_round: int = 1
    consensus_score: float = 0.0
    quality_score: float = 0.0

    # Progress tracking
    started_at: datetime = field(default_factory=datetime.now)
    phase_history: List[Dict[str, Any]] = field(default_factory=list)

    # Context and metadata
    context: Dict[str, Any] = field(default_factory=dict)
    supervision_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkerDefinition:
    """Definition of a worker agent type."""

    worker_type: str
    agent_class: Type[BaseAgent]
    specialization: str
    capabilities: List[str] = field(default_factory=list)

    # Allocation preferences
    required_for: List[str] = field(
        default_factory=list
    )  # Always required for these tasks
    optimal_for: List[str] = field(default_factory=list)  # Optimal for these tasks

    # Performance characteristics
    avg_execution_time_ms: int = 30000
    reliability_score: float = 0.95
    quality_score: float = 0.85


class BaseSupervisor(BaseAgent, ABC):
    """
    Abstract base supervisor agent implementing LangGraph + TalkHier patterns.

    Provides common supervision functionality:
    - Worker team coordination
    - LangGraph workflow management
    - TalkHier refinement protocols
    - Quality assurance and consensus building
    """

    def __init__(
        self,
        supervisor_type: str,
        domain: str,
        gemini_service: Optional[Any] = None,
        cache_client: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize base supervisor."""
        super().__init__(gemini_service, cache_client, config)

        self.supervisor_type = supervisor_type
        self.domain = domain

        # Worker management
        self.worker_definitions: Dict[str, WorkerDefinition] = {}
        self.active_workers: Dict[str, BaseAgent] = {}
        self.worker_allocation_strategy = WorkerAllocation.OPTIMAL_SET

        # LangGraph components
        self.workflow_graph: Optional[StateGraph] = None
        self.tool_executor: Optional[ToolExecutor] = None

        # Communication protocol
        self.communication_protocol = CommunicationProtocol(
            config.get("communication_protocol", {}) if config else {}
        )

        # Supervision configuration
        self.max_workers = config.get("max_workers", 10) if config else 10
        self.default_timeout = config.get("default_timeout", 300) if config else 300
        self.quality_threshold = (
            config.get("quality_threshold", 0.85) if config else 0.85
        )

        # Performance tracking
        self.supervised_tasks = 0
        self.successful_supervisions = 0
        self.average_supervision_time = 0.0

        # Initialize supervisor-specific components
        self._register_worker_types()
        self._build_workflow_graph()

    @abstractmethod
    def _register_worker_types(self):
        """Register worker types for this supervisor."""
        pass

    @abstractmethod
    def _build_workflow_graph(self):
        """Build LangGraph workflow for this supervisor."""
        pass

    @abstractmethod
    async def _coordinate_workers(
        self, state: SupervisionState, task: AgentTask
    ) -> SupervisionState:
        """Coordinate worker execution (supervisor-specific logic)."""
        pass

    def get_agent_type(self) -> str:
        """Return supervisor agent type."""
        return f"{self.supervisor_type}_supervisor"

    async def execute(self, task: AgentTask) -> AgentResult:
        """
        Execute supervision task using LangGraph + TalkHier protocol.

        Args:
            task: Supervision task to execute

        Returns:
            AgentResult with coordinated team output
        """

        start_time = datetime.now()
        self.supervised_tasks += 1

        try:
            # Initialize supervision state
            state = SupervisionState(
                task_id=task.id,
                original_query=str(task.input_data.get("query", "")),
                task_type=task.agent_type,
                domain=self.domain,
                supervision_mode=self._determine_supervision_mode(task),
                context=task.input_data,
            )

            # Execute LangGraph workflow
            final_state = await self._execute_supervision_workflow(state, task)

            # Build final result
            result = await self._build_supervision_result(final_state, task, start_time)

            if result.status == "completed":
                self.successful_supervisions += 1

            # Update performance metrics
            execution_time = (datetime.now() - start_time).total_seconds()
            self.average_supervision_time = (
                self.average_supervision_time * (self.supervised_tasks - 1)
                + execution_time
            ) / self.supervised_tasks

            return result

        except Exception as e:
            logger.error(f"Supervision failed for task {task.id}: {e}")
            return self.handle_error(task, e)

    async def validate_result(self, result: AgentResult) -> bool:
        """Validate supervision result."""

        if result.status != "completed":
            return False

        # Check if quality threshold was met
        quality_score = result.output.get("supervision_quality", {}).get(
            "overall_quality", 0.0
        )

        return quality_score >= self.quality_threshold

    def register_worker(self, worker_def: WorkerDefinition, worker_instance: BaseAgent):
        """Register a worker agent with this supervisor."""

        self.worker_definitions[worker_def.worker_type] = worker_def
        self.active_workers[worker_def.worker_type] = worker_instance

        logger.info(
            f"Registered worker {worker_def.worker_type} with {self.supervisor_type} supervisor"
        )

    async def allocate_workers(self, task: AgentTask) -> List[str]:
        """
        Allocate optimal workers for a task.

        Args:
            task: Task to allocate workers for

        Returns:
            List of worker types to use
        """

        task_type = task.agent_type
        complexity = task.input_data.get("complexity_score", 0.5)

        if self.worker_allocation_strategy == WorkerAllocation.ALL_WORKERS:
            return list(self.worker_definitions.keys())

        elif self.worker_allocation_strategy == WorkerAllocation.OPTIMAL_SET:
            # Select workers optimal for this task type
            optimal_workers = []

            for worker_type, worker_def in self.worker_definitions.items():
                if (
                    task_type in worker_def.optimal_for
                    or task_type in worker_def.required_for
                ):
                    optimal_workers.append(worker_type)

            # Ensure minimum viable team
            if not optimal_workers:
                optimal_workers = list(self.worker_definitions.keys())[:3]

            return optimal_workers

        elif self.worker_allocation_strategy == WorkerAllocation.MINIMAL_VIABLE:
            # Select minimum required workers
            required_workers = []

            for worker_type, worker_def in self.worker_definitions.items():
                if task_type in worker_def.required_for:
                    required_workers.append(worker_type)

            # Ensure at least one worker
            if not required_workers and self.worker_definitions:
                required_workers = [list(self.worker_definitions.keys())[0]]

            return required_workers

        else:  # DYNAMIC
            # Dynamic allocation based on task complexity
            worker_count = min(
                int(complexity * len(self.worker_definitions)) + 1,
                len(self.worker_definitions),
            )

            # Select highest-rated workers for task
            workers_by_score = sorted(
                self.worker_definitions.items(),
                key=lambda x: x[1].quality_score,
                reverse=True,
            )

            return [worker[0] for worker in workers_by_score[:worker_count]]

    def _determine_supervision_mode(self, task: AgentTask) -> SupervisionMode:
        """Determine optimal supervision mode for task."""

        complexity = task.input_data.get("complexity_score", 0.5)
        dependencies = task.input_data.get("dependencies", [])

        if dependencies:
            return SupervisionMode.SEQUENTIAL
        elif complexity > 0.8:
            return SupervisionMode.HYBRID
        else:
            return SupervisionMode.PARALLEL

    async def _execute_supervision_workflow(
        self, state: SupervisionState, task: AgentTask
    ) -> SupervisionState:
        """Execute supervision workflow using LangGraph."""

        if not self.workflow_graph:
            raise ValueError("Workflow graph not initialized")

        # Convert state to LangGraph format
        langgraph_state = {
            "supervision_state": state,
            "original_task": task,
            "supervisor_type": self.supervisor_type,
        }

        # Execute workflow
        final_langgraph_state = await self.workflow_graph.ainvoke(langgraph_state)

        # Extract final state
        final_state = final_langgraph_state["supervision_state"]

        return final_state

    async def _build_supervision_result(
        self, state: SupervisionState, task: AgentTask, start_time: datetime
    ) -> AgentResult:
        """Build final supervision result."""

        execution_time = (datetime.now() - start_time).total_seconds()

        # Aggregate worker outputs
        aggregated_output = {
            "supervision_type": self.supervisor_type,
            "domain": self.domain,
            "worker_results": state.worker_results,
            "supervision_quality": {
                "overall_quality": state.quality_score,
                "consensus_achieved": state.consensus_score >= self.quality_threshold,
                "final_consensus_score": state.consensus_score,
                "refinement_rounds": state.refinement_round,
            },
            "coordination_metadata": {
                "supervision_mode": state.supervision_mode.value,
                "workers_used": state.allocated_workers,
                "total_phases": len(state.phase_history),
                "execution_time_seconds": execution_time,
            },
        }

        # Determine result status
        status = (
            "completed"
            if state.consensus_score >= self.quality_threshold
            else "partial"
        )
        confidence = min(state.consensus_score, state.quality_score)

        return AgentResult(
            task_id=task.id,
            status=status,
            output=aggregated_output,
            confidence=confidence,
            execution_time=execution_time,
            metadata={
                "agent_type": self.get_agent_type(),
                "supervision_mode": state.supervision_mode.value,
                "workers_coordinated": len(state.allocated_workers),
                "refinement_rounds": state.refinement_round,
            },
        )

    async def send_talkhier_message(
        self,
        target_worker: str,
        message_type: MessageType,
        content: Union[TalkHierContent, str],
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[TalkHierMessage]:
        """Send TalkHier message to worker agent."""

        worker = self.active_workers.get(target_worker)
        if not worker:
            logger.error(f"Worker not found: {target_worker}")
            return None

        # Create TalkHier message
        if isinstance(content, str):
            talkhier_content = TalkHierContent(content=content)
        else:
            talkhier_content = content

        message = TalkHierMessage(
            from_agent=self.get_agent_type(),
            to_agent=target_worker,
            message_type=message_type,
            content=talkhier_content,
            hierarchy_metadata=HierarchyMetadata(
                hierarchy_level=2,  # Supervisor level
                worker_ids=list(self.active_workers.keys()),
            ),
            context=context or {},
        )

        # Send through communication protocol
        try:
            response = await self.communication_protocol._send_message_to_agent(
                worker, message
            )
            return response

        except Exception as e:
            logger.error(f"Failed to send message to worker {target_worker}: {e}")
            return None

    async def broadcast_to_workers(
        self,
        message_type: MessageType,
        content: Union[TalkHierContent, str],
        worker_subset: Optional[List[str]] = None,
    ) -> List[TalkHierMessage]:
        """Broadcast message to all or subset of workers."""

        target_workers = worker_subset or list(self.active_workers.keys())
        responses = []

        # Send to each worker
        for worker_type in target_workers:
            response = await self.send_talkhier_message(
                worker_type, message_type, content
            )
            if response:
                responses.append(response)

        return responses

    async def coordinate_refinement_round(
        self, state: SupervisionState, round_number: int
    ) -> SupervisionState:
        """Coordinate a single refinement round."""

        logger.info(f"Coordinating refinement round {round_number}")

        # Get refinement prompt
        try:
            prompt_manager = await get_prompt_manager()

            refinement_variables = {
                "round": round_number,
                "previous_outputs": state.worker_results,
                "consensus_score": state.consensus_score,
                "target_threshold": self.quality_threshold,
            }

            refinement_prompt = await prompt_manager.get_prompt(
                f"refinement/round_{round_number}", refinement_variables
            )

            # Create refinement message
            refinement_content = TalkHierContent(
                content=refinement_prompt,
                background=f"Round {round_number} refinement coordination",
                intermediate_outputs={
                    "previous_consensus": state.consensus_score,
                    "target_quality": self.quality_threshold,
                    "worker_count": len(state.allocated_workers),
                },
            )

            # Broadcast refinement request to workers
            responses = await self.broadcast_to_workers(
                MessageType.REFINEMENT_REQUEST,
                refinement_content,
                state.allocated_workers,
            )

            # Update state with refinement round results
            state.refinement_round = round_number

            # Evaluate consensus for this round
            if responses:
                consensus_score = await self.communication_protocol.consensus_builder.evaluate_consensus(
                    responses
                )
                state.consensus_score = consensus_score.overall_score
                state.quality_score = consensus_score.evidence_quality

            # Track phase history
            state.phase_history.append(
                {
                    "round": round_number,
                    "consensus_score": state.consensus_score,
                    "quality_score": state.quality_score,
                    "responses_received": len(responses),
                    "timestamp": datetime.now().isoformat(),
                }
            )

        except Exception as e:
            logger.error(f"Refinement round {round_number} failed: {e}")

        return state

    async def get_supervision_stats(self) -> SupervisionStatsDict:
        """Get supervisor performance statistics."""

        success_rate = self.successful_supervisions / max(self.supervised_tasks, 1)

        return {
            "supervisor": {
                "type": self.supervisor_type,
                "domain": self.domain,
                "supervised_tasks": self.supervised_tasks,
                "successful_supervisions": self.successful_supervisions,
                "success_rate": success_rate,
                "average_supervision_time": self.average_supervision_time,
            },
            "workers": {
                "total_worker_types": len(self.worker_definitions),
                "active_workers": len(self.active_workers),
                "worker_types": list(self.worker_definitions.keys()),
            },
            "communication": await self.communication_protocol.get_protocol_stats(),
        }

    def _create_langgraph_node(self, node_name: str, node_func: callable):
        """Create LangGraph node with error handling."""

        async def wrapped_node(state: dict[str, Any]) -> dict[str, Any]:
            try:
                return await node_func(state)
            except Exception as e:
                logger.error(f"LangGraph node {node_name} failed: {e}")
                # Return state with error information
                state["error"] = str(e)
                state["failed_node"] = node_name
                return state

        return wrapped_node

    async def close(self) -> None:
        """Close supervisor and cleanup resources."""

        # Close active workers
        for worker in self.active_workers.values():
            if hasattr(worker, "close"):
                try:
                    await worker.close()
                except Exception as e:
                    logger.warning(f"Failed to close worker: {e}")

        self.active_workers.clear()

        logger.info(f"Closed {self.supervisor_type} supervisor")


__all__ = [
    "BaseSupervisor",
    "SupervisionState",
    "WorkerDefinition",
    "SupervisionMode",
    "WorkerAllocation",
]
