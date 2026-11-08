"""Span alignment primitive: normalization with an offset map, containment, and
quote location that never lets a near-match pass as a quote."""

import unicodedata

import pytest

from src.services import text_alignment
from src.services.text_alignment import (
    NORMALIZER_VERSION,
    MatchKind,
    containment,
    locate,
    normalize,
)


class TestNormalize:
    def test_every_normalized_character_maps_back_into_the_raw_text(self) -> None:
        raw = "The  \u201c\ufb01nal\u201d re-\n  sult\u2014\ufb02agged\u2026\u00ad\u200b done"
        result = normalize(raw)

        assert result.text == 'The "final" result-flagged... done'
        assert len(result.raw_spans) == len(result.text)
        for index, (start, end) in enumerate(result.raw_spans):
            assert 0 <= start < end <= len(raw)
            if result.text[index].isalnum():
                assert result.text[index] in unicodedata.normalize(
                    "NFKC", raw[start:end]
                )

    def test_nfkc_equivalent_inputs_normalize_identically(self) -> None:
        composed = "caf\u00e9 r\u00e9sum\u00e9"
        decomposed = unicodedata.normalize("NFD", composed)

        a, b = normalize(composed), normalize(decomposed)

        assert a.text == b.text == composed
        # Each composed character maps to the whole decomposed cluster.
        start, end = b.raw_spans[3]
        assert decomposed[start:end] == "e\u0301"

    def test_records_the_normalizations_it_applied(self) -> None:
        assert normalize("plain words").applied == frozenset()
        assert normalize(
            "\ufb01 \u2018q\u2019 a\u2013b x\u00ady re-\nsult  z"
        ).applied == {
            "nfkc",
            "quote_folded",
            "dash_folded",
            "soft_hyphen_removed",
            "linebreak_hyphenation_repaired",
            "whitespace_collapsed",
        }

    def test_a_hyphen_before_a_line_break_is_kept_when_the_next_word_is_capitalized(
        self,
    ) -> None:
        assert normalize("Graph-\nSAGE").text == "Graph- SAGE"

    def test_reproducibility_tuple_is_exposed(self) -> None:
        assert NORMALIZER_VERSION
        assert unicodedata.unidata_version == text_alignment.UNICODE_VERSION
        assert normalize("x").normalizer_version == NORMALIZER_VERSION


SOURCE = (
    "Spatio-temporal graph neural networks combine a temporal encoder with "
    "message passing over a relational graph. We evaluate the model on daily "
    "returns for four hundred equities and report out-of-sample accuracy. "
    "Results are averaged over five random seeds to reduce variance."
)


class TestContainment:
    def test_containment_is_asymmetric(self) -> None:
        passage = (
            "We evaluate the model on daily returns for four hundred equities "
            "and report out-of-sample accuracy."
        )

        forward = containment(passage, SOURCE, shingle_size=4)
        backward = containment(SOURCE, passage, shingle_size=4)

        assert forward.ratio == 1.0
        assert forward.matched_tokens == forward.query_tokens == 15
        assert backward.ratio < 0.5

    def test_partial_overlap_counts_only_covered_tokens(self) -> None:
        query = "Results are averaged over five random seeds to reduce cost entirely"

        result = containment(query, SOURCE, shingle_size=4)

        assert result.query_tokens == 11
        assert result.matched_tokens == 9
        assert result.ratio == pytest.approx(9 / 11)

    def test_word_order_matters(self) -> None:
        shuffled = "seeds random five over averaged are Results"

        assert containment(shuffled, SOURCE, shingle_size=4).matched_tokens == 0

    def test_queries_shorter_than_a_shingle_are_matched_whole(self) -> None:
        assert containment("five random seeds", SOURCE, shingle_size=8).ratio == 1.0
        assert containment("six random seeds", SOURCE, shingle_size=8).ratio == 0.0

    def test_empty_query_has_zero_containment(self) -> None:
        result = containment("   ", SOURCE)
        assert (result.query_tokens, result.matched_tokens, result.ratio) == (0, 0, 0.0)

    def test_hash_collisions_never_create_findings(self, monkeypatch) -> None:
        # Force every shingle into one bucket: only exact verification of the
        # token sequence can keep unrelated text from matching.
        monkeypatch.setattr(text_alignment.alignment, "_shingle_key", lambda _: 0)
        unrelated = "Completely different words that never appear in the source text"

        assert containment(unrelated, SOURCE, shingle_size=3).matched_tokens == 0
        assert containment("five random seeds to", SOURCE, shingle_size=3).ratio == 1.0


class TestLocate:
    def test_exact_quote_returns_raw_offsets(self) -> None:
        quote = "message passing over a relational graph"

        match = locate(quote, SOURCE)

        assert match.kind is MatchKind.EXACT
        assert SOURCE[match.raw_start : match.raw_end] == quote
        assert match.normalizations_applied == frozenset()

    def test_normalized_match_lists_what_was_normalized(self) -> None:
        raw_source = "the \u201c\ufb01nal\u201d model was re-\ntrained on   new data"
        quote = 'the "final" model was retrained on new data'

        match = locate(quote, raw_source)

        assert match.kind is MatchKind.NORMALIZED
        assert (match.raw_start, match.raw_end) == (0, len(raw_source))
        assert match.normalizations_applied == {
            "nfkc",
            "quote_folded",
            "linebreak_hyphenation_repaired",
            "whitespace_collapsed",
        }

    def test_near_match_is_not_found_and_reports_coverage(self) -> None:
        near = "We evaluate the model on weekly returns for four hundred equities"

        match = locate(near, SOURCE)

        assert match.kind is MatchKind.NOT_FOUND
        assert match.raw_start is None and match.raw_end is None
        assert match.coverage is not None
        assert 0 < match.coverage.ratio < 1

    def test_empty_quote_is_not_found(self) -> None:
        assert locate("", SOURCE).kind is MatchKind.NOT_FOUND
