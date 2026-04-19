"""
Pydantic schemas for structured LLM outputs.

These schemas ensure type safety and validation for all LLM-generated responses.
"""

from .citation import CitationSchema
from .literature_review import (
    AcademicSource,
    LiteratureAnalysisSchema,
    SourceValidationResult,
    SourceVerification,
)
from .methodology import MethodologySchema
from .research_paper import PaperReview, ResearchPaper, ReviewFeedback
from .synthesis import SynthesisSchema

__all__ = [
    "AcademicSource",
    "CitationSchema",
    "LiteratureAnalysisSchema",
    "MethodologySchema",
    "PaperReview",
    "ResearchPaper",
    "ReviewFeedback",
    "SourceValidationResult",
    "SourceVerification",
    "SynthesisSchema",
]
