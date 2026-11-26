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


_PIN_SRC = (
    "Intro text. The quick-\nbrown fox said “hello  world” to the lazy dog. "
    "Soft­hyphen word here.   Extra   spaces\there. The end of the line."
)

# Pinned from the implementation before `locate` was re-expressed on `prepare`.
_LOCATE_PINNED = [
    ("quick-\nbrown fox", _PIN_SRC, "exact", 16, 32, [], None),
    ("The quick", _PIN_SRC, "exact", 12, 21, [], None),
    ("end of the line.", _PIN_SRC, "exact", 120, 136, [], None),
    (
        "quickbrown fox said",
        _PIN_SRC,
        "normalized",
        16,
        37,
        ["linebreak_hyphenation_repaired"],
        None,
    ),
    (
        'said "hello world" to',
        _PIN_SRC,
        "normalized",
        33,
        55,
        ["quote_folded", "whitespace_collapsed"],
        None,
    ),
    ("Softhyphen word", _PIN_SRC, "normalized", 70, 86, ["soft_hyphen_removed"], None),
    (
        "Extra spaces here.",
        _PIN_SRC,
        "normalized",
        95,
        115,
        ["whitespace_collapsed"],
        None,
    ),
    ("slow red cat", _PIN_SRC, "not_found", None, None, [], (3, 0)),
    ("", _PIN_SRC, "not_found", None, None, [], (0, 0)),
    ("   ", _PIN_SRC, "not_found", None, None, [], (0, 0)),
    (
        "The quick brown fox jumps over the dog",
        _PIN_SRC,
        "not_found",
        None,
        None,
        [],
        (8, 0),
    ),
    ("lazy dog. Soft", _PIN_SRC, "exact", 60, 74, [], None),
    ("ZZZ", "", "not_found", None, None, [], (1, 0)),
    ("a", "a", "exact", 0, 1, [], None),
    ("hello  world", _PIN_SRC, "exact", 39, 51, [], None),
    (
        "fox said “hello world”",
        _PIN_SRC,
        "normalized",
        29,
        52,
        ["quote_folded", "whitespace_collapsed"],
        None,
    ),
    ("The quick brown", _PIN_SRC, "not_found", None, None, [], (3, 0)),
    ("Intro text. The", _PIN_SRC, "exact", 0, 15, [], None),
    (
        "hyphen word here. Extra spaces",
        _PIN_SRC,
        "normalized",
        75,
        109,
        ["whitespace_collapsed"],
        None,
    ),
    ("dog. Softhyphen", _PIN_SRC, "normalized", 65, 81, ["soft_hyphen_removed"], None),
    ("nothing like it at all in source", _PIN_SRC, "not_found", None, None, [], (7, 0)),
    ("“hello", _PIN_SRC, "exact", 38, 44, [], None),
    ("brown fox said", _PIN_SRC, "exact", 23, 37, [], None),
    ("TO THE LAZY", _PIN_SRC, "not_found", None, None, [], (3, 0)),
    ("to the lazy dog", _PIN_SRC, "exact", 53, 68, [], None),
    ("line.", _PIN_SRC, "exact", 131, 136, [], None),
    ("Intro", _PIN_SRC, "exact", 0, 5, [], None),
    ("text. The quick brown fox said", _PIN_SRC, "not_found", None, None, [], (6, 0)),
    ("lazy cat", _PIN_SRC, "not_found", None, None, [], (2, 0)),
    ("Extra   spaces\there", _PIN_SRC, "exact", 95, 114, [], None),
]


class TestLocatePreparedParity:
    @pytest.mark.parametrize("case", _LOCATE_PINNED)
    def test_locate_is_unchanged(self, case: tuple) -> None:  # type: ignore[type-arg]
        quote, source, kind, start, end, applied, coverage = case
        m = locate(quote, source)
        assert (m.kind.value, m.raw_start, m.raw_end) == (kind, start, end)
        assert sorted(m.normalizations_applied) == applied
        got = (
            None
            if m.coverage is None
            else (m.coverage.query_tokens, m.coverage.matched_tokens)
        )
        assert got == coverage

    def test_prepared_locate_matches_exact_and_normalized(self) -> None:
        prepared = text_alignment.prepare(_PIN_SRC)
        assert (
            text_alignment.locate_prepared("The quick", prepared).kind
            is MatchKind.EXACT
        )
        m = text_alignment.locate_prepared("Softhyphen word", prepared)
        assert (m.kind, m.raw_start, m.raw_end) == (MatchKind.NORMALIZED, 70, 86)
        miss = text_alignment.locate_prepared("slow red cat", prepared)
        assert miss.kind is MatchKind.NOT_FOUND and miss.coverage is None
        assert (
            text_alignment.locate_prepared("  ", prepared).kind is MatchKind.NOT_FOUND
        )

    def test_source_is_normalized_once_for_many_quotes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []
        real = text_alignment.alignment.normalize

        def spy(raw: str):  # type: ignore[no-untyped-def]
            calls.append(len(raw))
            return real(raw)

        monkeypatch.setattr(text_alignment.alignment, "normalize", spy)
        prepared = text_alignment.prepare(_PIN_SRC)
        for _ in range(10):
            text_alignment.locate_prepared("Softhyphen word", prepared)
        big = [n for n in calls if n == len(_PIN_SRC)]
        assert len(big) == 1
