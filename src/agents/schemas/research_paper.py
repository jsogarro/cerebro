"""
Research paper schemas for graduate-level paper drafting and review.

These schemas support the paper drafting → review → revision workflow,
ensuring academic quality and proper formatting.
"""

from pydantic import BaseModel, Field


class ReviewFeedback(BaseModel):
    """Feedback from a graduate-level reviewer."""

    section: str = Field(description="Section being reviewed")
    score: float = Field(ge=0.0, le=10.0, description="Quality score 1-10")
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)


class PaperReview(BaseModel):
    """Complete review of a research paper."""

    overall_score: float = Field(ge=0.0, le=10.0, description="Overall quality 1-10")
    meets_graduate_standard: bool = Field(
        description="Whether paper meets graduate-level quality"
    )
    section_reviews: list[ReviewFeedback] = Field(default_factory=list)
    general_feedback: str = Field(description="Overall assessment and recommendations")
    critical_issues: list[str] = Field(
        default_factory=list, description="Issues that MUST be addressed"
    )


class ResearchPaper(BaseModel):
    """A complete graduate-level research paper."""

    title: str = Field(description="Paper title")
    abstract: str = Field(
        description="150-300 word abstract summarizing the research"
    )
    introduction: str = Field(
        description="Introduction establishing context, significance, and research questions"
    )
    literature_review: str = Field(
        description="Critical analysis of existing research, not just a summary"
    )
    methodology: str = Field(description="Detailed methodology section")
    findings: str = Field(
        description="Key findings presented with evidence and analysis"
    )
    discussion: str = Field(
        description="Interpretation of findings, implications, limitations"
    )
    conclusion: str = Field(
        description="Summary of contributions and future research directions"
    )
    references: list[str] = Field(
        default_factory=list, description="Formatted reference list"
    )
    review_history: list[PaperReview] = Field(
        default_factory=list, description="Reviews received"
    )
    revision_count: int = Field(default=0, description="Number of revisions made")


__all__ = ["PaperReview", "ResearchPaper", "ReviewFeedback"]
