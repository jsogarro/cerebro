"""
Data models for the agent system.

These models use immutable dataclasses to ensure functional programming principles.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentTask:
    """
    Represents a task to be executed by an agent.

    This is an immutable data structure that encapsulates all information
    needed for an agent to perform its work.
    """

    id: str
    agent_type: str
    input_data: dict[str, Any]
    context: dict[str, Any] = field(default_factory=dict)
    timeout: int = 300  # Default 5 minutes
    priority: int = 0  # Higher values = higher priority

    def with_updated_context(self, **kwargs) -> "AgentTask":
        """
        Create a new AgentTask with updated context.

        This maintains immutability by returning a new instance.
        """
        new_context = {**self.context, **kwargs}
        return AgentTask(
            id=self.id,
            agent_type=self.agent_type,
            input_data=self.input_data,
            context=new_context,
            timeout=self.timeout,
            priority=self.priority,
        )


@dataclass(frozen=True)
class AgentResult:
    """
    Represents the result of an agent's execution.

    This immutable structure contains the output and metadata from
    an agent's processing of a task.
    """

    task_id: str
    status: str  # success, failed, timeout
    output: dict[str, Any]
    confidence: float  # 0.0 to 1.0
    execution_time: float  # seconds
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_successful(self) -> bool:
        """Check if the result represents a successful execution."""
        return self.status == "success"

    def is_high_confidence(self, threshold: float = 0.8) -> bool:
        """Check if the result has high confidence."""
        return self.confidence >= threshold


@dataclass(frozen=True)
class AgentMessage:
    """
    Represents a message between agents.

    This enables inter-agent communication for coordination
    and information sharing.
    """

    from_agent: str
    to_agent: str
    message_type: str  # findings, request, response, coordination
    content: dict[str, Any]
    timestamp: float

    def is_broadcast(self) -> bool:
        """Check if this is a broadcast message to all agents."""
        return self.to_agent == "*"


@dataclass(frozen=True)
class AgentCapability:
    """
    Describes what an agent is capable of doing.

    Used for agent discovery and task routing.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_resources: list[str] = field(default_factory=list)

    def matches_task(self, task: AgentTask) -> bool:
        """Check if this capability matches the given task."""
        # Simple implementation - can be enhanced
        return task.agent_type.lower() in self.name.lower()


@dataclass(frozen=True)
class AgentMetrics:
    """
    Metrics collected from agent execution.

    Used for monitoring and optimization.
    """

    agent_type: str
    total_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    timeout_tasks: int = 0
    average_execution_time: float = 0.0
    average_confidence: float = 0.0

    def success_rate(self) -> float:
        """Calculate the success rate of the agent."""
        if self.total_tasks == 0:
            return 0.0
        return self.successful_tasks / self.total_tasks

    def with_new_task(self, result: AgentResult) -> "AgentMetrics":
        """
        Create updated metrics with a new task result.

        Maintains immutability by returning a new instance.
        """
        total = self.total_tasks + 1
        successful = self.successful_tasks + (1 if result.is_successful() else 0)
        failed = self.failed_tasks + (1 if result.status == "failed" else 0)
        timeout = self.timeout_tasks + (1 if result.status == "timeout" else 0)

        # Update averages
        avg_time = (
            self.average_execution_time * self.total_tasks + result.execution_time
        ) / total
        avg_conf = (
            self.average_confidence * self.total_tasks + result.confidence
        ) / total

        return AgentMetrics(
            agent_type=self.agent_type,
            total_tasks=total,
            successful_tasks=successful,
            failed_tasks=failed,
            timeout_tasks=timeout,
            average_execution_time=avg_time,
            average_confidence=avg_conf,
        )


@dataclass(frozen=True)
class AgentState:
    """
    Represents the current state of an agent.

    Used for monitoring and coordination.
    """

    agent_type: str
    status: str  # idle, processing, error
    current_task: str | None = None
    last_activity: float | None = None
    metrics: AgentMetrics = field(
        default_factory=lambda: AgentMetrics(agent_type="unknown")
    )

    def is_available(self) -> bool:
        """Check if the agent is available for new tasks."""
        return self.status == "idle"

    def with_new_status(
        self, status: str, task_id: str | None = None
    ) -> "AgentState":
        """
        Create a new state with updated status.

        Maintains immutability.
        """
        import time

        return AgentState(
            agent_type=self.agent_type,
            status=status,
            current_task=task_id,
            last_activity=time.time(),
            metrics=self.metrics,
        )
