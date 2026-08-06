"""Claim-support resolution: no material claim escapes without a verdict."""

from .materiality import (
    DELIVERABLE_ARTIFACT_KINDS,
    MATERIALITY_POLICY_ID,
    MATERIALITY_POLICY_VERSION,
    MINIMUM_ASSERTION_WORDS,
    NON_DELIVERABLE_ARTIFACT_KINDS,
    ClaimExclusionReason,
    ClaimInventory,
    ExcludedSegment,
    MaterialClaim,
    SourceSegment,
    UnclassifiedArtifactKindError,
    build_inventory,
    segment_source,
)

__all__ = [
    "DELIVERABLE_ARTIFACT_KINDS",
    "MATERIALITY_POLICY_ID",
    "MATERIALITY_POLICY_VERSION",
    "MINIMUM_ASSERTION_WORDS",
    "NON_DELIVERABLE_ARTIFACT_KINDS",
    "ClaimExclusionReason",
    "ClaimInventory",
    "ExcludedSegment",
    "MaterialClaim",
    "SourceSegment",
    "UnclassifiedArtifactKindError",
    "build_inventory",
    "segment_source",
]
