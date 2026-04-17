"""
State management for LangGraph workflows.

This module defines the state schemas used throughout the orchestration
system, following immutable patterns for functional programming.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from src.agents.models import AgentResult


def _agent_result_to_dict(result: AgentResult) -> dict[str, Any]:
    """Convert AgentResult to dictionary."""
    return asdict(result)


class WorkflowPhase(Enum):
    """Phases of the research workflow."""

    INITIALIZATION = "initialization"
    QUERY_ANALYSIS = "query_analysis"
    PLAN_GENERATION = "plan_generation"
    LITERATURE_REVIEW = "literature_review"
    COMPARATIVE_ANALYSIS = "comparative_analysis"
    METHODOLOGY_DESIGN = "methodology_design"
    SYNTHESIS = "synthesis"
    CITATION_VERIFICATION = "citation_verification"
    QUALITY_CHECK = "quality_check"
    REPORT_GENERATION = "report_generation"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentExecutionStatus(Enum):
    """Status of agent task execution."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


@dataclass(frozen=True)
class AgentTaskState:
    """
    State of an individual agent task within the workflow.

    This immutable structure tracks the execution state of each agent task.
    """

    task_id: str
    agent_type: str
    status: AgentExecutionStatus
    input_data: dict[str, Any]
    result: AgentResult | None = None
    error: str | None = None
    retry_count: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def with_status(self, status: AgentExecutionStatus) -> "AgentTaskState":
        """Create new state with updated status."""
        return AgentTaskState(
            task_id=self.task_id,
            agent_type=self.agent_type,
            status=status,
            input_data=self.input_data,
            result=self.result,
            error=self.error,
            retry_count=self.retry_count,
            started_at=(
                self.started_at
                if status != AgentExecutionStatus.IN_PROGRESS
                else datetime.utcnow()
            ),
            completed_at=self.completed_at,
        )

    def with_result(self, result: AgentResult) -> "AgentTaskState":
        """Create new state with result."""
        return AgentTaskState(
            task_id=self.task_id,
            agent_type=self.agent_type,
            status=AgentExecutionStatus.COMPLETED,
            input_data=self.input_data,
            result=result,
            error=self.error,
            retry_count=self.retry_count,
            started_at=self.started_at,
            completed_at=datetime.utcnow(),
        )

    def with_error(self, error: str) -> "AgentTaskState":
        """Create new state with error."""
        return AgentTaskState(
            task_id=self.task_id,
            agent_type=self.agent_type,
            status=AgentExecutionStatus.FAILED,
            input_data=self.input_data,
            result=self.result,
            error=error,
            retry_count=self.retry_count + 1,
            started_at=self.started_at,
            completed_at=datetime.utcnow(),
        )


@dataclass(frozen=True)
class StateCheckpoint:
    """
    Checkpoint for workflow state persistence.

    Enables recovery and resumption of long-running workflows.
    """

    checkpoint_id: str
    phase: WorkflowPhase
    timestamp: datetime
    state_data: dict[str, Any]
    agent_states: dict[str, AgentTaskState]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert checkpoint to dictionary for persistence."""
        return {
            "checkpoint_id": self.checkpoint_id,
            "phase": self.phase.value,
            "timestamp": self.timestamp.isoformat(),
            "state_data": self.state_data,
            "agent_states": {
                k: {
                    "task_id": v.task_id,
                    "agent_type": v.agent_type,
                    "status": v.status.value,
                    "input_data": v.input_data,
                    "result": _agent_result_to_dict(v.result) if v.result else None,
                    "error": v.error,
                    "retry_count": v.retry_count,
                    "started_at": v.started_at.isoformat() if v.started_at else None,
                    "completed_at": (
                        v.completed_at.isoformat() if v.completed_at else None
                    ),
                }
                for k, v in self.agent_states.items()
            },
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class WorkflowMetadata:
    """
    Metadata about the workflow execution.

    Tracks performance metrics and execution patterns.
    """

    workflow_id: str
    started_at: datetime
    updated_at: datetime
    total_nodes_executed: int = 0
    total_edges_traversed: int = 0
    parallel_executions: int = 0
    error_recoveries: int = 0
    checkpoints_created: int = 0
    estimated_completion: datetime | None = None
    execution_path: list[str] = field(default_factory=list)
    performance_metrics: dict[str, float] = field(default_factory=dict)

    def with_node_execution(self, node_name: str) -> "WorkflowMetadata":
        """Update metadata after node execution."""
        new_path = [*self.execution_path, node_name]
        return WorkflowMetadata(
            workflow_id=self.workflow_id,
            started_at=self.started_at,
            updated_at=datetime.utcnow(),
            total_nodes_executed=self.total_nodes_executed + 1,
            total_edges_traversed=self.total_edges_traversed,
            parallel_executions=self.parallel_executions,
            error_recoveries=self.error_recoveries,
            checkpoints_created=self.checkpoints_created,
            estimated_completion=self.estimated_completion,
            execution_path=new_path,
            performance_metrics=self.performance_metrics,
        )


@dataclass
class ResearchState:
    """
    Main state container for research workflows.

    This is the primary state object that flows through the LangGraph workflow.
    Note: This is mutable to work with LangGraph's state management.
    """

    # Core identifiers
    project_id: str
    workflow_id: str

    # Research data
    query: str
    domains: list[str]
    research_plan: dict[str, Any] | None = None

    # Workflow state
    current_phase: WorkflowPhase = WorkflowPhase.INITIALIZATION
    previous_phase: WorkflowPhase | None = None

    # Agent management
    agent_tasks: dict[str, AgentTaskState] = field(default_factory=dict)
    agent_results: dict[str, AgentResult] = field(default_factory=dict)
    pending_agents: set[str] = field(default_factory=set)
    completed_agents: set[str] = field(default_factory=set)
    failed_agents: set[str] = field(default_factory=set)

    # Quality and validation
    quality_score: float = 0.0
    validation_errors: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)

    # Checkpointing
    checkpoints: list[StateCheckpoint] = field(default_factory=list)
    last_checkpoint: StateCheckpoint | None = None

    # Error handling
    error_count: int = 0
    max_errors: int = 3
    error_history: list[dict[str, Any]] = field(default_factory=list)

    # Metadata
    metadata: WorkflowMetadata | None = None

    # Additional context
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Initialize metadata if not provided."""
        if self.metadata is None:
            self.metadata = WorkflowMetadata(
                workflow_id=self.workflow_id,
                started_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )

    def transition_to_phase(self, new_phase: WorkflowPhase) -> None:
        """Transition to a new workflow phase."""
        self.previous_phase = self.current_phase
        self.current_phase = new_phase
        if self.metadata is not None:
            self.metadata = self.metadata.with_node_execution(new_phase.value)

    def add_agent_task(self, task: AgentTaskState) -> None:
        """Add an agent task to the state."""
        self.agent_tasks[task.task_id] = task
        self.pending_agents.add(task.agent_type)

    def complete_agent_task(self, task_id: str, result: AgentResult) -> None:
        """Mark an agent task as completed."""
        if task_id in self.agent_tasks:
            task = self.agent_tasks[task_id]
            self.agent_tasks[task_id] = task.with_result(result)
            self.agent_results[task.agent_type] = result
            self.pending_agents.discard(task.agent_type)
            self.completed_agents.add(task.agent_type)

    def fail_agent_task(self, task_id: str, error: str) -> None:
        """Mark an agent task as failed."""
        if task_id in self.agent_tasks:
            task = self.agent_tasks[task_id]
            self.agent_tasks[task_id] = task.with_error(error)
            self.pending_agents.discard(task.agent_type)
            self.failed_agents.add(task.agent_type)
            self.error_count += 1
            self.error_history.append(
                {
                    "task_id": task_id,
                    "agent_type": task.agent_type,
                    "error": error,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            )

    def create_checkpoint(self) -> StateCheckpoint:
        """Create a checkpoint of the current state."""
        checkpoint = StateCheckpoint(
            checkpoint_id=f"{self.workflow_id}-{len(self.checkpoints)}",
            phase=self.current_phase,
            timestamp=datetime.utcnow(),
            state_data={
                "project_id": self.project_id,
                "query": self.query,
                "domains": self.domains,
                "research_plan": self.research_plan,
                "quality_score": self.quality_score,
                "error_count": self.error_count,
            },
            agent_states=self.agent_tasks.copy(),
            metadata={
                "completed_agents": list(self.completed_agents),
                "failed_agents": list(self.failed_agents),
                "pending_agents": list(self.pending_agents),
            },
        )
        self.checkpoints.append(checkpoint)
        self.last_checkpoint = checkpoint
        return checkpoint

    def restore_from_checkpoint(self, checkpoint: StateCheckpoint) -> None:
        """Restore state from a checkpoint."""
        self.current_phase = checkpoint.phase
        self.agent_tasks = checkpoint.agent_states.copy()

        # Restore state data
        state_data = checkpoint.state_data
        self.research_plan = state_data.get("research_plan")
        self.quality_score = state_data.get("quality_score", 0.0)
        self.error_count = state_data.get("error_count", 0)

        # Restore agent sets
        metadata = checkpoint.metadata
        self.completed_agents = set(metadata.get("completed_agents", []))
        self.failed_agents = set(metadata.get("failed_agents", []))
        self.pending_agents = set(metadata.get("pending_agents", []))

        self.last_checkpoint = checkpoint

    def should_checkpoint(self) -> bool:
        """Determine if a checkpoint should be created."""
        # Checkpoint after major phases or every N operations
        major_phases = {
            WorkflowPhase.PLAN_GENERATION,
            WorkflowPhase.LITERATURE_REVIEW,
            WorkflowPhase.SYNTHESIS,
            WorkflowPhase.REPORT_GENERATION,
        }

        return (
            self.current_phase in major_phases
            or (self.metadata is not None and self.metadata.total_nodes_executed % 5 == 0)
            or self.error_count > 0
        )

    def is_terminal(self) -> bool:
        """Check if workflow is in a terminal state."""
        return (
            self.current_phase in {WorkflowPhase.COMPLETED, WorkflowPhase.FAILED}
            or self.error_count >= self.max_errors
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert state to dictionary for serialization."""
        return {
            "project_id": self.project_id,
            "workflow_id": self.workflow_id,
            "query": self.query,
            "domains": self.domains,
            "research_plan": self.research_plan,
            "current_phase": self.current_phase.value,
            "previous_phase": (
                self.previous_phase.value if self.previous_phase else None
            ),
            "agent_tasks": {k: v.__dict__ for k, v in self.agent_tasks.items()},
            "agent_results": {k: _agent_result_to_dict(v) for k, v in self.agent_results.items()},
            "pending_agents": list(self.pending_agents),
            "completed_agents": list(self.completed_agents),
            "failed_agents": list(self.failed_agents),
            "quality_score": self.quality_score,
            "validation_errors": self.validation_errors,
            "conflicts": self.conflicts,
            "error_count": self.error_count,
            "error_history": self.error_history,
            "context": self.context,
        }


__all__ = [
    "AgentExecutionStatus",
    "AgentTaskState",
    "ResearchState",
    "StateCheckpoint",
    "WorkflowMetadata",
    "WorkflowPhase",
]
