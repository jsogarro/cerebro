"""Exact shared-token runs between a document and candidate sources.

A match is equality of token keys; no similarity score ever becomes a match.
CPU-bound and synchronous: callers run it with ``asyncio.to_thread``.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Iterable
from itertools import accumulate

from src.core.claims.citation_prose import (
    CitationProseKind,
    find_unrendered_citation_prose,
)
from src.services.text_alignment.alignment import normalize
from src.services.text_reuse.types import (
    MAX_MATCHES_PER_CANDIDATE,
    MAX_POSITIONS_PER_SHINGLE,
    MIN_MATCH_TOKENS,
    SHINGLE_TOKENS,
    Attribution,
    Candidate,
    ExcludedRegion,
    ReuseBudget,
    ReuseMatch,
    SkipReason,
    VerbatimResult,
)

_HEADING = r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\d+\.?[ \t]*)?"
_REFERENCES = re.compile(
    _HEADING
    + r"(?:references|bibliography|works cited|literature cited"
    + r"|notes and references|references and notes)[ \t\r]*(?::[ \t\r]*)?$",
    re.I | re.M,
)
_APPENDIX = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:appendix|appendices|supplementary|supplemental)\b",
    re.I | re.M,
)
_STATEMENT = re.compile(
    _HEADING
    + r"(?:acknowledg(?:e)?ments?|funding|competing interests?"
    + r"|conflicts? of interest|data availability|ethics(?: statement)?"
    + r"|author contributions)[ \t]*(?::[ \t]*)?$",
    re.I,
)
_REFERENCE_ENTRY = re.compile(r"^[ \t]*(?:\[(\d+)\]|(\d+)\.)[ \t]", re.M)
_DOUBLE_QUOTES = str.maketrans(dict.fromkeys("“”„‟″«»", '"'))
# A quote never spans a blank line, so one stray mark cannot flip later paragraphs.
_QUOTED = re.compile(r'"(?:[^"\n]|\n(?!\s*\n))*"')
_SENTENCE_END = re.compile(r"(?<!\bal)[.!?]\s")
_QUOTED_SHARE = 0.8
_MAX_QUOTE_CHARS = 20_000
_WINDOW_BEFORE = 150
_WINDOW_AFTER = 300
_LINE_LOOKBACK = 200


_FILLERS = frozenset("\u115f\u1160\u3164\uffa0")


def _invisible(ch: str) -> bool:
    """Format, combining and filler characters that render as nothing."""
    return unicodedata.category(ch) in ("Cf", "Mn", "Me") or ch in _FILLERS


def _strippable(ch: str) -> bool:
    # ">" and "|" are Markdown quote and table markers, not words.
    return unicodedata.category(ch).startswith("P") or ch in ">|"


def _token_key(token: str) -> str:
    key = token.casefold()
    if not key.isascii():
        key = "".join(c for c in key if not _invisible(c))
        if not key:
            return key
    if key[0].isalnum() and key[-1].isalnum():
        return key
    lo, hi = 0, len(key)
    while lo < hi and _strippable(key[lo]):
        lo += 1
    while hi > lo and _strippable(key[hi - 1]):
        hi -= 1
    return key[lo:hi]


def _tokenize(raw: str) -> list[tuple[str, int, int]]:
    norm = normalize(raw)
    return [(k, s, e) for t, s, e in norm.tokens() if (k := _token_key(t))]


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).casefold().strip()


def author_key(name: str) -> tuple[str, str]:
    """Casefolded, accent-stripped (surname, first initial)."""
    if "," in name:
        surname, _, given = name.partition(",")
    else:
        parts = name.split() or [""]
        last = parts[-1].rstrip(".")
        if len(parts) > 1 and last.isalpha() and last.isupper() and len(last) <= 3:
            # "Smith JA": trailing initials
            surname, given = " ".join(parts[:-1]), last
        else:
            # "Jan van der Berg": the surname starts at the first lowercase particle
            k = next(
                (n for n, p in enumerate(parts) if n and p[0].islower()), len(parts) - 1
            )
            surname, given = " ".join(parts[k:]), " ".join(parts[:k])
    return _fold(surname), _fold(given.replace(".", ""))[:1]


def _lines(raw: str) -> list[tuple[int, int, str]]:
    out, pos = [], 0
    for line in raw.splitlines(keepends=True):
        out.append((pos, pos + len(line), line.rstrip("\r\n")))
        pos += len(line)
    return out


def reference_region(raw: str) -> tuple[int, int] | None:
    headings = list(_REFERENCES.finditer(raw))
    if not headings:
        return None
    last = headings[-1]
    appendix = _APPENDIX.search(raw, last.end())
    return last.start(), appendix.start() if appendix else len(raw)


def _reference_count(region: str) -> int:
    # Entries above 999 are years ("2019. Title"), not list numbers.
    nums = (int(a or b) for a, b in _REFERENCE_ENTRY.findall(region))
    return max((n for n in nums if n < 1000), default=0)


def _statement_ranges(raw: str) -> list[tuple[int, int]]:
    lines = _lines(raw)
    shaped = [
        i
        for i, (_, _, text) in enumerate(lines)
        if text.strip()
        and len(text.strip()) < 80
        and not text.rstrip().endswith(".")
        and (i == 0 or text.lstrip().startswith("#") or not lines[i - 1][2].strip())
    ]
    ranges = []
    for n, i in enumerate(shaped):
        if _STATEMENT.match(lines[i][2]):
            end = lines[shaped[n + 1]][0] if n + 1 < len(shaped) else len(raw)
            ranges.append((lines[i][0], end))
    return ranges


def _index(keys: list[str]) -> tuple[dict[tuple[str, ...], list[int]], int]:
    index: dict[tuple[str, ...], list[int]] = {}
    common: set[tuple[str, ...]] = set()
    for j in range(len(keys) - SHINGLE_TOKENS + 1):
        sh = tuple(keys[j : j + SHINGLE_TOKENS])
        if sh in common:
            continue
        positions = index.setdefault(sh, [])
        positions.append(j)
        if len(positions) > MAX_POSITIONS_PER_SHINGLE:
            del index[sh]
            common.add(sh)
    return index, len(common)


class _DeadlineReachedError(Exception):
    pass


def _runs(
    doc: list[str],
    segments: list[tuple[int, int]],
    cand: list[str],
    deadline: float = float("inf"),
) -> tuple[list[tuple[int, int, int, int]], int, bool]:
    """Runs, common-shingle count, and whether the match cap cut the scan short."""
    index, common = _index(cand)
    last_end: dict[int, int] = {}
    runs = []
    for lo, hi in segments:
        for i in range(lo, hi - SHINGLE_TOKENS + 1):
            for j in index.get(tuple(doc[i : i + SHINGLE_TOKENS]), ()):
                if time.monotonic() > deadline:
                    raise _DeadlineReachedError
                d = i - j
                if last_end.get(d, -1) > i:
                    continue
                i0, j0, i1, j1 = i, j, i + SHINGLE_TOKENS, j + SHINGLE_TOKENS
                while i0 > lo and j0 > 0 and doc[i0 - 1] == cand[j0 - 1]:
                    i0, j0 = i0 - 1, j0 - 1
                while i1 < hi and j1 < len(cand) and doc[i1] == cand[j1]:
                    i1, j1 = i1 + 1, j1 + 1
                last_end[d] = i1
                if i1 - i0 >= MIN_MATCH_TOKENS:
                    runs.append((i0, i1, j0, j1))
                    if len(runs) >= MAX_MATCHES_PER_CANDIDATE:
                        return runs, common, True
    return runs, common, False


class _Attributor:
    def __init__(
        self, raw: str, tokens: list[tuple[str, int, int]], refs: int, body_end: int
    ):
        self.raw, self.refs, self.body_end = raw, refs, body_end
        regions = [
            m.span()
            for m in _QUOTED.finditer(raw.translate(_DOUBLE_QUOTES))
            if m.end() - m.start() <= _MAX_QUOTE_CHARS
        ]
        flags, r = [], 0
        for _, s, e in tokens:
            while r < len(regions) and regions[r][1] < e:
                r += 1
            flags.append(
                int(r < len(regions) and regions[r][0] <= s and e <= regions[r][1])
            )
        self.quoted_prefix = [0, *accumulate(flags)]

    def classify(self, i0: int, i1: int, start: int, end: int) -> Attribution:
        quoted = self._quoted(i0, i1, start, end)
        cited = self._cited(start, end)
        if quoted:
            return Attribution.QUOTED_AND_CITED if cited else Attribution.QUOTED_UNCITED
        return Attribution.CITED_UNQUOTED if cited else Attribution.UNATTRIBUTED

    def _quoted(self, i0: int, i1: int, start: int, end: int) -> bool:
        if self.quoted_prefix[i1] - self.quoted_prefix[i0] >= _QUOTED_SHARE * (i1 - i0):
            return True
        # Bounded look-back: an unbounded rfind is quadratic on a newline-free text.
        floor = max(0, start - _LINE_LOOKBACK)
        line_start = self.raw.rfind("\n", floor, start) + 1
        if line_start == 0 and floor > 0:
            return False
        block = self.raw[line_start:end].splitlines()
        return all(line.lstrip().startswith(">") for line in block)

    def _cited(self, start: int, end: int) -> bool:
        lo = max(0, start - _WINDOW_BEFORE)
        ends = list(_SENTENCE_END.finditer(self.raw, lo, start))
        lo = ends[-1].end() if ends else lo
        # Reference-list entries are citation-shaped; they must not cite the body.
        hi = end + _WINDOW_AFTER
        if start < self.body_end:
            hi = max(end, min(hi, self.body_end))
        # The citation scanner is superlinear on some inputs, so a long match is
        # scanned only near its two edges.
        head_end = min(hi, start + _WINDOW_AFTER)
        tail_start = max(head_end, end - _WINDOW_AFTER)
        windows = [self.raw[lo:head_end]]
        if tail_start < hi:
            windows.append(self.raw[tail_start:hi])
        return any(self._cites(w) for w in windows)

    def _cites(self, window: str) -> bool:
        for f in find_unrendered_citation_prose(window):
            if f.kind is not CitationProseKind.NUMERIC:
                return True
            nums = [int(n) for n in re.findall(r"\d+", f.text)]
            if nums and 0 < max(nums) <= self.refs:
                return True
        return False


def _slice(raw: str, tokens: list[tuple[str, int, int]], a: int, b: int) -> str:
    return raw[tokens[a][1] : tokens[b - 1][2]]


def _skip_reason(
    c: Candidate, started: float, total_chars: int, budget: ReuseBudget
) -> SkipReason | None:
    if time.monotonic() - started >= budget.deadline_s:
        return "deadline_reached"
    if total_chars >= budget.max_total_chars:
        return "budget_exhausted"
    if len(c.text) > budget.max_candidate_chars:
        return "too_long"
    return None


def find_verbatim_reuse(
    document: str,
    candidates: Iterable[Candidate],
    *,
    declared_authors: Iterable[tuple[str, str]],
    budget: ReuseBudget,
) -> VerbatimResult:
    started = time.monotonic()
    doc_tokens = _tokenize(document)
    doc_keys = [t[0] for t in doc_tokens]

    segments = [(0, len(doc_keys))]
    excluded: tuple[ExcludedRegion, ...] = ()
    refs = 0
    region = reference_region(document)
    if region:
        inside = [i for i, t in enumerate(doc_tokens) if region[0] <= t[1] < region[1]]
        lo, hi = (inside[0], inside[-1] + 1) if inside else (0, 0)
        segments = [s for s in ((0, lo), (hi, len(doc_keys))) if s[1] > s[0]]
        excluded = (ExcludedRegion("references", region[0], region[1], hi - lo),)
        refs = _reference_count(document[region[0] : region[1]])

    attributor = _Attributor(
        document, doc_tokens, refs, region[0] if region else len(document)
    )
    statements = _statement_ranges(document)
    declared = {(_fold(s), _fold(i)[:1]) for s, i in declared_authors}

    matches: list[ReuseMatch] = []
    skipped: list[tuple[str, SkipReason]] = []
    common_total = 0
    total_chars = 0
    for c in candidates:
        reason = _skip_reason(c, started, total_chars, budget)
        if reason:
            skipped.append((c.source_id, reason))
            continue
        total_chars += len(c.text)

        src_tokens = _tokenize(c.text)
        try:
            runs, common, truncated = _runs(
                doc_keys,
                segments,
                [t[0] for t in src_tokens],
                started + budget.deadline_s,
            )
        except _DeadlineReachedError:
            skipped.append((c.source_id, "deadline_reached"))
            continue
        common_total += common
        if truncated:
            skipped.append((c.source_id, "too_many_matches"))
        overlap = any(k[0] and k in declared for k in map(author_key, c.authors))
        for i0, i1, j0, j1 in sorted(runs):
            d_start, d_end = doc_tokens[i0][1], doc_tokens[i1 - 1][2]
            doc_raw = _slice(document, doc_tokens, i0, i1)
            src_raw = _slice(c.text, src_tokens, j0, j1)
            verbatim = " ".join(doc_raw.split()) == " ".join(src_raw.split())
            applied = normalize(doc_raw).applied | normalize(src_raw).applied
            if not verbatim:
                applied |= {"token_key_folding"}
            matches.append(
                ReuseMatch(
                    candidate_source_id=c.source_id,
                    kind="verbatim" if verbatim else "normalized",
                    doc_start=d_start,
                    doc_end=d_end,
                    src_start=src_tokens[j0][1],
                    src_end=src_tokens[j1 - 1][2],
                    tokens=i1 - i0,
                    attribution=attributor.classify(i0, i1, d_start, d_end),
                    section="statement"
                    if any(a <= d_start < b for a, b in statements)
                    else "body",
                    from_user_upload=c.origin == "own_upload",
                    declared_author_overlap=overlap,
                    normalizations_applied=applied,
                )
            )

    return VerbatimResult(
        matches=tuple(matches),
        excluded_regions=excluded,
        skipped=tuple(skipped),
        skipped_common_shingles=common_total,
    )
