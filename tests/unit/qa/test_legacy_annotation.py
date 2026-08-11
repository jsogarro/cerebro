"""Tests for the Packet 0 read-path annotation utility.

Rows written by the fabricating code that packet 0 removed (see
``citation_agent._resolve_doi`` and the deleted ``src.qa`` stubs) already
exist in ``agent_task.output_data`` and ``research_result.content``. This
module's job is to mark those rows when they are read, not to retract or
mutate what was persisted.

Coverage caveat (see the packet handoff for the full explanation): this
utility is not wired into a live endpoint by this packet. The endpoint that
actually serves ``research_result.content`` verbatim
(``src/api/routes/research.py::get_research_results``) is outside this
packet's owned files. These tests prove the function's own contract in
isolation.
"""

from src.qa.legacy_annotation import (
    ANNOTATION_KEY,
    ANNOTATION_REASON_KEY,
    annotate_legacy_result,
    annotate_legacy_results,
)


class TestKnownFabricationFingerprints:
    def test_flags_a_legacy_doi_resolution_row(self):
        """Matches the exact shape citation_agent._resolve_doi produced
        before X1: a top-level doi_info-shaped dict with a nested
        crossref_data.publisher of "Mock Publisher"."""
        legacy_row = {
            "resolved": True,
            "doi": "10.1234/example",
            "crossref_data": {
                "status": "verified",
                "publisher": "Mock Publisher",
                "type": "journal-article",
            },
        }

        annotated = annotate_legacy_result(legacy_row)

        assert annotated[ANNOTATION_KEY] is True
        assert "never independently verified" in annotated[ANNOTATION_REASON_KEY]
        # Original fields are preserved, not stripped.
        assert annotated["doi"] == "10.1234/example"

    def test_flags_a_legacy_plagiarism_check_row(self):
        """Matches the shape src.qa.PlagiarismDetector / the deleted
        POST /api/v1/qa/plagiarism-check endpoint produced before X2/X3."""
        legacy_row = {"originality_score": 1.0, "matches": []}

        annotated = annotate_legacy_result(legacy_row)

        assert annotated[ANNOTATION_KEY] is True

    def test_flags_the_fingerprint_when_nested_inside_a_larger_payload(self):
        """agent_task.output_data / research_result.content hold whole
        agent results, not just the fabricated field in isolation -- e.g.
        citation_agent's real output shape nests doi_resolution under a
        source-id key."""
        legacy_row = {
            "formatted_citations": ["Doe, A. (2024). Title."],
            "doi_resolution": {
                "source_0": {
                    "resolved": True,
                    "crossref_data": {"publisher": "Mock Publisher"},
                }
            },
        }

        annotated = annotate_legacy_result(legacy_row)

        assert annotated[ANNOTATION_KEY] is True
        # Unrelated sibling data is untouched.
        assert annotated["formatted_citations"] == ["Doe, A. (2024). Title."]


class TestCleanRowsPassThroughUnchanged:
    def test_a_row_with_no_known_fingerprint_is_not_annotated(self):
        clean_row = {
            "formatted_citations": ["Doe, A. (2024). Title."],
            "citation_style": "APA",
        }

        result = annotate_legacy_result(clean_row)

        assert ANNOTATION_KEY not in result
        assert result == clean_row

    def test_clean_row_is_returned_as_the_same_object_not_copied(self):
        """Cheap-to-call-unconditionally guarantee: no allocation for the
        common case of a row with nothing to flag."""
        clean_row = {"a": 1}

        result = annotate_legacy_result(clean_row)

        assert result is clean_row

    def test_annotating_does_not_mutate_the_caller_s_original_dict(self):
        legacy_row = {"originality_score": 1.0}

        annotated = annotate_legacy_result(legacy_row)

        assert annotated is not legacy_row
        assert ANNOTATION_KEY not in legacy_row

    def test_non_dict_payloads_pass_through_unchanged(self):
        assert annotate_legacy_result(None) is None
        assert annotate_legacy_result([1, 2, 3]) == [1, 2, 3]


class TestBehavioralReadPath:
    def test_a_legacy_row_is_served_with_the_annotation_applied(self):
        """Simulates the read path this utility is meant for: a batch of
        persisted ResearchResult.content payloads, some fabricated under
        the pre-fix code, some produced by other agents and clean."""
        persisted_rows = [
            {
                "source_id": "source_0",
                "doi_resolved": True,
                "doi_info": {
                    "resolved": True,
                    "crossref_data": {"publisher": "Mock Publisher"},
                },
            },
            {"source_id": "source_1", "key_findings": ["A real finding"]},
        ]

        served = annotate_legacy_results(persisted_rows)

        assert served[0][ANNOTATION_KEY] is True
        assert ANNOTATION_KEY not in served[1]
        assert served[1] == persisted_rows[1]
