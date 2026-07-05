"""MAST (Multi-Agent System failure Taxonomy) labeling for cerebro QA gate.

Phase S: Rule-based heuristic labelers for top-5 failure modes:
  FM-1.1: Disobey task specification
  FM-1.3: Step repetition
  FM-1.5: Unaware of termination conditions
  FM-2.6: Reasoning-action mismatch
  FM-3.2: No or incomplete verification

Based on MAST taxonomy from Cemri et al. (arXiv:2503.13657).
14 modes across 3 clusters: FC1 (System Design), FC2 (Inter-Agent Misalignment),
FC3 (Task Verification). This module implements deterministic (zero-cost) labeling.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class MASTLabel:
    """Represents a single MAST failure mode label with confidence."""

    mode: str  # e.g. "1.1", "1.3", "2.6"
    confidence: float  # 0.0 to 1.0
    evidence: str  # Human-readable justification for the label


@dataclass(frozen=True, slots=True)
class MASTLabelingResult:
    """Result of MAST labeling with all detected modes."""

    labels: list[MASTLabel]
    detected_modes: list[str]  # Convenience: just the mode codes
    confidence: float  # Overall confidence (max across all labels)
    revision_round: int


class ContentHashTracker:
    """Tracks content hashes to detect FM-1.3 step repetition."""

    def __init__(self) -> None:
        self._seen_hashes: set[str] = set()

    def is_repeated(self, content: str) -> bool:
        """
        Check if content hash was seen before.

        Args:
            content: Worker output content to check

        Returns:
            True if this exact content was already processed
        """
        if not content or not content.strip():
            return False

        h = hashlib.sha256(content.strip().encode()).hexdigest()[:16]
        if h in self._seen_hashes:
            return True
        self._seen_hashes.add(h)
        return False

    def reset(self) -> None:
        """Clear the hash history (e.g., for a new worker)."""
        self._seen_hashes.clear()


class MASTLabeler:
    """Rule-based MAST failure mode labeler (Phase S: heuristics only)."""

    # MAST taxonomy mapping (for reference and logging)
    MAST_TAXONOMY = {
        "1.1": "Disobey task specification",
        "1.2": "Disobey role specification",
        "1.3": "Step repetition",
        "1.4": "Loss of conversation history",
        "1.5": "Unaware of termination conditions",
        "2.1": "Conversation reset",
        "2.2": "Fail to ask for clarification",
        "2.3": "Task derailment",
        "2.4": "Information withholding",
        "2.5": "Ignored other agent's input",
        "2.6": "Reasoning-action mismatch",
        "3.1": "Premature termination",
        "3.2": "No or incomplete verification",
        "3.3": "Incorrect verification",
    }

    # Detector keyword lists (lowercase; matched against casefolded issues)
    SPEC_VIOLATION_KEYWORDS = (
        "missing required",
        "does not match schema",
        "cardinality",
        "expected format",
        "expected schema",
        "expected output",
        "must contain",
        "cannot be empty",
        "<2 items",
        "fewer than",
    )
    MISMATCH_KEYWORD_PAIRS = (
        ("claims", "but"),
        ("states", "however"),
        ("says", "actually"),
        ("declares", "produces"),
    )
    INCOMPLETE_KEYWORDS = (
        "missing",
        "incomplete",
        "partial",
        "lacks",
        "absent",
        "not provided",
    )

    def __init__(self, max_revision_rounds: int = 2):
        """
        Initialize the MAST labeler.

        Args:
            max_revision_rounds: Maximum verification revision rounds configured
        """
        self.max_revision_rounds = max_revision_rounds
        self.hash_tracker = ContentHashTracker()

    def label_verification_result(
        self,
        verdict: str,
        issues: Sequence[str],
        round_num: int,
        content: str,
        previous_content: str | None = None,
    ) -> MASTLabelingResult:
        """
        Apply rule-based MAST labeling to a verification result.

        Args:
            verdict: "pass" or "revise"
            issues: List of issues from verification report
            round_num: Current revision round (1-indexed)
            content: Current worker output content
            previous_content: Previous round's content (for repetition detection)

        Returns:
            MASTLabelingResult with detected failure modes
        """
        labels: list[MASTLabel] = []

        # FM-1.3: Step repetition
        if self._detect_step_repetition(content, previous_content):
            labels.append(
                MASTLabel(
                    mode="1.3",
                    confidence=1.0,  # Deterministic: exact hash match
                    evidence="Worker output identical to previous revision round",
                )
            )

        # FM-1.1: Task specification violation
        spec_label = self._detect_task_spec_violation(issues)
        if spec_label:
            labels.append(spec_label)

        # FM-1.5: No termination awareness
        if self._detect_no_termination_awareness(verdict, round_num):
            labels.append(
                MASTLabel(
                    mode="1.5",
                    confidence=0.9,
                    evidence=(
                        f"REVISE verdict at max rounds "
                        f"({round_num}/{self.max_revision_rounds})"
                    ),
                )
            )

        # FM-2.6: Reasoning-action mismatch
        mismatch_label = self._detect_reasoning_action_mismatch(issues)
        if mismatch_label:
            labels.append(mismatch_label)

        # FM-3.2: Incomplete verification
        incomplete_label = self._detect_incomplete_verification(issues)
        if incomplete_label:
            labels.append(incomplete_label)

        # Compute overall confidence and detected modes
        detected_modes = [label.mode for label in labels]
        overall_confidence = max((label.confidence for label in labels), default=0.0)

        if labels:
            logger.info(
                "mast_failure_labeled",
                modes=detected_modes,
                confidence=overall_confidence,
                round=round_num,
                verdict=verdict,
            )

        return MASTLabelingResult(
            labels=labels,
            detected_modes=detected_modes,
            confidence=overall_confidence,
            revision_round=round_num,
        )

    def _detect_step_repetition(
        self, content: str, previous_content: str | None
    ) -> bool:
        """
        FM-1.3: Step repetition (content identical to previous round).

        Uses hash-based exact match. Future Phase M could add fuzzy similarity.
        """
        # Always check hash tracker (global repetition across all rounds)
        is_hash_repeated = self.hash_tracker.is_repeated(content)

        # Also check previous_content if provided (adjacent round repetition)
        if previous_content and content.strip() == previous_content.strip():
            return True

        return is_hash_repeated

    def _detect_task_spec_violation(self, issues: Sequence[str]) -> MASTLabel | None:
        """
        FM-1.1: Disobey task specification.

        Detects keywords in issues that suggest output doesn't match spec.
        """
        for issue in issues:
            issue_folded = issue.casefold()
            if any(kw in issue_folded for kw in self.SPEC_VIOLATION_KEYWORDS):
                return MASTLabel(
                    mode="1.1",
                    confidence=0.85,
                    evidence=f"Issue suggests spec violation: {issue[:100]}",
                )

        return None

    def _detect_no_termination_awareness(self, verdict: str, round_num: int) -> bool:
        """
        FM-1.5: Unaware of termination conditions.

        Triggered when max rounds are hit with REVISE verdict.
        """
        return verdict == "revise" and round_num >= self.max_revision_rounds

    def _detect_reasoning_action_mismatch(
        self, issues: Sequence[str]
    ) -> MASTLabel | None:
        """
        FM-2.6: Reasoning-action mismatch.

        Detects issues mentioning discrepancies between what was claimed vs done.
        """
        for issue in issues:
            issue_folded = issue.casefold()
            if any(
                all(kw in issue_folded for kw in pair)
                for pair in self.MISMATCH_KEYWORD_PAIRS
            ):
                return MASTLabel(
                    mode="2.6",
                    confidence=0.8,
                    evidence=f"Issue suggests reasoning-action mismatch: {issue[:100]}",
                )

        return None

    def _detect_incomplete_verification(
        self, issues: Sequence[str]
    ) -> MASTLabel | None:
        """
        FM-3.2: No or incomplete verification.

        Detects issues mentioning missing or partial content.
        """
        for issue in issues:
            issue_folded = issue.casefold()
            if any(kw in issue_folded for kw in self.INCOMPLETE_KEYWORDS):
                return MASTLabel(
                    mode="3.2",
                    confidence=0.75,
                    evidence=f"Issue suggests incomplete verification: {issue[:100]}",
                )

        return None

    def reset_hash_tracker(self) -> None:
        """Reset the hash tracker (for new worker or task)."""
        self.hash_tracker.reset()


def format_mast_labels_for_metadata(result: MASTLabelingResult) -> dict[str, Any]:
    """
    Convert MASTLabelingResult to metadata dict for AgentResult storage.

    Args:
        result: MAST labeling result

    Returns:
        Dict with mast_failures, mast_confidence, mast_labels_detail
    """
    return {
        "mast_failures": result.detected_modes,
        "mast_confidence": result.confidence,
        "mast_labels_detail": [
            {
                "mode": label.mode,
                "name": MASTLabeler.MAST_TAXONOMY.get(label.mode, "Unknown"),
                "confidence": label.confidence,
                "evidence": label.evidence,
            }
            for label in result.labels
        ],
    }


__all__ = [
    "ContentHashTracker",
    "MASTLabel",
    "MASTLabeler",
    "MASTLabelingResult",
    "format_mast_labels_for_metadata",
]
