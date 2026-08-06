"""Quality assurance and evaluation suite."""

from src.qa.mast import (
    ContentHashTracker,
    MASTLabel,
    MASTLabeler,
    MASTLabelingResult,
    format_mast_labels_for_metadata,
)

# Export MAST components for external use.
#
# This module previously also defined a plagiarism-detector class,
# FactExtractionService, FactVerificationService, CitationVerifier,
# BenchmarkEvaluator, PeerReviewSystem, FactCheckResult, and
# CitationVerification — all stubs that returned unconditional
# success/pass-through results (e.g. the originality check always returned
# a fixed "fully original, no matches" verdict regardless of input) rather
# than performing any real check. They had zero callers anywhere in the repo
# and were reachable only because this module re-exported them alongside the
# real MAST components. Removed rather than implemented — none of those
# capabilities exist yet. (See src/qa/legacy_annotation.py and
# tests/unit/qa/test_fabrication_deletion_grep.py for the exact deleted
# identifiers and the strings this repo now asserts are absent from src/.)
__all__ = [
    "ContentHashTracker",
    "MASTLabel",
    "MASTLabeler",
    "MASTLabelingResult",
    "format_mast_labels_for_metadata",
]
