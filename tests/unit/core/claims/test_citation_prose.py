"""Citation-shaped prose that the renderer did not emit must be detected.

A model given an excerpt from a well-known paper writes ``(Vaswani et al.,
2017)`` from parametric recall with no metadata at all. A prompt instruction
not to do so is a request, not an enforcement; this scan is the enforcement.
"""

import pytest

from src.core.claims.citation_prose import (
    DETECTOR_VERSION,
    CitationProseKind,
    find_unrendered_citation_prose,
)


@pytest.mark.parametrize(
    ("text", "kind", "match"),
    [
        ("as shown (Vaswani et al., 2017).", "author_year", "(Vaswani et al., 2017)"),
        (
            "prior work (Smith & Jones 2020a) found",
            "author_year",
            "(Smith & Jones 2020a)",
        ),
        (
            "prior work (Smith and Jones, 2020) found",
            "author_year",
            "(Smith and Jones, 2020)",
        ),
        ("Vaswani et al. (2017) introduced", "author_year", "Vaswani et al. (2017)"),
        ("Smith (2020) argues", "author_year", "Smith (2020)"),
        ("as reported [12].", "numeric", "[12]"),
        ("as reported [3, 7\u20139].", "numeric", "[3, 7\u20139]"),
        ("see doi:10.1145/3292500.3330919 for", "doi", "doi:10.1145/3292500.3330919"),
        ("at https://doi.org/10.1000/xyz123.", "doi", "https://doi.org/10.1000/xyz123"),
        ("the DOI 10.48550/arXiv.2305.08740 is", "doi", "10.48550/arXiv.2305.08740"),
        ("preprint arXiv:2305.08740v2 shows", "arxiv", "arXiv:2305.08740v2"),
    ],
)
def test_detects_each_citation_shape(text: str, kind: str, match: str) -> None:
    (finding,) = find_unrendered_citation_prose(text)

    assert finding.kind is CitationProseKind(kind)
    assert finding.text == match
    assert text[finding.start : finding.end] == match


@pytest.mark.parametrize(
    "text",
    [
        "Revenue grew in 2020 (up 4%) and fell in 2021.",
        "The model (see Appendix B) uses 12 layers.",
        "Indexes like x[0] and matrix[i] are code, not citations.",
        "In (2019) terms the ratio held.",
    ],
)
def test_ordinary_prose_is_not_flagged(text: str) -> None:
    assert find_unrendered_citation_prose(text) == ()


def test_renderer_emitted_citations_are_excluded_by_position() -> None:
    rendered = "(Smith, 2020)"
    text = f"First claim {rendered}. Second claim (Smith, 2020) again."

    findings = find_unrendered_citation_prose(text, rendered_spans=[(12, 25)])

    assert [(f.start, f.text) for f in findings] == [(40, "(Smith, 2020)")]


def test_findings_are_ordered_and_deterministic() -> None:
    text = "arXiv:2101.00001 then (Lee, 2019) then [4]."

    first = find_unrendered_citation_prose(text)

    assert [f.kind for f in first] == [
        CitationProseKind.ARXIV,
        CitationProseKind.AUTHOR_YEAR,
        CitationProseKind.NUMERIC,
    ]
    assert find_unrendered_citation_prose(text) == first
    assert DETECTOR_VERSION
