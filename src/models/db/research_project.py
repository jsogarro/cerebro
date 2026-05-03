"""
Research Project database model.

Represents a research project in the system.
"""

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Float,
    Index,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.db.base import UUID as DBUUID
from src.models.db.base import BaseModel


class ProjectStatus(StrEnum):
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

    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)

    query: Mapped[str] = mapped_column(Text, nullable=False)

    domains: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=list, comment="List of research domains"
    )

    status: Mapped[ProjectStatus] = mapped_column(
        SQLEnum(ProjectStatus), nullable=False, default=ProjectStatus.DRAFT, index=True
    )

    quality_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="Overall quality score (0.0 to 1.0)"
    )

    user_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        DBUUID(),
        nullable=True,
        index=True,
        comment="Tenant organization boundary identifier",
    )

    workflow_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        comment="Temporal or LangGraph workflow ID",
    )

    project_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
        comment="Additional project metadata",
    )

    # Relationships
    agent_tasks = relationship(
        "AgentTask",
        back_populates="project",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    results = relationship(
        "ResearchResult",
        back_populates="project",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    checkpoints = relationship(
        "WorkflowCheckpoint",
        back_populates="project",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    # Indexes
    __table_args__ = (
        Index("idx_project_org_user_status", "organization_id", "user_id", "status", "created_at"),
        Index("idx_project_user_status", "user_id", "status", "created_at"),
        Index("idx_project_org_status", "organization_id", "status", "created_at"),
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

    def add_metadata(self, key: str, value: Any) -> None:
        """
        Add or update metadata.

        Args:
            key: Metadata key
            value: Metadata value
        """
        if self.project_metadata is None:
            self.project_metadata = {}
        self.project_metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
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
        return bool(self.status == ProjectStatus.IN_PROGRESS)

    @property
    def is_complete(self) -> bool:
        """Check if project is complete."""
        return self.status in [ProjectStatus.COMPLETED, ProjectStatus.FAILED]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary with relationships."""
        data = super().to_dict()
        data["is_active"] = self.is_active
        data["is_complete"] = self.is_complete
        return data

    def __repr__(self) -> str:
        """String representation."""
        return f"<ResearchProject(id={self.id}, title='{self.title}', status={self.status.value})>"
