import hashlib
import json

import pytest

from src.core.claims.citation_validation import (
    MAX_CITATIONS,
    CitationValidationError,
    FixtureCorpus,
    validate_citations,
)
from src.core.contracts.citation import CitationMetadata, SourcedValue
from src.core.contracts.outcomes import EvaluationStatus as S

DIMS = (
    "citation.reference_resolution",
    "citation.metadata_agreement",
    "citation.retraction_status",
    "citation.version_of_record",
)


def sv(value: str) -> SourcedValue:
    return SourcedValue(value=value, provenance="registry", evidence_id="ev-1")


def cite(doi: str | None, family: str = "Smith", year: str = "2020"):
    return CitationMetadata(
        authors=(sv(family),), year=sv(year), doi=sv(doi) if doi else None
    )


ENTRIES = [
    {"doi": "10.1000/ok", "first_author": "Smith", "year": 2020},
    {"doi": "10.1000/gone", "known_absent": True},
    {
        "doi": "10.1000/retracted",
        "first_author": "Smith",
        "year": 2020,
        "retracted": True,
    },
    {
        "doi": "10.1000/preprint",
        "first_author": "Smith",
        "year": 2020,
        "version_of_record_doi": "10.1000/final",
    },
]


def corpus() -> FixtureCorpus:
    text = json.dumps(ENTRIES)
    return FixtureCorpus.from_json(text, hashlib.sha256(text.encode()).hexdigest())


def by_dim(results):
    return {r.dimension: r for r in results}


def test_all_clear_passes_every_dimension():
    res = by_dim(validate_citations([cite("10.1000/ok")], corpus()))
    assert set(res) == set(DIMS)
    assert all(r.status is S.PASSED for r in res.values())


def test_doi_lookup_is_case_and_prefix_insensitive():
    res = by_dim(validate_citations([cite("https://doi.org/10.1000/OK")], corpus()))
    assert res["citation.reference_resolution"].status is S.PASSED


def test_absence_from_corpus_is_unknown_not_unresolvable():
    r = by_dim(validate_citations([cite("10.1000/unlisted")], corpus()))
    ref = r["citation.reference_resolution"]
    assert ref.status is S.ABSTAINED
    assert ref.error_code == "citation.unreachable"
    assert all(x.status is S.ABSTAINED for x in r.values())


def test_known_absent_fails_resolution():
    ref = by_dim(validate_citations([cite("10.1000/gone")], corpus()))[
        "citation.reference_resolution"
    ]
    assert ref.status is S.FAILED and ref.error_code == "citation.unresolvable"


@pytest.mark.parametrize(
    "doi", ["10.", "10.1000/", "10.1000/a b", "10.1\u0430000/x", "11.1000/x"]
)
def test_malformed_doi_fails(doi):
    # "10." and friends are defects in the artifact, not unknowns.
    ref = by_dim(validate_citations([cite(doi)], corpus()))[
        "citation.reference_resolution"
    ]
    assert ref.status is S.FAILED and ref.error_code == "citation.malformed_identifier"


def test_missing_doi_abstains():
    ref = by_dim(validate_citations([cite(None)], corpus()))[
        "citation.reference_resolution"
    ]
    assert ref.status is S.ABSTAINED and ref.error_code == "citation.no_identifier"


def test_retracted_fails_even_when_everything_else_passes():
    r = by_dim(validate_citations([cite("10.1000/retracted")], corpus()))
    assert r["citation.retraction_status"].status is S.FAILED
    assert r["citation.reference_resolution"].status is S.PASSED
    assert r["citation.metadata_agreement"].status is S.PASSED


def test_version_of_record_fails_and_names_it():
    vor = by_dim(validate_citations([cite("10.1000/preprint")], corpus()))[
        "citation.version_of_record"
    ]
    assert vor.status is S.FAILED
    assert vor.details["version_of_record"] == "10.1000/final"


def test_metadata_disagreement_is_advisory():
    r = by_dim(validate_citations([cite("10.1000/ok", family="Jones")], corpus()))
    m = r["citation.metadata_agreement"]
    assert m.status is S.FAILED and m.advisory is True
    assert all(not x.advisory for k, x in r.items() if k != m.dimension)


def test_year_within_tolerance_agrees_beyond_disagrees():
    near = by_dim(validate_citations([cite("10.1000/ok", year="2021")], corpus()))
    far = by_dim(validate_citations([cite("10.1000/ok", year="2023")], corpus()))
    assert near["citation.metadata_agreement"].status is S.PASSED
    assert far["citation.metadata_agreement"].status is S.FAILED


def test_rollup_failed_beats_abstained_beats_passed():
    r = by_dim(
        validate_citations(
            [cite("10.1000/ok"), cite("10.1000/unlisted"), cite("10.1000/gone")],
            corpus(),
        )
    )
    assert r["citation.reference_resolution"].status is S.FAILED
    r = by_dim(
        validate_citations([cite("10.1000/ok"), cite("10.1000/unlisted")], corpus())
    )
    assert r["citation.reference_resolution"].status is S.ABSTAINED


def test_zero_citations_is_never_passed():
    for res in validate_citations([], corpus()):
        assert res.status is S.ABSTAINED
        assert res.error_code == "citation.no_citations"
        assert res.details["citations_total"] == 0


def test_over_cap_is_error_not_truncation():
    res = validate_citations([cite("10.1000/ok")] * (MAX_CITATIONS + 1), corpus())
    assert {r.status for r in res} == {S.ERROR}
    assert all(r.error_code == "citation.too_many_citations" for r in res)


def test_corpus_hash_mismatch_refuses_to_load():
    with pytest.raises(CitationValidationError):
        FixtureCorpus.from_json(json.dumps(ENTRIES), "0" * 64)


def test_known_absent_cannot_carry_metadata():
    text = json.dumps([{"doi": "10.1000/x", "known_absent": True, "retracted": True}])
    with pytest.raises(CitationValidationError):
        FixtureCorpus.from_json(text, hashlib.sha256(text.encode()).hexdigest())


def test_results_are_deterministic():
    a = validate_citations([cite("10.1000/ok"), cite("10.1000/gone")], corpus())
    b = validate_citations([cite("10.1000/ok"), cite("10.1000/gone")], corpus())
    assert a == b


def test_every_status_the_module_emits_is_a_known_status():
    cases = [[], [cite(None)], [cite("10.1000/gone")], [cite("10.1000/ok")]]
    for c in cases:
        for r in validate_citations(c, corpus()):
            assert r.status in set(S)
            if r.status in {S.ABSTAINED, S.ERROR}:
                assert r.error_code
