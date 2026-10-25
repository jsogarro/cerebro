"""Normalization with a raw-offset map, shingle containment, and quote location.

Three rules carry the honesty constraint:

- every normalized character maps back to a raw span, so any locator resolves
  into the raw source rather than into a derived string;
- containment is ``matched_tokens / query_tokens`` -- asymmetric, so a short
  passage inside a long source scores near 1.0 where Jaccard scores near 0;
- no similarity threshold ever declares a match. A quote is found exactly,
  found after listed normalizations, or not found.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

NORMALIZER_VERSION = "1"
UNICODE_VERSION = unicodedata.unidata_version
SHINGLE_SIZE = 5

_ZERO_WIDTH = frozenset("\u200b\u200c\u200d\u2060\ufeff")
_SOFT_HYPHEN = "\u00ad"
_QUOTES = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u2032": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u201f": '"',
    "\u2033": '"',
}
_DASHES = dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-")
_LINEBREAK_HYPHEN = re.compile(r"-[ \t]*\r?\n[ \t]*")


@dataclass(frozen=True)
class NormalizedText:
    text: str
    # raw_spans[i] is the half-open raw span that produced text[i].
    raw_spans: tuple[tuple[int, int], ...]
    applied: frozenset[str]
    normalizer_version: str = NORMALIZER_VERSION

    def tokens(self) -> list[tuple[str, int, int]]:
        """Whitespace tokens as ``(token, raw_start, raw_end)``."""
        return [
            (m.group(), self.raw_spans[m.start()][0], self.raw_spans[m.end() - 1][1])
            for m in re.finditer(r"\S+", self.text)
        ]


def normalize(raw: str) -> NormalizedText:
    chars: list[str] = []
    spans: list[tuple[int, int]] = []
    applied: set[str] = set()
    pending_space: tuple[int, int] | None = None
    whitespace_run = 0

    def emit(char: str, span: tuple[int, int]) -> None:
        nonlocal pending_space, whitespace_run
        if char.isspace():
            if pending_space is None:
                pending_space = span
            whitespace_run += 1
            if char != " " or whitespace_run > 1:
                applied.add("whitespace_collapsed")
            return
        if pending_space is not None and chars:
            chars.append(" ")
            spans.append(pending_space)
        elif pending_space is not None:
            applied.add("whitespace_collapsed")
        pending_space, whitespace_run = None, 0
        chars.append(char)
        spans.append(span)

    i = 0
    while i < len(raw):
        char = raw[i]
        if char in _ZERO_WIDTH or char == _SOFT_HYPHEN:
            applied.add(
                "soft_hyphen_removed" if char == _SOFT_HYPHEN else "zero_width_removed"
            )
            i += 1
            continue
        hyphen = _LINEBREAK_HYPHEN.match(raw, i)
        if (
            hyphen
            and chars
            and chars[-1].isalpha()
            and pending_space is None
            and raw[hyphen.end() : hyphen.end() + 1].islower()
        ):
            applied.add("linebreak_hyphenation_repaired")
            i = hyphen.end()
            continue
        end = i + 1
        while end < len(raw) and unicodedata.combining(raw[end]):
            end += 1
        cluster = raw[i:end]
        folded = unicodedata.normalize("NFKC", cluster)
        if folded != cluster:
            applied.add("nfkc")
        for out in folded:
            if out in _QUOTES:
                applied.add("quote_folded")
                out = _QUOTES[out]
            elif out in _DASHES:
                applied.add("dash_folded")
                out = _DASHES[out]
            emit(out, (i, end))
        i = end
    if pending_space is not None and chars:
        applied.add("whitespace_collapsed")
    return NormalizedText("".join(chars), tuple(spans), frozenset(applied))


@dataclass(frozen=True)
class ContainmentResult:
    query_tokens: int
    matched_tokens: int

    @property
    def ratio(self) -> float:
        return self.matched_tokens / self.query_tokens if self.query_tokens else 0.0


def _shingle_key(tokens: tuple[str, ...]) -> int:
    return hash(tokens)


def containment(
    query: str, source: str, *, shingle_size: int = SHINGLE_SIZE
) -> ContainmentResult:
    """Fraction of query tokens covered by word n-grams that occur in source."""
    q = [token for token, _, _ in normalize(query).tokens()]
    s = [token for token, _, _ in normalize(source).tokens()]
    n = min(shingle_size, len(q))
    if n == 0:
        return ContainmentResult(0, 0)
    index: dict[int, list[int]] = {}
    for j in range(len(s) - n + 1):
        index.setdefault(_shingle_key(tuple(s[j : j + n])), []).append(j)
    covered = [False] * len(q)
    for i in range(len(q) - n + 1):
        shingle = q[i : i + n]
        # A key match is only a candidate; the token sequence must be equal.
        if any(
            s[j : j + n] == shingle for j in index.get(_shingle_key(tuple(shingle)), ())
        ):
            covered[i : i + n] = [True] * n
    return ContainmentResult(len(q), sum(covered))


class MatchKind(StrEnum):
    EXACT = "exact"
    NORMALIZED = "normalized"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class SpanMatch:
    kind: MatchKind
    raw_start: int | None = None
    raw_end: int | None = None
    normalizations_applied: frozenset[str] = frozenset()
    # Set only for NOT_FOUND: how much of the quote the source does contain.
    coverage: ContainmentResult | None = None


def locate(quote: str, source: str) -> SpanMatch:
    if quote.strip():
        start = source.find(quote)
        if start >= 0:
            return SpanMatch(MatchKind.EXACT, start, start + len(quote))
        normalized_quote = normalize(quote)
        normalized_source = normalize(source)
        at = normalized_source.text.find(normalized_quote.text)
        if normalized_quote.text and at >= 0:
            raw_start = normalized_source.raw_spans[at][0]
            raw_end = normalized_source.raw_spans[at + len(normalized_quote.text) - 1][
                1
            ]
            return SpanMatch(
                MatchKind.NORMALIZED,
                raw_start,
                raw_end,
                normalized_quote.applied | normalize(source[raw_start:raw_end]).applied,
            )
    return SpanMatch(MatchKind.NOT_FOUND, coverage=containment(quote, source))
