import pytest
from pydantic import ValidationError

from src.core.claims.citation_render import (
    AUTHOR_DATE_MINIMAL,
    CitationStyleUnavailableError,
    render,
)
from src.core.contracts.citation import CitationMetadata, SourcedValue
from src.core.contracts.provenance import ClaimSupportStatus as S


def sv(value: str, provenance: str = "registry") -> SourcedValue:
    return SourcedValue(value=value, provenance=provenance, evidence_id="ev-1")


def meta(
    *families: str, year: str | None = "2020", doi: str | None = None
) -> CitationMetadata:
    return CitationMetadata(
        authors=tuple(sv(f) for f in families),
        year=sv(year) if year else None,
        doi=sv(doi) if doi else None,
    )


def r(m: CitationMetadata, support: S = S.SUPPORTED):
    return render(m, AUTHOR_DATE_MINIMAL, support=support)


def test_single_author_golden():
    assert r(meta("Smith")).text.encode() == b"(Smith, 2020)"


def test_two_authors_golden():
    assert r(meta("Smith", "Jones")).text.encode() == b"(Smith and Jones, 2020)"


def test_three_authors_et_al_golden():
    assert r(meta("Smith", "Jones", "Lee")).text.encode() == b"(Smith et al., 2020)"


def test_doi_golden_and_span_covers_it():
    out = r(meta("Smith", doi="10.1000/xyz"))
    assert out.text == "(Smith, 2020) https://doi.org/10.1000/xyz"
    assert out.spans == ((0, len(out.text)),)


def test_missing_year_is_explicit_gap():
    out = r(meta("Smith", year=None))
    assert out.text == "[citation incomplete: no year]"
    assert out.missing_fields == ("year",)
    assert "n.d." not in out.text


def test_missing_author_is_explicit_gap():
    out = r(meta())
    assert out.text == "[citation incomplete: no author]"
    assert out.missing_fields == ("author",)


def test_complete_has_no_missing_fields():
    assert r(meta("Smith")).missing_fields == ()


def test_model_inferred_rejected_at_construction():
    with pytest.raises(ValidationError, match="never renderable"):
        SourcedValue(value="Smith", provenance="model_inferred", evidence_id="ev-1")


@pytest.mark.parametrize("provenance", ["registry", "document"])
def test_evidence_backed_requires_evidence_id(provenance):
    with pytest.raises(ValidationError, match="evidence_id"):
        SourcedValue(value="Smith", provenance=provenance)


def test_user_supplied_needs_no_evidence_id():
    SourcedValue(value="Smith", provenance="user_supplied")


def test_unknown_style_typed_error():
    with pytest.raises(CitationStyleUnavailableError) as e:
        render(meta("Smith"), "apa", support=S.SUPPORTED)
    assert e.value.code == "citation.style_unavailable"


def test_disputed_marker_distinct_from_supported():
    supported = r(meta("Smith"))
    disputed = r(meta("Smith"), S.DISPUTED)
    assert disputed.text == "(Smith, 2020) [disputed]"
    assert disputed.text != supported.text
    assert disputed.spans == ((0, len("(Smith, 2020)")),)


def test_partial_marker():
    assert (
        r(meta("Smith"), S.PARTIALLY_SUPPORTED).text
        == "(Smith, 2020) [partial support]"
    )


def test_unsupported_never_a_citation():
    out = r(meta("Smith", doi="10.1000/xyz"), S.UNSUPPORTED)
    assert out.spans == ()
    assert (
        "Smith" not in out.text and "2020" not in out.text and "10.1000" not in out.text
    )


def test_deterministic():
    m = meta("Smith", "Jones", doi="10.1000/xyz")
    assert {r(m, S.DISPUTED).text for _ in range(5)} == {r(m, S.DISPUTED).text}
