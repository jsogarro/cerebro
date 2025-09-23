"""
Workflow Checkpoint database model.

Stores workflow checkpoints for recovery and analysis.
"""

from typing import Any

from sqlalchemy import JSON, Boolean, Column, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.models.db.base import BaseModel


class WorkflowCheckpoint(BaseModel):
    """
    Workflow checkpoint model.

    Stores checkpoint data for Temporal and LangGraph workflows,
    enabling recovery and workflow analysis.
    """

    __tablename__ = "workflow_checkpoints"

    # Workflow identification
    workflow_id = Column(
        String(255),
        nullable=False,
        index=True,
        comment="Temporal or LangGraph workflow ID",
    )

    # Foreign key to research project
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("research_projects.id"),
        nullable=False,
        index=True,
    )

    # Checkpoint data
    checkpoint_data = Column(JSON, nullable=False, comment="Serialized workflow state")

    # Phase/stage information
    phase = Column(
        String(100), nullable=False, index=True, comment="Workflow phase at checkpoint"
    )

    # Checkpoint metadata
    checkpoint_type = Column(
        String(50),
        nullable=False,
        default="automatic",
        comment="Type of checkpoint (automatic, manual, error)",
    )

    checkpoint_version = Column(
        String(50), nullable=True, comment="Version of checkpoint format"
    )

    # Recovery information
    is_recoverable = Column(
        Boolean,
        nullable=False,
        default=True,
        comment="Whether this checkpoint can be used for recovery",
    )

    recovery_metadata = Column(
        JSON, nullable=True, comment="Additional data needed for recovery"
    )

    # Performance metrics at checkpoint
    execution_metrics = Column(
        JSON, nullable=True, comment="Performance metrics at checkpoint time"
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
        return self.checkpoint_type == "automatic"

    @property
    def is_manual(self) -> bool:
        """Check if checkpoint was created manually."""
        return self.checkpoint_type == "manual"

    @property
    def is_error_checkpoint(self) -> bool:
        """Check if checkpoint was created due to error."""
        return self.checkpoint_type == "error"

    @property
    def agent_states(self) -> dict[str, Any]:
        """Get agent states from checkpoint data."""
        return self.get_state("agent_states", {})

    @property
    def workflow_context(self) -> dict[str, Any]:
        """Get workflow context from checkpoint data."""
        return self.get_state("context", {})

    def to_dict(self) -> dict:
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
