"""
Agent Task database model.

Represents agent tasks within research projects.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.db.base import BaseModel


class TaskStatus(str, Enum):
    """Agent task status."""

    PENDING = "pending"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class AgentTask(BaseModel):
    """
    Agent task model.

    Stores information about individual agent tasks
    executed as part of research projects.
    """

    __tablename__ = "agent_tasks"

    project_id: Mapped[Any] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_projects.id"),
        nullable=False,
        index=True,
    )

    agent_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Type of agent (e.g., literature_review, synthesis)",
    )

    status: Mapped[TaskStatus] = mapped_column(
        SQLEnum(TaskStatus), nullable=False, default=TaskStatus.PENDING, index=True
    )

    input_data: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, comment="Input data for the agent"
    )

    output_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="Output data from the agent"
    )

    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Error message if task failed"
    )

    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="Number of retry attempts"
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    execution_time_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="Execution time in milliseconds"
    )

    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Task priority (higher = more important)",
    )

    depends_on: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="List of task IDs this task depends on"
    )

    # Relationships
    project = relationship("ResearchProject", back_populates="agent_tasks")

    # Indexes
    __table_args__ = (
        Index("idx_task_project_status", "project_id", "status"),
        Index("idx_task_agent_status", "agent_type", "status"),
        Index("idx_task_priority", "priority", "status"),
        Index("idx_task_started", "started_at", "status"),
    )

    def start(self) -> None:
        """Mark task as started."""

        self.status = TaskStatus.IN_PROGRESS
        self.started_at = datetime.utcnow()

    def complete(self, output_data: dict[str, Any]) -> None:
        """
        Mark task as completed.

        Args:
            output_data: Output data from the agent
        """

        self.status = TaskStatus.COMPLETED
        self.output_data = output_data
        self.completed_at = datetime.utcnow()

        if self.started_at:
            delta = self.completed_at - self.started_at
            self.execution_time_ms = int(delta.total_seconds() * 1000)

    def fail(self, error_message: str) -> None:
        """
        Mark task as failed.

        Args:
            error_message: Error message
        """

        self.status = TaskStatus.FAILED
        self.error_message = error_message
        self.completed_at = datetime.utcnow()

        if self.started_at:
            delta = self.completed_at - self.started_at
            self.execution_time_ms = int(delta.total_seconds() * 1000)

    def retry(self) -> None:
        """Mark task for retry."""
        self.status = TaskStatus.RETRYING
        self.retry_count += 1
        self.error_message = None
        self.output_data = None
        self.started_at = None
        self.completed_at = None
        self.execution_time_ms = None

    def cancel(self) -> None:
        """Cancel the task."""

        self.status = TaskStatus.CANCELLED
        if not self.completed_at:
            self.completed_at = datetime.utcnow()

    @property
    def is_pending(self) -> bool:
        """Check if task is pending."""
        return self.status in [TaskStatus.PENDING, TaskStatus.QUEUED]

    @property
    def is_running(self) -> bool:
        """Check if task is running."""
        return self.status in [TaskStatus.IN_PROGRESS, TaskStatus.RETRYING]

    @property
    def is_complete(self) -> bool:
        """Check if task is complete."""
        return self.status in [
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        ]

    @property
    def is_successful(self) -> bool:
        """Check if task completed successfully."""
        return self.status == TaskStatus.COMPLETED

    def can_start(self, completed_task_ids: set) -> bool:
        """
        Check if task can start based on dependencies.

        Args:
            completed_task_ids: Set of completed task IDs

        Returns:
            True if all dependencies are met
        """
        if not self.depends_on:
            return True

        dependencies = set(self.depends_on)
        return dependencies.issubset(completed_task_ids)

    def to_dict(self) -> dict:
        """Convert to dictionary with additional properties."""
        data = super().to_dict()
        data["is_pending"] = self.is_pending
        data["is_running"] = self.is_running
        data["is_complete"] = self.is_complete
        data["is_successful"] = self.is_successful
        return data

    def __repr__(self) -> str:
        """String representation."""
        return f"<AgentTask(id={self.id}, agent={self.agent_type}, status={self.status.value})>"
