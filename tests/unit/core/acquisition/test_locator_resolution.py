"""Resolution of the frozen locator grammar against snapshot bytes.

Packet 4A froze the grammar and left resolution unimplemented, so "stable" was
a property of the spelling rather than a demonstrated round trip. These tests
are the demonstration for the pure half: given a locator string and the bytes
it was minted against, exactly one byte range comes back, every time.

The cases that matter most are the refusals. A locator whose span runs past
the end of the snapshot must **raise**, because Python slicing would silently
return a shorter excerpt — and an evidence pointer that quietly resolves to
less than it claims is worse than one that fails, since nothing downstream can
tell the difference.
"""

import pytest

from src.core.acquisition.resolution import (
    UnresolvableLocatorError,
    locate,
    resolve,
)
from src.core.contracts.locators import InvalidLocatorError

SNAPSHOT = b"alpha\nbeta\ngamma\n"


class TestByteSpans:
    def test_a_byte_span_returns_exactly_its_range(self) -> None:
        assert resolve("bytes:0-5", SNAPSHOT) == b"alpha"

    def test_a_byte_span_is_half_open(self) -> None:
        assert resolve("bytes:6-10", SNAPSHOT) == b"beta"

    def test_a_span_reaching_the_final_byte_resolves(self) -> None:
        assert resolve("bytes:11-16", SNAPSHOT) == b"gamma"

    def test_a_span_past_the_end_is_refused_rather_than_truncated(self) -> None:
        # Python would return b"gamma\n" here. Silently resolving to fewer
        # bytes than the locator claims is the failure this refusal exists for.
        with pytest.raises(UnresolvableLocatorError, match="beyond"):
            resolve("bytes:11-99", SNAPSHOT)

    def test_a_span_starting_past_the_end_is_refused(self) -> None:
        with pytest.raises(UnresolvableLocatorError, match="beyond"):
            resolve("bytes:20-30", SNAPSHOT)

    def test_resolution_is_pure_over_the_bytes_it_is_given(self) -> None:
        # The resolver must consume its bytes argument rather than any cached
        # or stored copy: the same locator over different bytes differs.
        assert resolve("bytes:0-5", SNAPSHOT) != resolve("bytes:0-5", b"omega\n")


class TestCharacterSpans:
    def test_character_offsets_count_code_points_not_bytes(self) -> None:
        snapshot = "héllo wörld".encode()

        assert resolve("char:0-5", snapshot) == "héllo".encode()

    def test_a_character_span_past_the_end_is_refused(self) -> None:
        with pytest.raises(UnresolvableLocatorError, match="beyond"):
            resolve("char:0-99", "héllo".encode())

    def test_a_snapshot_that_is_not_text_refuses_a_character_span(self) -> None:
        with pytest.raises(UnresolvableLocatorError, match="not valid UTF-8"):
            resolve("char:0-2", b"\xff\xfe\x00")

    def test_byte_and_character_spans_disagree_on_multibyte_content(self) -> None:
        # The two schemes are not interchangeable, which is why the grammar
        # makes a locator name which one it means.
        snapshot = "héllo".encode()

        assert resolve("bytes:0-2", snapshot) != resolve("char:0-2", snapshot)


class TestLineSpans:
    def test_line_offsets_are_one_based(self) -> None:
        assert resolve("line:1-2", SNAPSHOT) == b"alpha\n"

    def test_a_line_span_is_half_open_and_includes_terminators(self) -> None:
        assert resolve("line:1-3", SNAPSHOT) == b"alpha\nbeta\n"

    def test_a_final_line_without_a_terminator_resolves_to_what_is_there(
        self,
    ) -> None:
        assert resolve("line:2-3", b"alpha\nbeta") == b"beta"

    def test_carriage_returns_are_content_not_delimiters(self) -> None:
        # Only LF delimits. A CRLF file's lines therefore end in CRLF, which is
        # verbatim: the resolver never normalizes what the source actually said.
        assert resolve("line:1-2", b"alpha\r\nbeta\r\n") == b"alpha\r\n"

    def test_a_line_span_past_the_end_is_refused(self) -> None:
        with pytest.raises(UnresolvableLocatorError, match="beyond"):
            resolve("line:1-99", SNAPSHOT)

    def test_an_empty_snapshot_has_no_first_line_to_resolve(self) -> None:
        # An empty snapshot has zero lines, not one empty one. Resolving here
        # would return b"" — a citation that reads as a successful quotation of
        # nothing at all.
        with pytest.raises(UnresolvableLocatorError, match="beyond"):
            resolve("line:1-2", b"")

    def test_an_empty_snapshot_has_no_bytes_to_resolve(self) -> None:
        with pytest.raises(UnresolvableLocatorError, match="beyond"):
            resolve("bytes:0-1", b"")


class TestAnnotationsAndGrammar:
    def test_annotations_do_not_change_what_resolves(self) -> None:
        # Annotations are explicitly non-authoritative; the canonical span is
        # the whole authority, so adding one cannot move the result.
        assert resolve("bytes:0-5|css:h1", SNAPSHOT) == resolve("bytes:0-5", SNAPSHOT)

    def test_a_malformed_locator_is_rejected_by_the_frozen_grammar(self) -> None:
        with pytest.raises(InvalidLocatorError):
            resolve("bytes:5-5", SNAPSHOT)

    def test_an_annotation_first_is_rejected(self) -> None:
        with pytest.raises(InvalidLocatorError):
            resolve("css:h1|bytes:0-5", SNAPSHOT)


class TestLocate:
    def test_locate_produces_a_locator_that_resolves_to_the_named_span(self) -> None:
        locator = locate("bytes", 6, 10)

        assert locator == "bytes:6-10"
        assert resolve(locator, SNAPSHOT) == b"beta"

    def test_locate_carries_annotations_after_the_canonical_span(self) -> None:
        assert locate("char", 0, 5, annotations=("heading:intro",)) == (
            "char:0-5|heading:intro"
        )

    def test_locate_refuses_a_span_the_grammar_would_reject(self) -> None:
        with pytest.raises(InvalidLocatorError):
            locate("line", 0, 4)

    def test_locate_refuses_an_annotation_scheme_the_grammar_does_not_know(
        self,
    ) -> None:
        with pytest.raises(InvalidLocatorError):
            locate("bytes", 0, 4, annotations=("selector:h1",))
