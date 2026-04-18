"""
Synthesis agent schemas for structured output.
"""

from pydantic import BaseModel, Field


class SynthesisSchema(BaseModel):
    """
    Schema for synthesis analysis output.

    Field names MUST match the keys used in synthesis_agent.py output dict.
    """

    integrated_findings: list[str] = Field(
        default_factory=list,
        description="Integrated findings from multiple agents",
    )
    cross_agent_patterns: list[str] = Field(
        default_factory=list,
        description="Patterns identified across different agents",
    )
    conflict_resolutions: list[str] = Field(
        default_factory=list,
        description="Resolutions for conflicting findings",
    )
    meta_insights: list[str] = Field(
        default_factory=list,
        description="Higher-order insights from synthesis",
    )
    comprehensive_narrative: str = Field(
        default="",
        description="Complete synthesis narrative combining all agent outputs",
    )
    confidence_assessment: str = Field(
        default="",
        description="Confidence assessment of the synthesis",
    )
