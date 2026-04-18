"""
Methodology agent schemas for structured output.
"""

from pydantic import BaseModel, Field


class MethodologySchema(BaseModel):
    """
    Schema for methodology analysis output.

    Field names MUST match the keys used in methodology_agent.py output dict.
    """

    research_design: str = Field(
        default="",
        description="Recommended research design approach",
    )
    data_collection_methods: list[str] = Field(
        default_factory=list,
        description="Data collection methods to use",
    )
    sampling_strategy: str = Field(
        default="",
        description="Sampling strategy recommendation",
    )
    analysis_approaches: list[str] = Field(
        default_factory=list,
        description="Analytical approaches for the study",
    )
    validity_measures: list[str] = Field(
        default_factory=list,
        description="Validity and reliability measures",
    )
    ethical_considerations: list[str] = Field(
        default_factory=list,
        description="Ethical considerations for the research",
    )
    limitations: list[str] = Field(
        default_factory=list,
        description="Potential limitations of the methodology",
    )
    timeline: str = Field(
        default="",
        description="Estimated timeline for research completion",
    )
    quality_indicators: list[str] = Field(
        default_factory=list,
        description="Quality indicators for the methodology",
    )
