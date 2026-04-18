"""
Literature review schemas for structured output.
"""

from pydantic import BaseModel, Field


class AcademicSource(BaseModel):
    """Schema for academic source information."""

    title: str = Field(description="Exact paper title")
    authors: list[str] = Field(description="List of author names")
    year: int = Field(description="Publication year")
    journal: str = Field(default="", description="Journal or conference name")
    abstract: str = Field(default="", description="2-3 sentence summary")
    doi: str | None = Field(default=None, description="DOI if known")
    relevance_score: float = Field(
        default=0.8, ge=0.0, le=1.0, description="Relevance score 0.0-1.0"
    )


class LiteratureAnalysisSchema(BaseModel):
    """Schema for complete literature analysis output."""

    sources: list[AcademicSource] = Field(
        default_factory=list,
        description="List of real academic sources identified",
    )
    key_findings: list[str] = Field(
        default_factory=list,
        description="Key findings extracted from the literature",
    )
    research_gaps: list[str] = Field(
        default_factory=list,
        description="Identified gaps in the literature",
    )
    methodologies_used: list[str] = Field(
        default_factory=list,
        description="Research methodologies found in the papers",
    )
    quality_assessment: str = Field(
        default="",
        description="Overall quality assessment of the literature",
    )
