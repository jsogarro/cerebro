"""Unit tests for MAST (Multi-Agent System failure Taxonomy) labeling.

Tests rule-based heuristic detection for the top-5 Phase S failure modes:
  FM-1.1: Disobey task specification
  FM-1.3: Step repetition
  FM-1.5: Unaware of termination conditions
  FM-2.6: Reasoning-action mismatch
  FM-3.2: No or incomplete verification

Tests cover positive/negative cases, real historical failures, edge cases,
and the overall labeling workflow.
"""

# ruff: noqa: N801 - Test class names use FM codes (FM11, FM13, etc.) for clarity

import pytest

from src.qa.mast import (
    ContentHashTracker,
    MASTLabel,
    MASTLabeler,
    MASTLabelingResult,
    format_mast_labels_for_metadata,
)


class TestContentHashTracker:
    """Tests for FM-1.3 step repetition detection via content hashing."""

    def test_first_content_not_repeated(self):
        """First content seen should not be flagged as repeated."""
        tracker = ContentHashTracker()
        content = "This is the first worker output"
        assert not tracker.is_repeated(content)

    def test_exact_duplicate_detected(self):
        """Exact duplicate content should be detected."""
        tracker = ContentHashTracker()
        content = "This is a worker output"
        tracker.is_repeated(content)  # First occurrence
        assert tracker.is_repeated(content)  # Duplicate

    def test_whitespace_normalized(self):
        """Whitespace differences should be normalized."""
        tracker = ContentHashTracker()
        content1 = "  Worker output  \n"
        content2 = "Worker output"
        tracker.is_repeated(content1)
        assert tracker.is_repeated(content2)

    def test_empty_content_not_repeated(self):
        """Empty content should not trigger repetition."""
        tracker = ContentHashTracker()
        assert not tracker.is_repeated("")
        assert not tracker.is_repeated("   ")

    def test_reset_clears_history(self):
        """Reset should clear seen hashes."""
        tracker = ContentHashTracker()
        content = "Some content"
        tracker.is_repeated(content)
        assert tracker.is_repeated(content)  # Duplicate

        tracker.reset()
        assert not tracker.is_repeated(content)  # Now fresh

    def test_different_content_not_repeated(self):
        """Different content should not be flagged."""
        tracker = ContentHashTracker()
        tracker.is_repeated("Content A")
        tracker.is_repeated("Content B")
        tracker.is_repeated("Content C")
        assert not tracker.is_repeated("Content D")


class TestMASTLabeler:
    """Tests for overall MAST labeling logic."""

    def test_initialization(self):
        """Labeler should initialize with correct max rounds."""
        labeler = MASTLabeler(max_revision_rounds=3)
        assert labeler.max_revision_rounds == 3
        assert labeler.hash_tracker is not None

    def test_taxonomy_complete(self):
        """All 14 MAST modes should be in taxonomy."""
        assert len(MASTLabeler.MAST_TAXONOMY) == 14
        assert "1.1" in MASTLabeler.MAST_TAXONOMY
        assert "3.3" in MASTLabeler.MAST_TAXONOMY


class TestFM11_TaskSpecViolation:
    """Tests for FM-1.1: Disobey task specification detection."""

    def test_missing_required_field_detected(self):
        """Issue mentioning 'missing required' should trigger FM-1.1."""
        labeler = MASTLabeler()
        issues = ["The output is missing required field 'timestamp'"]
        result = labeler.label_verification_result(
            verdict="revise",
            issues=issues,
            round_num=1,
            content="Some output",
            previous_content=None,
        )
        assert "1.1" in result.detected_modes

    def test_schema_mismatch_detected(self):
        """Issue about schema mismatch should trigger FM-1.1."""
        labeler = MASTLabeler()
        issues = ["Output does not match schema: expected list, got dict"]
        result = labeler.label_verification_result(
            verdict="revise",
            issues=issues,
            round_num=1,
            content="Some output",
        )
        assert "1.1" in result.detected_modes

    def test_cardinality_violation_detected(self):
        """Issue about cardinality (item count) should trigger FM-1.1."""
        labeler = MASTLabeler()
        issues = ["Query returned <2 items but spec requires at least 3"]
        result = labeler.label_verification_result(
            verdict="revise", issues=issues, round_num=1, content="[item1]"
        )
        assert "1.1" in result.detected_modes

    def test_empty_query_violation_detected(self):
        """Real historical failure: 'Query cannot be empty' should trigger FM-1.1."""
        labeler = MASTLabeler()
        issues = ["Query cannot be empty per task specification"]
        result = labeler.label_verification_result(
            verdict="revise", issues=issues, round_num=1, content=""
        )
        assert "1.1" in result.detected_modes

    def test_fewer_than_required_detected(self):
        """Real historical failure: '<2 items' should trigger FM-1.1."""
        labeler = MASTLabeler()
        issues = ["Worker returned <2 items when supervisor expected list of 5"]
        result = labeler.label_verification_result(
            verdict="revise", issues=issues, round_num=1, content="[single_item]"
        )
        assert "1.1" in result.detected_modes

    def test_no_spec_violation_when_other_issues(self):
        """Non-spec issues should NOT trigger FM-1.1."""
        labeler = MASTLabeler()
        issues = ["The reasoning is circular", "Tone could be more professional"]
        result = labeler.label_verification_result(
            verdict="revise", issues=issues, round_num=1, content="Some output"
        )
        assert "1.1" not in result.detected_modes


class TestFM13_StepRepetition:
    """Tests for FM-1.3: Step repetition detection."""

    def test_exact_repetition_detected(self):
        """Identical content across rounds should trigger FM-1.3."""
        labeler = MASTLabeler()
        content = "Worker output round 1"
        result = labeler.label_verification_result(
            verdict="revise",
            issues=["Some issue"],
            round_num=2,
            content=content,
            previous_content=content,
        )
        assert "1.3" in result.detected_modes

    def test_no_repetition_on_first_round(self):
        """First round should never trigger FM-1.3."""
        labeler = MASTLabeler()
        content = "Worker output"
        result = labeler.label_verification_result(
            verdict="revise",
            issues=["Some issue"],
            round_num=1,
            content=content,
            previous_content=None,
        )
        assert "1.3" not in result.detected_modes

    def test_different_content_not_repeated(self):
        """Different content across rounds should NOT trigger FM-1.3."""
        labeler = MASTLabeler()
        result = labeler.label_verification_result(
            verdict="revise",
            issues=["Issue"],
            round_num=2,
            content="New improved output",
            previous_content="Original output",
        )
        assert "1.3" not in result.detected_modes

    def test_whitespace_changes_not_count_as_different(self):
        """Whitespace-only differences should still trigger FM-1.3."""
        labeler = MASTLabeler()
        result = labeler.label_verification_result(
            verdict="revise",
            issues=["Issue"],
            round_num=2,
            content="Worker output",
            previous_content="  Worker output  \n",
        )
        assert "1.3" in result.detected_modes

    def test_hash_tracker_detects_global_repetition(self):
        """Hash tracker should detect repetition even without previous_content."""
        labeler = MASTLabeler(
            max_revision_rounds=5
        )  # High enough to not trigger FM-1.5
        content = "Some output"

        # First call (round 1)
        labeler.label_verification_result(
            verdict="revise", issues=[], round_num=1, content=content
        )

        # Second call (round 2) - should detect via hash tracker
        result = labeler.label_verification_result(
            verdict="revise",
            issues=[],
            round_num=2,
            content=content,
            previous_content=None,  # Not provided
        )
        assert "1.3" in result.detected_modes


class TestFM15_NoTerminationAwareness:
    """Tests for FM-1.5: Unaware of termination conditions detection."""

    def test_revise_at_max_rounds_triggers(self):
        """REVISE verdict at max rounds should trigger FM-1.5."""
        labeler = MASTLabeler(max_revision_rounds=2)
        result = labeler.label_verification_result(
            verdict="revise",  # Still revising at limit
            issues=["Still has issues"],
            round_num=2,  # At max
            content="Output",
        )
        assert "1.5" in result.detected_modes

    def test_pass_at_max_rounds_does_not_trigger(self):
        """PASS verdict at max rounds should NOT trigger FM-1.5."""
        labeler = MASTLabeler(max_revision_rounds=2)
        result = labeler.label_verification_result(
            verdict="pass",  # Passed before limit
            issues=[],
            round_num=2,
            content="Output",
        )
        assert "1.5" not in result.detected_modes

    def test_revise_before_max_does_not_trigger(self):
        """REVISE before max rounds should NOT trigger FM-1.5."""
        labeler = MASTLabeler(max_revision_rounds=3)
        result = labeler.label_verification_result(
            verdict="revise",
            issues=["Issue"],
            round_num=2,  # Not at max yet
            content="Output",
        )
        assert "1.5" not in result.detected_modes

    def test_different_max_rounds_config(self):
        """FM-1.5 detection should respect configured max rounds."""
        labeler = MASTLabeler(max_revision_rounds=5)
        result = labeler.label_verification_result(
            verdict="revise", issues=["Issue"], round_num=5, content="Output"
        )
        assert "1.5" in result.detected_modes

        result2 = labeler.label_verification_result(
            verdict="revise", issues=["Issue"], round_num=4, content="Output"
        )
        assert "1.5" not in result2.detected_modes


class TestFM26_ReasoningActionMismatch:
    """Tests for FM-2.6: Reasoning-action mismatch detection."""

    def test_claims_but_pattern_detected(self):
        """Issue with 'claims X but Y' pattern should trigger FM-2.6."""
        labeler = MASTLabeler()
        issues = ["Worker claims to extract 5 sources but only returns 2"]
        result = labeler.label_verification_result(
            verdict="revise", issues=issues, round_num=1, content="Output"
        )
        assert "2.6" in result.detected_modes

    def test_states_however_pattern_detected(self):
        """Issue with 'states X however Y' pattern should trigger FM-2.6."""
        labeler = MASTLabeler()
        issues = [
            "Agent states it will analyze data, however output contains no analysis"
        ]
        result = labeler.label_verification_result(
            verdict="revise", issues=issues, round_num=1, content="Output"
        )
        assert "2.6" in result.detected_modes

    def test_says_actually_pattern_detected(self):
        """Issue with 'says X actually Y' pattern should trigger FM-2.6."""
        labeler = MASTLabeler()
        issues = ["Worker says it processed 100 records but actually only processed 10"]
        result = labeler.label_verification_result(
            verdict="revise", issues=issues, round_num=1, content="Output"
        )
        assert "2.6" in result.detected_modes

    def test_no_mismatch_when_only_one_keyword(self):
        """Issue with only one keyword should NOT trigger FM-2.6."""
        labeler = MASTLabeler()
        issues = ["Worker claims the results are correct"]  # No mismatch signal
        result = labeler.label_verification_result(
            verdict="revise", issues=issues, round_num=1, content="Output"
        )
        assert "2.6" not in result.detected_modes


class TestFM32_IncompleteVerification:
    """Tests for FM-3.2: No or incomplete verification detection."""

    def test_missing_keyword_detected(self):
        """Issue mentioning 'missing' should trigger FM-3.2."""
        labeler = MASTLabeler()
        issues = ["Analysis is missing key citations"]
        result = labeler.label_verification_result(
            verdict="revise", issues=issues, round_num=1, content="Output"
        )
        assert "3.2" in result.detected_modes

    def test_incomplete_keyword_detected(self):
        """Issue mentioning 'incomplete' should trigger FM-3.2."""
        labeler = MASTLabeler()
        issues = ["Verification is incomplete; missing test coverage section"]
        result = labeler.label_verification_result(
            verdict="revise", issues=issues, round_num=1, content="Output"
        )
        assert "3.2" in result.detected_modes

    def test_partial_keyword_detected(self):
        """Issue mentioning 'partial' should trigger FM-3.2."""
        labeler = MASTLabeler()
        issues = ["Worker provided only partial results"]
        result = labeler.label_verification_result(
            verdict="revise", issues=issues, round_num=1, content="Output"
        )
        assert "3.2" in result.detected_modes

    def test_lacks_keyword_detected(self):
        """Issue mentioning 'lacks' should trigger FM-3.2."""
        labeler = MASTLabeler()
        issues = ["Output lacks required evidence"]
        result = labeler.label_verification_result(
            verdict="revise", issues=issues, round_num=1, content="Output"
        )
        assert "3.2" in result.detected_modes

    def test_not_provided_detected(self):
        """Issue mentioning 'not provided' should trigger FM-3.2."""
        labeler = MASTLabeler()
        issues = ["Citations were not provided"]
        result = labeler.label_verification_result(
            verdict="revise", issues=issues, round_num=1, content="Output"
        )
        assert "3.2" in result.detected_modes


class TestMultipleModes:
    """Tests for cases where multiple failure modes are present."""

    def test_spec_violation_and_incomplete(self):
        """Both FM-1.1 and FM-3.2 can be detected together."""
        labeler = MASTLabeler()
        issues = [
            "Output is missing required field 'timestamp'",  # FM-1.1
            "Analysis section is incomplete",  # FM-3.2
        ]
        result = labeler.label_verification_result(
            verdict="revise", issues=issues, round_num=1, content="Output"
        )
        assert "1.1" in result.detected_modes
        assert "3.2" in result.detected_modes
        assert len(result.labels) == 2

    def test_repetition_and_no_termination(self):
        """FM-1.3 and FM-1.5 can be detected together."""
        labeler = MASTLabeler(max_revision_rounds=2)
        content = "Same output"
        result = labeler.label_verification_result(
            verdict="revise",
            issues=["Issue"],
            round_num=2,  # At max
            content=content,
            previous_content=content,  # Repeated
        )
        assert "1.3" in result.detected_modes
        assert "1.5" in result.detected_modes

    def test_all_five_modes_detected(self):
        """All 5 Phase S modes can be detected in one result."""
        labeler = MASTLabeler(max_revision_rounds=2)
        issues = [
            "Missing required fields",  # FM-1.1
            "Claims 5 sources but returns 2",  # FM-2.6
            "Analysis is incomplete",  # FM-3.2
        ]
        content = "Repeated output"
        result = labeler.label_verification_result(
            verdict="revise",
            issues=issues,
            round_num=2,  # At max → FM-1.5
            content=content,
            previous_content=content,  # Repeated → FM-1.3
        )
        assert len(result.detected_modes) == 5
        assert all(
            mode in result.detected_modes
            for mode in ["1.1", "1.3", "1.5", "2.6", "3.2"]
        )


class TestLabelingResult:
    """Tests for MASTLabelingResult structure."""

    def test_result_structure(self):
        """Result should have expected structure."""
        labeler = MASTLabeler()
        result = labeler.label_verification_result(
            verdict="pass", issues=[], round_num=1, content="Output"
        )
        assert isinstance(result, MASTLabelingResult)
        assert isinstance(result.labels, list)
        assert isinstance(result.detected_modes, list)
        assert isinstance(result.confidence, float)
        assert isinstance(result.revision_round, int)

    def test_confidence_is_max_of_labels(self):
        """Overall confidence should be max of individual label confidences."""
        labeler = MASTLabeler()
        issues = [
            "Missing required field",  # FM-1.1 with confidence 0.85
            "Analysis incomplete",  # FM-3.2 with confidence 0.75
        ]
        result = labeler.label_verification_result(
            verdict="revise", issues=issues, round_num=1, content="Output"
        )
        assert result.confidence == max(label.confidence for label in result.labels)
        assert result.confidence == pytest.approx(0.85, abs=0.01)

    def test_empty_result_for_pass_verdict(self):
        """PASS verdict with no issues should have empty labels."""
        labeler = MASTLabeler()
        result = labeler.label_verification_result(
            verdict="pass", issues=[], round_num=1, content="Output"
        )
        assert len(result.labels) == 0
        assert len(result.detected_modes) == 0
        assert result.confidence == 0.0


class TestFormatMastLabelsForMetadata:
    """Tests for metadata formatting helper."""

    def test_format_with_labels(self):
        """Should format labels into metadata dict."""
        label1 = MASTLabel(mode="1.1", confidence=0.85, evidence="Test evidence 1")
        label2 = MASTLabel(mode="3.2", confidence=0.75, evidence="Test evidence 2")
        result = MASTLabelingResult(
            labels=[label1, label2],
            detected_modes=["1.1", "3.2"],
            confidence=0.85,
            revision_round=2,
        )

        metadata = format_mast_labels_for_metadata(result)

        assert metadata["mast_failures"] == ["1.1", "3.2"]
        assert metadata["mast_confidence"] == 0.85
        assert len(metadata["mast_labels_detail"]) == 2
        assert metadata["mast_labels_detail"][0]["mode"] == "1.1"
        assert metadata["mast_labels_detail"][0]["confidence"] == 0.85
        assert "Disobey task specification" in metadata["mast_labels_detail"][0]["name"]

    def test_format_empty_result(self):
        """Should format empty result correctly."""
        result = MASTLabelingResult(
            labels=[], detected_modes=[], confidence=0.0, revision_round=1
        )
        metadata = format_mast_labels_for_metadata(result)

        assert metadata["mast_failures"] == []
        assert metadata["mast_confidence"] == 0.0
        assert metadata["mast_labels_detail"] == []


class TestRealHistoricalFailures:
    """Tests using actual historical failures from cerebro."""

    def test_verification_query_cannot_be_empty(self):
        """Real failure: VerificationAgent 'Query cannot be empty'."""
        labeler = MASTLabeler()
        issues = ["Query cannot be empty per task specification"]
        result = labeler.label_verification_result(
            verdict="revise",
            issues=issues,
            round_num=1,
            content="",  # Empty content
        )
        # Should detect FM-1.1 (task spec violation)
        assert "1.1" in result.detected_modes
        assert result.confidence > 0.7

    def test_comparative_fewer_than_two_items(self):
        """Real failure: Comparative supervisor '<2 items' violation."""
        labeler = MASTLabeler()
        issues = [
            "Worker returned <2 items but comparative analysis requires at least 2"
        ]
        result = labeler.label_verification_result(
            verdict="revise",
            issues=issues,
            round_num=1,
            content='{"items": [{"single": "item"}]}',
        )
        # Should detect FM-1.1 (task spec violation)
        assert "1.1" in result.detected_modes
        assert result.confidence > 0.7


class TestEdgeCases:
    """Edge cases and robustness tests."""

    def test_empty_issues_list(self):
        """Empty issues list should not crash."""
        labeler = MASTLabeler()
        result = labeler.label_verification_result(
            verdict="pass", issues=[], round_num=1, content="Output"
        )
        assert len(result.detected_modes) == 0

    def test_none_previous_content(self):
        """None previous_content should not crash."""
        labeler = MASTLabeler()
        result = labeler.label_verification_result(
            verdict="revise",
            issues=["Issue"],
            round_num=2,
            content="Output",
            previous_content=None,
        )
        # Should not trigger FM-1.3 without previous content
        assert isinstance(result, MASTLabelingResult)

    def test_very_long_issue_text(self):
        """Very long issue text should be truncated in evidence."""
        labeler = MASTLabeler()
        long_issue = "Missing required field" + " extra detail" * 100
        issues = [long_issue]
        result = labeler.label_verification_result(
            verdict="revise", issues=issues, round_num=1, content="Output"
        )
        assert "1.1" in result.detected_modes
        # Evidence should be truncated (check implementation truncates to 100 chars)
        for label in result.labels:
            assert len(label.evidence) <= 150  # Reasonable upper bound

    def test_case_insensitive_keyword_matching(self):
        """Keyword matching should be case-insensitive."""
        labeler = MASTLabeler()
        issues = ["MISSING REQUIRED FIELD"]  # Uppercase
        result = labeler.label_verification_result(
            verdict="revise", issues=issues, round_num=1, content="Output"
        )
        assert "1.1" in result.detected_modes

    def test_multiple_keywords_in_single_issue(self):
        """Single issue with multiple keywords should trigger multiple modes."""
        labeler = MASTLabeler()
        issues = [
            "Output is missing required fields and claims 5 items but returns 2"
            # Should trigger both FM-1.1 (missing required) and FM-2.6 (claims/but)
        ]
        result = labeler.label_verification_result(
            verdict="revise", issues=issues, round_num=1, content="Output"
        )
        assert "1.1" in result.detected_modes
        assert "2.6" in result.detected_modes


class TestQAGateMASTWiring:
    """MAST labels must be attached on the production QA-gate path.

    Regression guard: labeling was originally wired only into
    _run_worker_with_verification_loop, which has no production callers.
    The real path is BaseSupervisor._run_verification, called by every
    supervisor's confidence/verification phase.
    """

    @pytest.mark.asyncio
    async def test_run_verification_returns_mast_keys_on_empty_content(self):
        from unittest.mock import MagicMock

        from src.agents.supervisors.analytics_supervisor import AnalyticsSupervisor

        supervisor = AnalyticsSupervisor(
            gemini_service=MagicMock(), cache_client=MagicMock()
        )
        result = await supervisor._run_verification("")
        assert "mast_labels" in result
        assert "mast_confidence" in result
        assert isinstance(result["mast_labels"], list)

    @pytest.mark.asyncio
    async def test_run_verification_returns_mast_keys_on_error_fallback(self):
        """Even the graceful-degradation path must carry MAST keys."""
        from unittest.mock import MagicMock, patch

        from src.agents.supervisors.analytics_supervisor import AnalyticsSupervisor

        supervisor = AnalyticsSupervisor(
            gemini_service=MagicMock(), cache_client=MagicMock()
        )
        with patch(
            "src.agents.factory.AgentFactory.create_agent",
            side_effect=RuntimeError("boom"),
        ):
            result = await supervisor._run_verification("some content")
        assert result["verdict"] == "pass"
        assert "mast_labels" in result
        assert "mast_confidence" in result

    def test_build_supervision_result_wires_labels_from_verification(self):
        """Labels stored in worker_results['verification'] must reach
        supervision_metadata via _store_mast_labels_in_state."""
        from unittest.mock import MagicMock

        from src.agents.supervisors.analytics_supervisor import AnalyticsSupervisor

        supervisor = AnalyticsSupervisor(
            gemini_service=MagicMock(), cache_client=MagicMock()
        )
        state = MagicMock()
        state.supervision_metadata = {}
        verification = {
            "verdict": "revise",
            "issues": ["missing required field"],
            "mast_labels": ["1.1"],
            "mast_confidence": 0.7,
        }
        supervisor._store_mast_labels_in_state(state, verification)
        assert state.supervision_metadata["mast_failures"] == ["1.1"]
        assert state.supervision_metadata["mast_confidence"] == 0.7
