"""Read-path annotation for pre-fix fabricated verification metadata.

Wave 4 packet 0 removed the code paths that fabricated verification results
(``citation_agent._resolve_doi``'s "Mock Publisher" success branch, and the
``PlagiarismDetector``/``BenchmarkEvaluator``/etc. stubs formerly re-exported
from ``src.qa``). Rows written *before* the fix still exist in
``agent_task.output_data`` and ``research_result.content`` — JSON columns
that hold agent output verbatim — and any endpoint that serves those columns
keeps returning the fabricated content unchanged. Deleting the code that
*produces* new fabricated results does not retract results already
persisted; without marking those rows, "we removed the fabricated
verification" would itself be an unsubstantiated claim about what a caller
of the read path actually sees.

This module provides a pure annotation function for that read path. See the
Packet 0 handoff for which endpoint(s) would need to call it and why that is
outside this packet's owned files.
"""

from __future__ import annotations

from typing import Any

ANNOTATION_KEY = "unsubstantiated_legacy_result"
ANNOTATION_REASON_KEY = "unsubstantiated_legacy_result_reason"

_ANNOTATION_REASON = (
    "This result was persisted before verification fabrication was removed "
    "from the agents that produced it (see citation_agent._resolve_doi and "
    "the stub classes formerly re-exported from src.qa) and was never "
    "independently verified. Its content should not be treated as evidence "
    "of anything the labels inside it claim."
)

_MAX_SCAN_DEPTH = 6


def _contains_known_fabrication_fingerprint(value: Any, depth: int = 0) -> bool:
    """Recursively search for the exact markers the removed code emitted.

    Matches only the specific fabricated shapes packet 0 deleted:

    - ``{"publisher": "Mock Publisher"}`` (citation_agent._resolve_doi's
      fabricated ``crossref_data``, at any nesting depth)
    - an ``originality_score`` key present anywhere (src.qa's deleted
      ``PlagiarismDetector.check_originality`` and the deleted
      ``POST /api/v1/qa/plagiarism-check`` route both returned this
      unconditionally)

    This is deliberately narrow — it flags known lies, not anything that
    merely looks unverified. Depth is bounded because ``research_result
    .content`` and ``agent_task.output_data`` are agent-authored JSON with
    a small number of fixed shapes, not arbitrary user-supplied recursive
    structures; 6 levels comfortably covers every shape these agents
    produce today, and bounding it keeps this scan from being unbounded
    work on a pathological payload.
    """
    if depth > _MAX_SCAN_DEPTH:
        return False

    if isinstance(value, dict):
        if "originality_score" in value:
            return True
        if value.get("publisher") == "Mock Publisher":
            return True
        return any(
            _contains_known_fabrication_fingerprint(child, depth + 1)
            for child in value.values()
        )

    if isinstance(value, list):
        return any(
            _contains_known_fabrication_fingerprint(child, depth + 1) for child in value
        )

    return False


def annotate_legacy_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Return ``payload``, annotated if it carries a known pre-fix fabrication.

    ``payload`` is treated as read-only agent output (typically the value of
    ``agent_task.output_data`` or ``research_result.content``). A shallow
    copy is returned when annotating, so the caller's original dict — which
    may be backed by an ORM instance's JSON column — is never mutated in
    place. Payloads with no known fabrication fingerprint are returned
    unchanged (same object, not copied), so this is cheap to call
    unconditionally on every row.

    Non-dict input (``None``, a list, a scalar) is returned unchanged: only
    dict-shaped agent output can carry the fingerprints this function knows
    about, and the read path should decide separately how to handle
    malformed rows.
    """
    if not isinstance(payload, dict):
        return payload

    if not _contains_known_fabrication_fingerprint(payload):
        return payload

    annotated = dict(payload)
    annotated[ANNOTATION_KEY] = True
    annotated[ANNOTATION_REASON_KEY] = _ANNOTATION_REASON
    return annotated


def annotate_legacy_results(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply :func:`annotate_legacy_result` across a list of persisted rows."""
    return [annotate_legacy_result(payload) for payload in payloads]
