"""Verbatim text-reuse core: matching, regions, attribution, overlays, validation."""

from __future__ import annotations

import enum
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from src.core.acquisition.sources import LicenseDeclaration, SourceLicense
from src.services.text_reuse import types as reuse_types
from src.services.text_reuse.types import (
    MAX_TOTAL_CANDIDATE_CHARS,
    MIN_MATCH_TOKENS,
    Attribution,
    Candidate,
    ReuseBudget,
    source_link,
)
from src.services.text_reuse.verbatim import find_verbatim_reuse

M = MIN_MATCH_TOKENS
OWN_ID = str(uuid.uuid4())
LICENCE = SourceLicense(None, LicenseDeclaration.UNDECLARED)


def words(prefix: str, n: int) -> list[str]:
    return [f"{prefix}{i}" for i in range(n)]


def sentence(prefix: str, n: int) -> str:
    return " ".join(words(prefix, n))


def cand(text: str, *, sid: str = "W1", origin: str = "openalex", **kw) -> Candidate:
    if origin == "own_upload" and sid == "W1":
        sid = OWN_ID
    return Candidate(
        source_id=sid,
        origin=origin,
        title="t",
        authors=kw.pop("authors", ()),
        year=2020,
        doi=None,
        arxiv_id=None,
        licence=LICENCE,
        text=text,
        text_kind="full_text",
        **kw,
    )


def run(doc: str, cands, authors=(), budget=None):
    return find_verbatim_reuse(
        doc, cands, declared_authors=authors, budget=budget or ReuseBudget()
    )


def passage(n: int = 30) -> str:
    return sentence("p", n)


# -- 1-3 matching ------------------------------------------------------------


def test_identical_passage_found_once_verbatim():
    p = passage()
    doc = f"{sentence('a', 20)} {p} {sentence('b', 20)}"
    src = f"{sentence('c', 15)} {p} {sentence('d', 15)}"
    res = run(doc, [cand(src)])
    assert len(res.matches) == 1
    m = res.matches[0]
    assert doc[m.doc_start : m.doc_end] == p
    assert src[m.src_start : m.src_end] == p
    assert m.kind == "verbatim"
    assert m.tokens == 30


def test_normalized_kind_lists_normalizations():
    p = passage(28).split()
    src_p = " ".join(p)
    doc_p = " ".join(p[:5]) + "—" + "-".join([p[5], p[6]]) + " " + " ".join(p[7:])
    doc_p = doc_p.replace(p[10], p[10].upper())
    doc_p = doc_p.replace(f"{p[5]}-{p[6]}", f"{p[5]}-\n{p[6]}")
    doc_p = f"“{doc_p}”"
    res = run(
        f"{sentence('a', 9)} {doc_p} {sentence('b', 9)}",
        [cand(sentence("c", 9) + " " + src_p + " " + sentence("d", 9))],
    )
    assert res.matches and res.matches[0].kind == "normalized"
    assert res.matches[0].normalizations_applied


@pytest.mark.parametrize(
    "overlap,expected", [(M - 1, 0), (M, 1), (M + 1, 1), (M + 2, 1)]
)
def test_length_boundaries(overlap, expected):
    p = sentence("p", overlap)
    res = run(
        f"{sentence('a', 20)} {p} {sentence('b', 20)}",
        [cand(f"{sentence('c', 20)} {p} {sentence('d', 20)}")],
    )
    assert len(res.matches) == expected
    if expected:
        assert res.matches[0].tokens == overlap


@pytest.mark.parametrize("pos", [3, 6, 9])
def test_single_substitution_leaves_only_long_runs(pos):
    n = M + 8
    p = words("p", n)
    q = list(p)
    q[pos] = "SUBSTITUTE"
    runs = [r for r in (pos, n - pos - 1) if r >= M]
    res = run(
        f"{sentence('a', 20)} {' '.join(q)} {sentence('b', 20)}",
        [cand(f"{sentence('c', 20)} {' '.join(p)} {sentence('d', 20)}")],
    )
    assert sorted(m.tokens for m in res.matches) == sorted(runs)


def test_two_candidates_and_overlapping_seeds_merge():
    p = passage(40)
    doc = f"{sentence('a', 10)} {p} {sentence('b', 10)}"
    res = run(
        doc, [cand(f"x {p} y", sid="W1"), cand(f"{sentence('z', 5)} {p}", sid="W2")]
    )
    assert sorted(m.candidate_source_id for m in res.matches) == ["W1", "W2"]
    assert all(m.tokens == 40 for m in res.matches)


# -- 6 work cap --------------------------------------------------------------


def test_repetitive_text_is_bounded_and_planted_passage_found():
    rep = "a b c d e f g h " * 100_000
    plant = sentence("plant", 20)
    t0 = time.monotonic()
    res = run(
        f"{rep} zz1 {plant} zz2 {rep}"[:1_900_000],
        [cand(f"{rep} yy1 {plant} yy2 {rep}"[:1_900_000])],
    )
    assert time.monotonic() - t0 < 5
    assert res.skipped_common_shingles >= 1
    assert [m.tokens for m in res.matches] == [20]


# -- 7 budget ----------------------------------------------------------------


def test_budget_total_too_long_and_deadline():
    cs = [cand(sentence(f"c{i}x", 20), sid=f"W{i}") for i in range(1, 7)]
    res = run(
        "doc text",
        cs,
        budget=ReuseBudget(
            max_total_chars=5 * len(cs[0].text), max_candidate_chars=len(cs[0].text)
        ),
    )
    assert dict(res.skipped) == {"W6": "budget_exhausted"}
    res = run("doc", [cand("x" * 11)], budget=ReuseBudget(max_candidate_chars=10))
    assert dict(res.skipped) == {"W1": "too_long"}
    res = run("doc", cs[:2], budget=ReuseBudget(deadline_s=0))
    assert dict(res.skipped) == {"W1": "deadline_reached", "W2": "deadline_reached"}


def test_default_budget_constants():
    b = ReuseBudget()
    assert (b.max_total_chars, b.max_candidate_chars, b.deadline_s) == (
        MAX_TOTAL_CANDIDATE_CHARS,
        2_000_000,
        120,
    )
    assert MAX_TOTAL_CANDIDATE_CHARS == 10_000_000
    res = run("doc", [cand("x" * 2_000_001)])
    assert dict(res.skipped) == {"W1": "too_long"}


# -- 8 regions ---------------------------------------------------------------


def test_reference_region_excluded_with_range_and_count():
    p = passage()
    doc = f"{sentence('a', 30)}\n\nReferences\n\n[1] {p}\n"
    res = run(doc, [cand(p)])
    assert res.matches == ()
    (region,) = res.excluded_regions
    assert doc[region.start : region.end].startswith("References")
    assert region.tokens >= 30


def test_appendix_after_references_is_compared():
    p = passage()
    doc = f"{sentence('a', 30)}\n\nReferences\n\n[1] x y z.\n\nAppendix A\n\n{p}\n"
    res = run(doc, [cand(p)])
    assert len(res.matches) == 1


def test_notes_and_references_heading_recognised():
    p = passage()
    res = run(f"{sentence('a', 30)}\n\nNotes and References\n\n{p}\n", [cand(p)])
    assert res.matches == () and res.excluded_regions


def test_reference_list_vs_reference_list_returns_nothing():
    refs = "\n".join(f"[{i}] {sentence(f'r{i}_', 15)}." for i in range(1, 6))
    res = run(
        f"{sentence('a', 30)}\n\nReferences\n\n{refs}\n",
        [cand(f"{sentence('b', 30)}\n\nReferences\n\n{refs}\n")],
    )
    assert res.matches == ()


def test_no_reference_heading_excludes_nothing():
    p = passage()
    res = run(f"{sentence('a', 30)} {p}", [cand(p)])
    assert res.excluded_regions == () and len(res.matches) == 1


def test_statement_section_labelled():
    p = passage()
    doc = (
        f"{sentence('a', 30)}\n\nFunding\n\n{p}.\n\nIntroduction\n\n{sentence('b', 30)}"
    )
    res = run(doc, [cand(p)])
    assert [m.section for m in res.matches] == ["statement"]
    res = run(f"{p}\n\nFunding\n\nNone.", [cand(p)])
    assert [m.section for m in res.matches] == ["body"]


# -- 9 attribution -----------------------------------------------------------

REFS4 = "\n\nReferences\n\n[1] A.\n[2] B.\n[3] C.\n[4] D.\n"
REFS2 = "\n\nReferences\n\n[1] A.\n[2] B.\n"


def attribution(doc: str, p: str | None = None) -> Attribution:
    res = run(doc, [cand(p or passage())])
    assert len(res.matches) == 1
    return res.matches[0].attribution


def test_attribution_table():
    p = passage()
    lead = sentence("a", 25)
    assert attribution(f'{lead}. "{p}" (Smith, 2020).') is Attribution.QUOTED_AND_CITED
    assert (
        attribution(f'{lead}. Smith (2020) writes: "{p}".')
        is Attribution.QUOTED_AND_CITED
    )
    assert attribution(f'{lead}. "{p}".') is Attribution.QUOTED_UNCITED
    assert attribution(f"{lead}. {p} [4].{REFS4}") is Attribution.CITED_UNQUOTED
    assert attribution(f"{lead}. {p} [4].{REFS2}") is Attribution.UNATTRIBUTED
    assert attribution(f"{lead}. {p} [4].") is Attribution.UNATTRIBUTED
    inner = p.split()
    quoted_gap = " ".join(inner[:15]) + " … " + " ".join(inner[15:])
    assert attribution(f'{lead}. "{quoted_gap}".', p) is Attribution.QUOTED_UNCITED
    assert (
        attribution(f"{lead}.\n\n> {p}\n> (Smith, 2020)")
        is Attribution.QUOTED_AND_CITED
    )


def test_curly_and_guillemet_quotes_count():
    p = passage()
    assert attribution(f"{sentence('a', 25)}. “{p}”.") is Attribution.QUOTED_UNCITED
    assert attribution(f"{sentence('a', 25)}. «{p}».") is Attribution.QUOTED_UNCITED


def test_partial_quote_below_80_percent_is_not_quoted():
    w = passage().split()
    doc = f'{sentence("a", 25)}. "{" ".join(w[:10])}" {" ".join(w[10:])}.'
    assert attribution(doc) is Attribution.UNATTRIBUTED


def test_citation_after_window_is_ignored():
    p = passage()
    far = " ".join(["filler"] * 200)
    assert (
        attribution(f"{sentence('a', 25)}. {p}. {far} (Smith, 2020).")
        is Attribution.UNATTRIBUTED
    )


# -- 10 overlays -------------------------------------------------------------


def test_overlays_do_not_change_class():
    p = passage()
    doc = f'{sentence("a", 20)}. "{p}" (Smith, 2020).'
    base = run(doc, [cand(p, authors=("Smith, John",))]).matches[0]
    assert base.attribution is Attribution.QUOTED_AND_CITED
    assert not base.from_user_upload and not base.declared_author_overlap
    own = run(doc, [cand(p, origin="own_upload")]).matches[0]
    assert own.from_user_upload and own.attribution is base.attribution
    hit = run(
        doc, [cand(p, authors=("Smith, John",))], authors=[("smith", "j")]
    ).matches[0]
    assert hit.declared_author_overlap and hit.attribution is base.attribution
    miss = run(
        doc, [cand(p, authors=("Jones, Ann",))], authors=[("smith", "j")]
    ).matches[0]
    assert not miss.declared_author_overlap


def test_author_key_accepts_given_first_and_accents():
    p = passage()
    doc = f"{sentence('a', 20)} {p}"
    m = run(doc, [cand(p, authors=("José Núñez",))], authors=[("nunez", "j")]).matches[
        0
    ]
    assert m.declared_author_overlap


# -- 11 validation -----------------------------------------------------------


def make(**kw):
    base = {
        "source_id": "W1",
        "origin": "openalex",
        "title": "t",
        "authors": (),
        "year": None,
        "doi": None,
        "arxiv_id": None,
        "licence": LICENCE,
        "text": "x",
        "text_kind": "full_text",
    }
    return Candidate(**{**base, **kw})


@pytest.mark.parametrize(
    "kw",
    [
        {"doi": "javascript:alert(1)"},
        {"arxiv_id": "../x"},
        {"source_id": "X1"},
        {"source_id": "not-a-uuid", "origin": "own_upload"},
        {"title": "t" * 301},
        {"authors": tuple("a" for _ in range(51))},
        {"authors": ("a" * 101,)},
    ],
)
def test_candidate_rejects_bad_values(kw):
    with pytest.raises(ValueError):
        make(**kw)


def test_candidate_accepts_good_values_and_links():
    assert make(doi="10.1234/x").doi == "10.1234/x"
    assert make(origin="arxiv", source_id="2101.00001", arxiv_id="2101.00001v2")
    assert make(origin="arxiv", source_id="hep-th/9901001")
    assert make().link == "https://openalex.org/W1"
    assert source_link("openalex", "W123") == "https://openalex.org/W123"
    assert source_link("arxiv", "2101.00001") == "https://arxiv.org/abs/2101.00001"
    assert source_link("own_upload", OWN_ID) is None
    with pytest.raises(ValueError):
        source_link("openalex", "W1/../x")


# -- 12 vocabulary and purity ------------------------------------------------


def test_no_verdict_vocabulary_in_enums():
    banned = ("PLAGIARISM", "ORIGINAL", "MISCONDUCT", "CLEAN")
    for obj in vars(reuse_types).values():
        if isinstance(obj, type) and issubclass(obj, enum.Enum):
            for member in obj:
                assert not any(b in member.name.upper() for b in banned)


def test_module_is_pure_and_uses_public_citation_api():
    root = Path(reuse_types.__file__).parent
    pure = subprocess.run(
        [
            "grep",
            "-rnE",
            r"import (httpx|requests|socket|urllib)|open\(",
            str(root / "verbatim.py"),
            str(root / "types.py"),
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    assert pure.stdout == ""
    assert "_PATTERNS" not in "".join(p.read_text() for p in root.glob("*.py"))


# -- bug-finder regressions ----------------------------------------------------


def test_many_matches_on_one_long_line_stay_fast():
    doc = " ".join(f"u{i % 9}" for i in range(9 * 10000))
    src = " ".join(f"u{i % 9}" for i in range(9 * 60))
    t0 = time.monotonic()
    res = run(doc, [cand(src)])
    assert res.matches
    assert time.monotonic() - t0 < 10


def test_citation_after_match_in_appendix_counts():
    p = passage(20)
    doc = f'{sentence("a", 20)}\n\nReferences\n\n[1] A.\n\nAppendix A\n\n"{p}" (Smith, 2020) end.'
    assert attribution(doc, p) is Attribution.QUOTED_AND_CITED


def test_year_first_reference_entry_does_not_inflate_count():
    p = passage()
    doc = f"{sentence('a', 25)}. {p} [4].\n\nReferences\n\n2019. Foo bar.\n"
    assert attribution(doc) is Attribution.UNATTRIBUTED


@pytest.mark.parametrize(
    "kw",
    [
        {"source_id": "W" + chr(0x661)},
        {"doi": "10." + chr(0x661) * 4 + "/x"},
        {"doi": "10.1234/x\x00"},
    ],
)
def test_candidate_rejects_non_ascii_digits_and_control_chars(kw):
    with pytest.raises(ValueError):
        make(**kw)
