"""
WebSocket message models for real-time communication.

These models define the structure for WebSocket messages sent between
the server and various clients (web, CLI, etc.).
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class WSMessageType(StrEnum):
    """WebSocket message types."""

    # Progress updates
    PROGRESS = "progress"

    # Agent activity updates
    AGENT_STARTED = "agent.started"
    AGENT_PROGRESS = "agent.progress"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"

    # Project lifecycle events
    PROJECT_STARTED = "project.started"
    PROJECT_COMPLETED = "project.completed"
    PROJECT_FAILED = "project.failed"
    PROJECT_CANCELLED = "project.cancelled"

    # Workflow events
    WORKFLOW_PHASE_STARTED = "workflow.phase.started"
    WORKFLOW_PHASE_COMPLETED = "workflow.phase.completed"

    # System events
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    # Connection events
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    HEARTBEAT = "heartbeat"


class WSMessage(BaseModel):
    """Base WebSocket message."""

    type: WSMessageType = Field(..., description="Message type")
    project_id: UUID | None = Field(None, description="Associated project ID")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    data: dict[str, Any] = Field(default_factory=dict, description="Message payload")

    class Config:
        """Pydantic configuration."""

        json_encoders = {
            datetime: lambda v: v.isoformat(),
            UUID: lambda v: str(v),
        }


class ProgressUpdate(BaseModel):
    """Progress update data."""

    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    in_progress_tasks: int = 0
    pending_tasks: int = 0
    progress_percentage: float = 0.0
    estimated_time_remaining_seconds: int | None = None
    current_agent: str | None = None
    current_phase: str | None = None
    last_updated: datetime = Field(default_factory=datetime.utcnow)


class AgentUpdate(BaseModel):
    """Agent activity update data."""

    agent_type: str = Field(..., description="Type of agent")
    agent_id: str = Field(..., description="Unique agent instance ID")
    status: str = Field(..., description="Agent execution status")
    task_description: str | None = None
    progress_percentage: float | None = None
    result_summary: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    estimated_duration_seconds: int | None = None


class WorkflowPhaseUpdate(BaseModel):
    """Workflow phase update data."""

    phase_name: str = Field(..., description="Name of the workflow phase")
    phase_status: str = Field(..., description="Phase execution status")
    description: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    agents_involved: list[str] = Field(default_factory=list)


class CLIProgressBar(BaseModel):
    """CLI-specific progress bar configuration."""

    description: str = Field(..., description="Progress bar description")
    percentage: float = Field(..., ge=0, le=100)
    completed: int = 0
    total: int = 0
    show_eta: bool = True
    color: str = "green"


class CLIWSMessage(WSMessage):
    """CLI-specific WebSocket message with formatted output."""

    formatted_text: str | None = Field(
        None, description="Pre-formatted text for terminal"
    )
    progress_bar: CLIProgressBar | None = Field(
        None, description="Progress bar data"
    )
    clear_screen: bool = Field(False, description="Whether to clear terminal screen")

    def to_terminal_output(self) -> str:
        """Convert message to terminal output."""
        if self.formatted_text:
            return self.formatted_text

        # Generate default formatted output based on message type
        if self.type == WSMessageType.PROGRESS:
            progress_data = ProgressUpdate(**self.data)
            return f"Progress: {progress_data.progress_percentage:.1f}% ({progress_data.completed_tasks}/{progress_data.total_tasks} tasks)"

        elif self.type == WSMessageType.AGENT_STARTED:
            agent_data = AgentUpdate(**self.data)
            return f"🚀 Started: {agent_data.agent_type} - {agent_data.task_description or 'Processing...'}"

        elif self.type == WSMessageType.AGENT_COMPLETED:
            agent_data = AgentUpdate(**self.data)
            return f"✅ Completed: {agent_data.agent_type} - {agent_data.result_summary or 'Done'}"

        elif self.type == WSMessageType.AGENT_FAILED:
            agent_data = AgentUpdate(**self.data)
            return f"❌ Failed: {agent_data.agent_type} - {agent_data.error_message or 'Unknown error'}"

        elif self.type == WSMessageType.ERROR:
            return f"❌ Error: {self.data.get('message', 'Unknown error occurred')}"

        elif self.type == WSMessageType.WARNING:
            return f"⚠️  Warning: {self.data.get('message', 'Warning')}"

        elif self.type == WSMessageType.INFO:
            return f"ℹ️  Info: {self.data.get('message', 'Information')}"  # noqa: RUF001

        else:
            return f"{self.type}: {self.data}"


class ConnectionInfo(BaseModel):
    """Connection information for WebSocket clients."""

    client_id: str = Field(..., description="Unique client identifier")
    client_type: str = Field(..., description="Type of client (web, cli, etc.)")
    user_id: str | None = None
    project_subscriptions: list[UUID] = Field(default_factory=list)
    connected_at: datetime = Field(default_factory=datetime.utcnow)
    last_heartbeat: datetime = Field(default_factory=datetime.utcnow)


class SubscriptionRequest(BaseModel):
    """WebSocket subscription request."""

    action: str = Field(..., description="Action: subscribe or unsubscribe")
    project_id: UUID | None = Field(None, description="Project to subscribe to")
    message_types: list[WSMessageType] | None = Field(
        None, description="Specific message types to subscribe to"
    )
    client_type: str = Field(
        default="web", description="Client type for message formatting"
    )


class SubscriptionResponse(BaseModel):
    """WebSocket subscription response."""

    success: bool = Field(..., description="Whether subscription was successful")
    message: str = Field(..., description="Response message")
    active_subscriptions: list[UUID] = Field(
        default_factory=list, description="Currently active project subscriptions"
    )


class HeartbeatMessage(BaseModel):
    """Heartbeat message for connection health."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    client_id: str

    class Config:
        """Pydantic configuration."""

        json_encoders = {
            datetime: lambda v: v.isoformat(),
        }
