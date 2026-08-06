"""Evidence locators must resolve to the same span forever, in any process.

A locator is only worth persisting if re-opening a terminal run in a new
database session resolves it to the same bytes of the same immutable snapshot.
That rules out anything evaluated against a live document, anything that
depends on a parser version, and any two spellings of the same span.
"""

import pytest

from src.core.contracts.locators import (
    LOCATOR_ANNOTATION_SCHEMES,
    LOCATOR_CANONICAL_SCHEMES,
    InvalidLocatorError,
    canonical_span,
    parse_locator,
)


def test_a_canonical_byte_span_parses_to_a_half_open_range() -> None:
    locator = parse_locator("bytes:1024-2048")

    assert locator.canonical.scheme == "bytes"
    assert locator.canonical.start == 1024
    assert locator.canonical.end == 2048
    assert locator.annotations == ()


def test_canonical_and_annotation_scheme_sets_are_disjoint_and_closed() -> None:
    assert frozenset({"bytes", "char", "line"}) == LOCATOR_CANONICAL_SCHEMES
    assert not (LOCATOR_CANONICAL_SCHEMES & LOCATOR_ANNOTATION_SCHEMES)


def test_the_first_segment_must_be_a_parser_free_canonical_span() -> None:
    with pytest.raises(InvalidLocatorError, match="canonical span scheme"):
        parse_locator("xpath:/html/body/p[2]")


def test_annotations_follow_the_canonical_span_and_are_not_authoritative() -> None:
    locator = parse_locator("bytes:0-64|xpath:/html/body/div[3]/p[2]|page:7")

    assert locator.canonical.scheme == "bytes"
    assert [segment.scheme for segment in locator.annotations] == ["xpath", "page"]


def test_unknown_schemes_are_rejected_rather_than_carried_along() -> None:
    with pytest.raises(InvalidLocatorError, match="unknown locator scheme"):
        parse_locator("bytes:0-64|selenium:by-id")


def test_an_empty_or_reversed_span_is_rejected() -> None:
    with pytest.raises(InvalidLocatorError, match="strictly increasing"):
        parse_locator("bytes:64-64")
    with pytest.raises(InvalidLocatorError, match="strictly increasing"):
        parse_locator("bytes:64-32")


def test_negative_offsets_are_rejected() -> None:
    with pytest.raises(InvalidLocatorError, match="non-negative"):
        parse_locator("bytes:-1-64")


def test_line_spans_are_one_based() -> None:
    with pytest.raises(InvalidLocatorError, match="1-based"):
        parse_locator("line:0-4")


def test_one_span_has_exactly_one_spelling() -> None:
    """Padded and signed offsets are rejected so locator equality is span equality."""
    for padded in ("bytes:0001024-2048", "bytes:+1024-2048", "bytes:1_024-2048"):
        with pytest.raises(InvalidLocatorError, match="canonical decimal"):
            parse_locator(padded)


def test_canonical_span_round_trips_to_the_input_string() -> None:
    assert canonical_span("bytes", 1024, 2048) == "bytes:1024-2048"
    assert parse_locator(canonical_span("char", 0, 7)).canonical.end == 7


def test_whitespace_and_empty_segments_are_rejected() -> None:
    for malformed in ("bytes:0-64|", "|bytes:0-64", "bytes:0-64 ", " bytes:0-64", ""):
        with pytest.raises(InvalidLocatorError):
            parse_locator(malformed)


def test_a_locator_may_not_repeat_a_canonical_span() -> None:
    with pytest.raises(InvalidLocatorError, match="exactly one canonical span"):
        parse_locator("bytes:0-64|char:0-64")
