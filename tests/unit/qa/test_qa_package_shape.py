"""X2 (Packet 0 fabrication deletion): ``src.qa`` must expose only the real
MAST components, not the stub classes that used to be re-exported alongside
them.

The deleted classes (``PlagiarismDetector``, ``FactExtractionService``,
``FactVerificationService``, ``CitationVerifier``, ``BenchmarkEvaluator``,
``PeerReviewSystem``, ``FactCheckResult``, ``CitationVerification``) always
returned unconditional pass/success results regardless of input -- e.g.
``PlagiarismDetector.check_originality`` always returned
``{"originality_score": 1.0, "matches": []}``. They had zero callers in the
repository; they were importable only because ``src/qa/__init__.py``
re-exported them alongside the real MAST re-exports.
"""

import src.qa as qa_module


def test_qa_module_exports_only_mast_components():
    assert qa_module.__all__ == [
        "ContentHashTracker",
        "MASTLabel",
        "MASTLabeler",
        "MASTLabelingResult",
        "format_mast_labels_for_metadata",
    ]


def test_qa_module_no_longer_defines_the_fabricating_stub_classes():
    deleted_symbols = [
        "PlagiarismDetector",
        "FactExtractionService",
        "FactVerificationService",
        "CitationVerifier",
        "BenchmarkEvaluator",
        "PeerReviewSystem",
        "FactCheckResult",
        "CitationVerification",
    ]
    for symbol in deleted_symbols:
        assert not hasattr(qa_module, symbol), (
            f"src.qa still defines {symbol}, which packet 0 was supposed to delete"
        )


def test_qa_module_still_exports_the_real_mast_components():
    """Deleting the stubs must not have collaterally broken the real re-exports."""
    for symbol in qa_module.__all__:
        assert hasattr(qa_module, symbol)
