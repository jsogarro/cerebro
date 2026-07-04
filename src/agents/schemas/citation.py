"""
Citation agent schemas for structured output.
"""

from pydantic import BaseModel, Field


class FormattedCitation(BaseModel):
    """Schema for a single formatted citation."""

    citation_text: str = Field(description="Formatted citation text")
    source_id: str = Field(default="", description="Unique identifier for the source")


class CitationSchema(BaseModel):
    """Schema for citation formatting output."""

    citations: list[FormattedCitation] = Field(
        default_factory=list,
        description="List of formatted citations",
    )
    style: str = Field(
        default="APA",
        description="Citation style used (APA, MLA, Chicago, etc.)",
    )
    total_sources: int = Field(
        default=0,
        description="Total number of sources cited",
    )
