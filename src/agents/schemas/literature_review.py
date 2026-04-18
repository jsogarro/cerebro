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


class SourceVerification(BaseModel):
    """Schema for source verification results."""

    title: str = Field(description="Paper title being verified")
    exists: bool = Field(description="Whether this paper likely exists as described")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence that paper is real")
    issues: list[str] = Field(default_factory=list, description="Any issues found")
    corrected_title: str | None = Field(default=None, description="Corrected title if needed")
    corrected_authors: list[str] | None = Field(default=None, description="Corrected authors if needed")
    corrected_year: int | None = Field(default=None, description="Corrected year if needed")


class SourceValidationResult(BaseModel):
    """Schema for source validation results."""

    verified_sources: list[SourceVerification] = Field(description="Verification results for each source")
    total_verified: int = Field(description="Number of sources that passed verification")
    total_rejected: int = Field(description="Number of sources rejected as likely hallucinated")
    validation_notes: str = Field(default="", description="Overall validation notes")


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
