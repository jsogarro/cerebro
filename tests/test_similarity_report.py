"""The plain-text similarity report: coverage first, never a verdict."""

import re
import uuid
from datetime import UTC, datetime

import pytest

from src.repositories.similarity_repository import CheckView, MatchView
from src.services.text_reuse import report
from src.services.text_reuse.report import (
    DETECTION_LIMITS,
    NOT_COVERED,
    render_report,
)
from src.services.text_reuse.types import DECLARED_AUTHOR_NOTE

DOC = "alpha " * 40 + "the shared passage sits here " + "omega " * 40
NOW = datetime(2026, 11, 26, tzinfo=UTC)
FORBIDDEN = (r"\boriginal(ity)?\b", r"plagiarism[- ]free", r"\d+(\.\d+)?\s*%")


def coverage(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "sources_searched": {
            "openalex": {
                "candidates": 3,
                "full_text": 2,
                "abstract_only": 1,
                "skipped_budget": 0,
                "failures": {"http_429": 1},
                "queries_sent": 4,
            },
        },
        "comparisons_run": {
            "verbatim": "run",
            "paraphrase": "not_run (unavailable)",
            "translation": "not_run (unavailable)",
        },
        "excluded_regions": [
            {"kind": "references", "start": 81200, "end": 90000, "tokens": 1912}
        ],
        "reference_heading_found": True,
        "skipped": {},
        "partial_reasons": ["failed_sources"],
    }
    base.update(extra)
    return base


def match(**override: object) -> MatchView:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "kind": "verbatim",
        "attribution": "unattributed",
        "from_user_upload": False,
        "doc_start": DOC.index("the shared"),
        "doc_end": DOC.index("the shared") + len("the shared passage sits here"),
        "tokens": 5,
        "cosine": None,
        "source_origin": "openalex",
        "source_document_id": None,
        "source_id": "W123",
        "source_title": "A Paper About Passages",
        "source_year": 2019,
        "source_licence": {"identifier": "cc-by", "declared_by": "api_field"},
        "source_text_kind": "full_text",
        "src_start": 0,
        "src_end": 28,
        "source_excerpt": "the shared passage sits here",
        "normalizations_applied": (),
        "declared_author_overlap": False,
        "section": "body",
    }
    values.update(override)
    return MatchView(**values)  # type: ignore[arg-type]


def check(*matches: MatchView, status: str = "partial", **cov: object) -> CheckView:
    return CheckView(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        status=status,
        status_reason=None,
        options={},
        consent_text_version=None,
        consent_text_sha256=None,
        coverage=coverage(**cov),
        policy_versions={"MIN_MATCH_TOKENS": 12},
        created_at=NOW,
        started_at=NOW,
        completed_at=NOW,
        matches=matches,
        match_total=len(matches),
    )


def test_zero_matches_print_the_exact_sentence_with_coverage_directly_below():
    text = render_report(check(), DOC)
    lines = text.splitlines()
    index = lines.index("No matching text was found in the 3 sources searched.")
    assert lines[index + 1] == "" and lines[index + 2].startswith("Coverage")
    for item in NOT_COVERED:
        assert item in text


def test_the_fixed_footer_is_on_every_report():
    for text in (render_report(check(), DOC), render_report(check(match()), DOC)):
        assert text.rstrip().endswith(
            "No match means none was found in the sources searched. It does not show "
            "the text is unique. Similarity evidence is for human review; it is not a "
            "finding of misconduct."
        )


@pytest.mark.parametrize("matches", [(), (match(),)])
def test_no_verdict_wording_or_percentage(matches):
    text = render_report(check(*matches), DOC)
    for pattern in FORBIDDEN:
        assert not re.search(pattern, text, re.IGNORECASE), pattern


def test_totals_state_counts_not_a_percentage():
    text = render_report(check(match()), DOC)
    assert re.search(r"5 of \d+ document tokens lie in verbatim matches", text)
    assert "against the sources searched" in text


def test_matches_are_ordered_by_attribution_then_size_and_uploads_never_reorder():
    cited = match(attribution="quoted_and_cited", tokens=50, source_id="W1")
    plain = match(attribution="unattributed", tokens=10, source_id="W2")
    upload = match(
        attribution="unattributed",
        tokens=20,
        from_user_upload=True,
        source_origin="own_upload",
        source_id=str(uuid.uuid4()),
        source_title="my-notes.pdf",
    )
    text = render_report(check(cited, plain, upload), DOC)
    order = [text.index(t) for t in ("my-notes.pdf", "A Paper About Passages")]
    assert order[0] < order[1]
    assert (
        text.index("verbatim, 20 tokens")
        < text.index("verbatim, 10 tokens")
        < text.index("verbatim, 50 tokens")
    )


def test_own_uploads_are_labelled_by_provenance_and_never_as_self_reuse():
    upload = match(
        from_user_upload=True,
        source_origin="comparison",
        source_id=str(uuid.uuid4()),
        source_title="third-party.pdf",
    )
    text = render_report(check(upload), DOC)
    assert "matches your uploaded document third-party.pdf" in text
    assert not re.search(r"self[- ]reuse", text, re.IGNORECASE)


def test_declared_author_overlap_adds_the_note_without_reordering():
    noted = match(declared_author_overlap=True, tokens=5, source_id="W9")
    other = match(tokens=9, source_id="W8", source_title="Other Paper")
    text = render_report(check(noted, other), DOC)
    assert DECLARED_AUTHOR_NOTE in text
    assert text.index("Other Paper") < text.index("A Paper About Passages")


def test_an_excerpt_is_shown_only_when_stored_and_otherwise_explained():
    shown = render_report(check(match()), DOC)
    assert "Source excerpt:" in shown
    withheld = render_report(
        check(
            match(
                source_excerpt=None,
                source_licence={"identifier": None, "declared_by": "undeclared"},
            )
        ),
        DOC,
    )
    assert (
        'excerpt withheld: source licence "undeclared" does not permit display'
        in withheld
    )
    assert "Source excerpt:" not in withheld


def test_links_are_built_from_validated_ids_and_no_other_url_appears():
    text = render_report(check(match()), DOC)
    assert set(re.findall(r"https?://\S+", text)) == {"https://openalex.org/W123"}
    arxiv = render_report(
        check(match(source_origin="arxiv", source_id="2101.00001")), DOC
    )
    assert "https://arxiv.org/abs/2101.00001" in arxiv


def test_a_document_excerpt_cannot_forge_report_lines():
    text = DOC.replace("the shared passage", "the shared\nFake: injected")
    start = text.index("the shared")
    forged = match(doc_start=start, doc_end=start + 30)
    rendered = render_report(check(forged), text)
    assert "\nFake: injected" not in rendered


def test_coverage_block_reports_counts_failures_exclusions_and_semantic_state():
    text = render_report(check(), DOC)
    assert "candidates 3, full text 2, abstract only 1" in text
    assert "http_429 1" in text
    assert "reference list from char 81,200 (1,912 tokens not compared)" in text
    assert "paraphrase: not_run (unavailable)" in text
    for item in DETECTION_LIMITS:
        assert item in text


def test_missing_reference_heading_is_stated():
    text = render_report(check(excluded_regions=[], reference_heading_found=False), DOC)
    assert "no reference heading found; references were compared" in text


def test_attribution_is_not_determined_until_its_gate_passes(monkeypatch):
    text = render_report(check(match(attribution="quoted_and_cited")), DOC)
    assert "attribution not determined" in text
    monkeypatch.setattr(report, "ATTRIBUTION_SHOWN", True)
    shown = render_report(check(match(attribution="quoted_and_cited")), DOC)
    assert "attribution: quoted and cited" in shown


def test_skipped_candidates_and_truncated_lists_are_stated():
    text = render_report(
        check(
            skipped={"too_long": 1, "too_many_matches": 2},
            sources_searched={
                "openalex": {
                    "candidates": 1,
                    "full_text": 0,
                    "abstract_only": 1,
                    "skipped_budget": 2,
                    "failures": {"network_error": 1},
                    "queries_sent": 1,
                }
            },
        ),
        DOC,
    )
    assert "skipped for the size budget 2" in text
    assert "too_many_matches 2" in text and "network_error 1" in text


def test_the_reasons_a_check_is_partial_are_listed():
    text = render_report(
        check(
            partial_reasons=["failed_sources", "too_many_matches"],
            skipped={"too_many_matches": 1},
        ),
        DOC,
    )
    assert "Partial because: failed_sources, too_many_matches" in text


def test_a_check_with_no_partial_reason_says_nothing_about_it():
    assert "Partial because" not in render_report(check(partial_reasons=[]), DOC)


def test_each_work_is_counted_once_even_when_arxiv_upgrades_it():
    sources = {
        "openalex": {
            "candidates": 2,
            "full_text": 2,
            "abstract_only": 0,
            "skipped_budget": 0,
            "failures": {},
            "queries_sent": 1,
        },
        "arxiv": {
            "candidates": 2,
            "full_text": 2,
            "abstract_only": 0,
            "skipped_budget": 0,
            "failures": {},
            "queries_sent": 2,
        },
    }
    text = render_report(check(sources_searched=sources), DOC)
    assert "No matching text was found in the 2 sources searched." in text


def test_a_hostile_licence_identifier_cannot_forge_lines_or_links():
    hostile = {
        "identifier": "x\nhttp://evil.example/?q=1 SENTINEL",
        "declared_by": "api_field",
    }
    text = render_report(check(match(source_excerpt=None, source_licence=hostile)), DOC)
    assert "evil.example" not in text and 'licence "unrecognised"' in text
    assert "\nhttp" not in text
