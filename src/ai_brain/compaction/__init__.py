"""Context Compaction Module

Implements context compaction strategies to reduce token usage while preserving
critical information and constraints.

Modules:
    constraint_registry: Extract and re-inject critical constraints
"""

from .constraint_registry import (
    ConstraintExtractionResult,
    ConstraintRegistry,
    ConstraintType,
    ExtractedConstraint,
)

__all__ = [
    "ConstraintExtractionResult",
    "ConstraintRegistry",
    "ConstraintType",
    "ExtractedConstraint",
]
