"""
Research Result database model.

Stores research findings, sources, and citations.
"""

from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.db.base import BaseModel


class ResultType(StrEnum):
    """Type of research result."""

    FINDING = "finding"
    SOURCE = "source"
    CITATION = "citation"
    METHODOLOGY = "methodology"
    COMPARISON = "comparison"
    SYNTHESIS = "synthesis"
    STATISTIC = "statistic"
    KNOWLEDGE_GRAPH = "knowledge_graph"
    SUMMARY = "summary"
    REPORT = "report"


class ResearchResult(BaseModel):
    """
    Research result model.

    Stores various types of research outputs including findings,
    sources, citations, and analysis results.
    """

    __tablename__ = "research_results"

    project_id: Mapped[Any] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("research_projects.id"),
        nullable=False,
        index=True,
    )

    result_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        comment="Type of result (finding, source, citation, etc.)",
    )

    content: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, comment="Result content (structure varies by type)"
    )

    confidence_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="Confidence score for the result (0.0 to 1.0)"
    )

    agent_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="Agent that produced this result",
    )

    result_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
        comment="Additional metadata",
    )

    source_id: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        index=True,
        comment="External source identifier (DOI, URL, etc.)",
    )

    # Relationships
    project = relationship("ResearchProject", back_populates="results")

    # Indexes
    __table_args__ = (
        Index("idx_result_project_type", "project_id", "result_type"),
        Index("idx_result_agent", "project_id", "agent_type"),
        Index("idx_result_confidence", "confidence_score", "result_type"),
        Index("idx_result_source", "source_id"),
    )

    def set_confidence(self, score: float) -> None:
        """
        Set confidence score.

        Args:
            score: Confidence score (0.0 to 1.0)
        """
        if not 0.0 <= score <= 1.0:
            raise ValueError("Confidence score must be between 0.0 and 1.0")
        self.confidence_score = score

    def add_metadata(self, key: str, value: Any) -> None:
        """
        Add or update metadata.

        Args:
            key: Metadata key
            value: Metadata value
        """
        if self.result_metadata is None:
            self.result_metadata = {}
        self.result_metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """
        Get metadata value.

        Args:
            key: Metadata key
            default: Default value if key not found

        Returns:
            Metadata value
        """
        if self.result_metadata is None:
            return default
        return self.result_metadata.get(key, default)

    @property
    def is_high_confidence(self) -> bool:
        """Check if result has high confidence (>= 0.7)."""
        return bool(self.confidence_score is not None and self.confidence_score >= 0.7)

    @property
    def is_finding(self) -> bool:
        """Check if result is a finding."""
        return bool(self.result_type == ResultType.FINDING.value)

    @property
    def is_source(self) -> bool:
        """Check if result is a source."""
        return bool(self.result_type == ResultType.SOURCE.value)

    @property
    def is_citation(self) -> bool:
        """Check if result is a citation."""
        return bool(self.result_type == ResultType.CITATION.value)

    def get_content_field(self, field: str, default: Any = None) -> Any:
        """
        Get a field from the content JSON.

        Args:
            field: Field name
            default: Default value if field not found

        Returns:
            Field value
        """
        if not self.content:
            return default
        return self.content.get(field, default)

    def set_content_field(self, field: str, value: Any) -> None:
        """
        Set a field in the content JSON.

        Args:
            field: Field name
            value: Field value
        """
        if self.content is None:
            self.content = {}
        self.content[field] = value

    def merge_content(self, additional_content: dict[str, Any]) -> None:
        """
        Merge additional content into existing content.

        Args:
            additional_content: Content to merge
        """
        if self.content is None:
            self.content = {}
        self.content.update(additional_content)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary with additional properties."""
        data = super().to_dict()
        data["is_high_confidence"] = self.is_high_confidence
        data["is_finding"] = self.is_finding
        data["is_source"] = self.is_source
        data["is_citation"] = self.is_citation
        return data

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"<ResearchResult(id={self.id}, type={self.result_type}, "
            f"confidence={self.confidence_score:.2f if self.confidence_score else 'N/A'})>"
        )
