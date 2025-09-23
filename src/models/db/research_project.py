"""
Research Project database model.

Represents a research project in the system.
"""

from enum import Enum

from sqlalchemy import (
    JSON,
    Column,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import backref, relationship

from src.models.db.base import BaseModel


class ProjectStatus(str, Enum):
    """Research project status."""

    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResearchProject(BaseModel):
    """
    Research project model.

    Stores information about research projects including their
    query, status, quality scores, and metadata.
    """

    __tablename__ = "research_projects"

    # Basic fields
    title = Column(String(500), nullable=False, index=True)

    query = Column(Text, nullable=False)

    domains = Column(
        JSON, nullable=False, default=list, comment="List of research domains"
    )

    status = Column(
        SQLEnum(ProjectStatus), nullable=False, default=ProjectStatus.DRAFT, index=True
    )

    quality_score = Column(
        Float, nullable=True, comment="Overall quality score (0.0 to 1.0)"
    )

    # Foreign keys
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )

    # Optional workflow ID for Temporal/LangGraph
    workflow_id = Column(
        String(255),
        nullable=True,
        index=True,
        comment="Temporal or LangGraph workflow ID",
    )

    # Metadata for flexible data storage (renamed to avoid SQLAlchemy conflict)
    project_metadata = Column(
        "metadata",  # Use 'metadata' as the column name in the database
        JSON,
        nullable=False,
        default=dict,
        comment="Additional project metadata",
    )

    # Relationships
    user = relationship("User", backref=backref("research_projects", lazy="dynamic"))

    agent_tasks = relationship(
        "AgentTask",
        back_populates="project",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    results = relationship(
        "ResearchResult",
        back_populates="project",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    checkpoints = relationship(
        "WorkflowCheckpoint",
        back_populates="project",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    # Indexes
    __table_args__ = (
        Index("idx_project_user_status", "user_id", "status", "created_at"),
        Index("idx_project_workflow", "workflow_id"),
        Index("idx_project_quality", "quality_score", "status"),
    )

    def update_status(self, new_status: ProjectStatus) -> None:
        """
        Update project status.

        Args:
            new_status: New status for the project
        """
        self.status = new_status

    def set_quality_score(self, score: float) -> None:
        """
        Set quality score.

        Args:
            score: Quality score (0.0 to 1.0)
        """
        if not 0.0 <= score <= 1.0:
            raise ValueError("Quality score must be between 0.0 and 1.0")
        self.quality_score = score

    def add_metadata(self, key: str, value: any) -> None:
        """
        Add or update metadata.

        Args:
            key: Metadata key
            value: Metadata value
        """
        if self.project_metadata is None:
            self.project_metadata = {}
        self.project_metadata[key] = value

    def get_metadata(self, key: str, default: any = None) -> any:
        """
        Get metadata value.

        Args:
            key: Metadata key
            default: Default value if key not found

        Returns:
            Metadata value
        """
        if self.project_metadata is None:
            return default
        return self.project_metadata.get(key, default)

    @property
    def is_active(self) -> bool:
        """Check if project is active."""
        return self.status == ProjectStatus.IN_PROGRESS

    @property
    def is_complete(self) -> bool:
        """Check if project is complete."""
        return self.status in [ProjectStatus.COMPLETED, ProjectStatus.FAILED]

    def to_dict(self) -> dict:
        """Convert to dictionary with relationships."""
        data = super().to_dict()
        data["is_active"] = self.is_active
        data["is_complete"] = self.is_complete
        return data

    def __repr__(self) -> str:
        """String representation."""
        return f"<ResearchProject(id={self.id}, title='{self.title}', status={self.status.value})>"
