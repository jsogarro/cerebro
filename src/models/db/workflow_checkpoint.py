"""
Workflow Checkpoint database model.

Stores workflow checkpoints for recovery and analysis.
"""

import uuid
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.db.base import BaseModel


class WorkflowCheckpoint(BaseModel):
    """
    Workflow checkpoint model.

    Stores checkpoint data for Temporal and LangGraph workflows,
    enabling recovery and workflow analysis.
    """

    __tablename__ = "workflow_checkpoints"

    # Workflow identification
    workflow_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    # Foreign key to research project
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_projects.id"),
        nullable=False,
        index=True,
    )

    # Checkpoint data
    checkpoint_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # Phase/stage information
    phase: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )

    # Checkpoint metadata
    checkpoint_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="automatic",
    )

    checkpoint_version: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )

    # Recovery information
    is_recoverable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    recovery_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )

    # Performance metrics at checkpoint
    execution_metrics: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )

    # Relationships
    project = relationship("ResearchProject", back_populates="checkpoints")

    # Indexes
    __table_args__ = (
        Index("idx_checkpoint_workflow", "workflow_id", "created_at"),
        Index("idx_checkpoint_project_phase", "project_id", "phase"),
        Index("idx_checkpoint_recoverable", "is_recoverable", "workflow_id"),
    )

    def get_state(self, key: str, default: Any = None) -> Any:
        """
        Get a value from checkpoint data.

        Args:
            key: State key
            default: Default value if key not found

        Returns:
            State value
        """
        if not self.checkpoint_data:
            return default
        return self.checkpoint_data.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        """
        Set a value in checkpoint data.

        Args:
            key: State key
            value: State value
        """
        if self.checkpoint_data is None:
            self.checkpoint_data = {}
        self.checkpoint_data[key] = value

    def update_state(self, state_updates: dict[str, Any]) -> None:
        """
        Update multiple state values.

        Args:
            state_updates: Dictionary of state updates
        """
        if self.checkpoint_data is None:
            self.checkpoint_data = {}
        self.checkpoint_data.update(state_updates)

    def add_metric(self, metric_name: str, metric_value: Any) -> None:
        """
        Add execution metric.

        Args:
            metric_name: Name of the metric
            metric_value: Metric value
        """
        if self.execution_metrics is None:
            self.execution_metrics = {}
        self.execution_metrics[metric_name] = metric_value

    def get_metric(self, metric_name: str, default: Any = None) -> Any:
        """
        Get execution metric.

        Args:
            metric_name: Name of the metric
            default: Default value if metric not found

        Returns:
            Metric value
        """
        if not self.execution_metrics:
            return default
        return self.execution_metrics.get(metric_name, default)

    def mark_as_error_checkpoint(self, error_info: dict[str, Any]) -> None:
        """
        Mark checkpoint as error checkpoint.

        Args:
            error_info: Information about the error
        """
        self.checkpoint_type = "error"
        self.is_recoverable = True

        if self.recovery_metadata is None:
            self.recovery_metadata = {}
        self.recovery_metadata["error_info"] = error_info

    @property
    def is_automatic(self) -> bool:
        """Check if checkpoint was created automatically."""
        return bool(self.checkpoint_type == "automatic")

    @property
    def is_manual(self) -> bool:
        """Check if checkpoint was created manually."""
        return bool(self.checkpoint_type == "manual")

    @property
    def is_error_checkpoint(self) -> bool:
        """Check if checkpoint was created due to error."""
        return bool(self.checkpoint_type == "error")

    @property
    def agent_states(self) -> dict[str, Any]:
        """Get agent states from checkpoint data."""
        result = self.get_state("agent_states", {})
        return result if isinstance(result, dict) else {}

    @property
    def workflow_context(self) -> dict[str, Any]:
        """Get workflow context from checkpoint data."""
        result = self.get_state("context", {})
        return result if isinstance(result, dict) else {}

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary with additional properties."""
        data = super().to_dict()
        data["is_automatic"] = self.is_automatic
        data["is_manual"] = self.is_manual
        data["is_error_checkpoint"] = self.is_error_checkpoint
        return data

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"<WorkflowCheckpoint(id={self.id}, workflow={self.workflow_id}, "
            f"phase={self.phase}, type={self.checkpoint_type})>"
        )
