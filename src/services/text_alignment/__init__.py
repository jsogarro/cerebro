"""Deterministic span alignment over source text.

Credential-free, with no network, filesystem, or subprocess access. Every
result is reproducible from ``(NORMALIZER_VERSION, UNICODE_VERSION)`` and the
inputs.
"""

from . import alignment
from .alignment import (
    NORMALIZER_VERSION,
    SHINGLE_SIZE,
    UNICODE_VERSION,
    ContainmentResult,
    MatchKind,
    NormalizedText,
    SpanMatch,
    containment,
    locate,
    normalize,
)

__all__ = [
    "NORMALIZER_VERSION",
    "SHINGLE_SIZE",
    "UNICODE_VERSION",
    "ContainmentResult",
    "MatchKind",
    "NormalizedText",
    "SpanMatch",
    "alignment",
    "containment",
    "locate",
    "normalize",
]
