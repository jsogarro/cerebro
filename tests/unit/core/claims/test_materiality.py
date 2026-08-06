"""The denominator: which statements in an artifact are material claims.

Wave 7 reads ``n(unsupported) / n(material claims)``. These tests are about the
divisor. Every one of them is written against the same hazard: a claim that
quietly leaves the denominator improves the release number without anything
failing, so the properties worth proving are *coverage* and *disposition*, not
whether any individual sentence was classified the way a reader would.
"""

import hashlib
from itertools import pairwise

import pytest

from src.core.claims import (
    DELIVERABLE_ARTIFACT_KINDS,
    NON_DELIVERABLE_ARTIFACT_KINDS,
    ClaimExclusionReason,
    UnclassifiedArtifactKindError,
    build_inventory,
    segment_source,
)
from src.core.contracts.locators import parse_locator

DELIVERABLE_KIND = "research_report"

MIXED_SOURCE = (
    "The model improves accuracy by 5%.\n\n"
    "## Results\n"
    "Does the improvement hold out of sample?  It does; see Table 3.\n"
    "Latency fell from 210ms to 180ms!\n"
)


UNTERMINATED_SOURCE = "The final sentence carries no terminator and no newline"


def test_the_coverage_sources_exercise_the_shapes_that_break_coverage() -> None:
    """The parametrized cases below are only as good as the inputs they use.

    A mutation that dropped the trailing-remainder branch of the splitter
    survived reassembly equality, because every sample happened to end on a
    boundary and the branch was never reached. So the properties of the sample
    set are asserted here rather than assumed: without an unterminated source,
    the coverage test proves coverage only of sources that end tidily.
    """
    assert "\n\n" in MIXED_SOURCE, "must exercise blank-line runs"
    assert "  " in MIXED_SOURCE, "must exercise repeated spaces"
    assert {".", "?", "!"} <= set(MIXED_SOURCE), "must mix terminators"
    assert not UNTERMINATED_SOURCE[-1].isspace(), "must not end on a boundary"
    assert UNTERMINATED_SOURCE[-1] not in ".!?", "must not end on a terminator"


@pytest.mark.parametrize(
    "source", [MIXED_SOURCE, UNTERMINATED_SOURCE], ids=["mixed", "unterminated"]
)
def test_segmentation_reproduces_the_source_byte_for_byte(source: str) -> None:
    """Coverage is the property, not sentence quality.

    Reassembly equality is what makes "every statement was considered" a
    checkable fact rather than a claim about the splitter — a splitter that
    silently normalized whitespace, or dropped a trailing remainder, cannot
    satisfy it.
    """
    segments = segment_source(source)

    assert "".join(segment.text for segment in segments) == source


@pytest.mark.parametrize(
    "source", [MIXED_SOURCE, UNTERMINATED_SOURCE], ids=["mixed", "unterminated"]
)
def test_segments_are_contiguous_and_non_overlapping(source: str) -> None:
    segments = segment_source(source)

    assert segments[0].start == 0
    assert segments[-1].end == len(source)
    for earlier, later in pairwise(segments):
        assert earlier.end == later.start


def test_every_segment_is_material_or_carries_a_named_exclusion() -> None:
    """There is no third disposition — that is the whole guarantee here."""
    segments = segment_source(MIXED_SOURCE)

    inventory = build_inventory(
        artifact_id="artifact-1",
        artifact_kind=DELIVERABLE_KIND,
        source=MIXED_SOURCE,
    )

    assert len(inventory.claims) + len(inventory.exclusions) == len(segments)
    accounted = [claim.span for claim in inventory.claims]
    accounted += [exclusion.span for exclusion in inventory.exclusions]
    for segment in segments:
        within = [
            span
            for span in accounted
            if segment.start <= span[0] and span[1] <= segment.end
        ]
        assert len(within) == 1, f"segment {segment.start}-{segment.end} unaccounted"


def test_an_ordinary_assertion_is_material() -> None:
    inventory = build_inventory(
        artifact_id="artifact-1",
        artifact_kind=DELIVERABLE_KIND,
        source="The model improves accuracy by 5%.",
    )

    assert [claim.text for claim in inventory.claims] == [
        "The model improves accuracy by 5%."
    ]


def test_an_assertion_matching_no_recognized_pattern_is_still_material() -> None:
    """Materiality is a deny-list, and this is the test that proves it.

    An allow-list policy — "material iff it looks like a claim" — passes every
    test built from well-formed prose while silently dropping the malformed,
    the translated, and the tabular. Those omissions shrink the denominator,
    which is the one direction of error a release gate cannot detect. So the
    subject here is deliberately an assertion no pattern would recognize: no
    subject-verb-object shape, no citation, no number in prose position.
    """
    odd_but_asserted = "throughput :: 4.1k rps sustained, p99 unchanged"
    assert not odd_but_asserted[0].isupper(), "must not read as ordinary prose"
    assert not odd_but_asserted.endswith("."), "must lack a sentence terminator"

    inventory = build_inventory(
        artifact_id="artifact-1",
        artifact_kind=DELIVERABLE_KIND,
        source=odd_but_asserted,
    )

    assert [claim.text for claim in inventory.claims] == [odd_but_asserted]


def test_a_question_is_excluded_as_interrogative() -> None:
    inventory = build_inventory(
        artifact_id="artifact-1",
        artifact_kind=DELIVERABLE_KIND,
        source="Does the improvement hold out of sample?",
    )

    assert inventory.claims == ()
    assert [exclusion.reason for exclusion in inventory.exclusions] == [
        ClaimExclusionReason.INTERROGATIVE
    ]


def test_a_short_fragment_is_excluded_below_the_assertion_length_floor() -> None:
    inventory = build_inventory(
        artifact_id="artifact-1",
        artifact_kind=DELIVERABLE_KIND,
        source="## Results",
    )

    assert inventory.claims == ()
    assert [exclusion.reason for exclusion in inventory.exclusions] == [
        ClaimExclusionReason.BELOW_MINIMUM_ASSERTION_LENGTH
    ]


def test_an_unclassified_artifact_kind_raises_rather_than_emptying_the_denominator() -> (
    None
):
    """A zero denominator and a passing gate are indistinguishable.

    ``n(unsupported) / n(material)`` over an empty inventory is not a failure
    anywhere downstream — it is a clean sheet. So an artifact kind nobody has
    classified must stop the pipeline rather than contribute nothing to it.
    """
    unknown_kind = "unregistered_kind"
    assert unknown_kind not in DELIVERABLE_ARTIFACT_KINDS
    assert unknown_kind not in NON_DELIVERABLE_ARTIFACT_KINDS

    with pytest.raises(UnclassifiedArtifactKindError):
        build_inventory(
            artifact_id="artifact-1",
            artifact_kind=unknown_kind,
            source="The model improves accuracy by 5%.",
        )


def test_a_non_deliverable_artifact_excludes_its_statements_by_name() -> None:
    """Out of scope is recorded per statement, not by returning nothing.

    An empty result would be indistinguishable from "this artifact had no
    prose", and the two call for different responses.
    """
    non_deliverable = sorted(NON_DELIVERABLE_ARTIFACT_KINDS)[0]

    inventory = build_inventory(
        artifact_id="artifact-1",
        artifact_kind=non_deliverable,
        source="The model improves accuracy by 5%.",
    )

    assert inventory.claims == ()
    assert [exclusion.reason for exclusion in inventory.exclusions] == [
        ClaimExclusionReason.NOT_A_DELIVERABLE_ARTIFACT
    ]


def test_claim_ids_are_canonical_locator_spans_and_are_stable() -> None:
    first = build_inventory(
        artifact_id="artifact-1",
        artifact_kind=DELIVERABLE_KIND,
        source=MIXED_SOURCE,
    )
    second = build_inventory(
        artifact_id="artifact-1",
        artifact_kind=DELIVERABLE_KIND,
        source=MIXED_SOURCE,
    )

    assert [claim.claim_id for claim in first.claims] == [
        claim.claim_id for claim in second.claims
    ]
    for claim in first.claims:
        span = parse_locator(claim.claim_id).canonical
        assert span.scheme == "char"
        assert MIXED_SOURCE[span.start : span.end] == claim.text


def test_the_inventory_names_the_exact_source_it_segmented() -> None:
    inventory = build_inventory(
        artifact_id="artifact-1",
        artifact_kind=DELIVERABLE_KIND,
        source=MIXED_SOURCE,
    )

    assert (
        inventory.source_sha256
        == hashlib.sha256(MIXED_SOURCE.encode("utf-8")).hexdigest()
    )


def test_claim_ids_are_unique_within_an_inventory() -> None:
    inventory = build_inventory(
        artifact_id="artifact-1",
        artifact_kind=DELIVERABLE_KIND,
        source=MIXED_SOURCE,
    )

    claim_ids = [claim.claim_id for claim in inventory.claims]
    assert len(claim_ids) == len(set(claim_ids))
    assert len(claim_ids) > 1, "a single-claim inventory cannot show collision"
