import random
from datetime import UTC, datetime

import pytest

from src.core.claims.citation_binding import CitationBindingError, bind_citations
from src.core.contracts.citation import (
    BindingGapReason,
    CitationStatus,
    ClaimSpan,
    MetadataCandidate,
)
from src.core.contracts.provenance import (
    AbsentEvidenceReason,
    ClaimSupport,
    ClaimSupportStatus,
    Evidence,
    ProducerKind,
)
from src.core.contracts.trust import TrustClassification

S = ClaimSupportStatus
R = BindingGapReason
TEXT = "Attention helps. Scale matters."
SHA_A = "a" * 64
SHA_B = "b" * 64


def ev(evidence_id: str, sha: str = SHA_A, locator: str = "char:0-10") -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        run_id="run-1",
        task_id="task-1",
        source_type="pdf",
        source_uri="https://example.org/a.pdf",
        snapshot_artifact_id="art-1",
        content_sha256=sha,
        locator=locator,
        trust=TrustClassification.EXTERNAL_UNTRUSTED,
        producer_kind=ProducerKind.SYSTEM,
        acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def sup(claim_id: str, status: S = S.SUPPORTED, *evidence_ids: str) -> ClaimSupport:
    absent = None
    if status is S.UNSUPPORTED and not evidence_ids:
        absent = AbsentEvidenceReason.NOT_ATTEMPTED
    return ClaimSupport(
        claim_support_id=f"cs-{claim_id}",
        run_id="run-1",
        artifact_id="art-1",
        claim_id=claim_id,
        claim_text="claim",
        status=status,
        evidence_ids=evidence_ids,
        absent_evidence_reason=absent,
        evaluator_id="det",
        evaluator_version="1",
        producer_kind=ProducerKind.SYSTEM,
        explanation="x",
        evaluated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def cand(field, value, provenance="registry", evidence_id="ev-1", position=0):
    return MetadataCandidate(
        field=field,
        value=value,
        provenance=provenance,
        evidence_id=evidence_id,
        position=position,
    )


FULL = (
    cand("author", "Vaswani", position=0),
    cand("author", "Shazeer", position=1),
    cand("author", "Parmar", position=2),
    cand("year", "2017"),
    cand("doi", "10.1000/xyz"),
)
SPAN1 = ClaimSpan(claim_id="c1", start=0, end=16, evidence_ids=("ev-1",))


def bind(spans=(SPAN1,), candidates=FULL, status=S.SUPPORTED, evidence=None, text=TEXT):
    evidence = evidence if evidence is not None else {"ev-1": ev("ev-1")}
    return bind_citations(
        text,
        spans,
        evidence=evidence,
        support={s.claim_id: sup(s.claim_id, status, "ev-1") for s in spans},
        metadata={"ev-1": candidates},
    )


def test_full_binding_renders_after_claim_span_with_provenance():
    out = bind()
    (rec,) = out.records
    assert rec.status is CitationStatus.MINTED
    assert rec.in_text == "(Vaswani et al., 2017) https://doi.org/10.1000/xyz"
    assert out.rendered_text.startswith("Attention helps. (Vaswani")
    assert out.rendered_text.endswith(" Scale matters.")
    assert out.gaps == () and out.clean
    fields = [*rec.metadata.authors, rec.metadata.year, rec.metadata.doi]
    assert all(f.provenance.value == "registry" and f.evidence_id for f in fields)


def test_independent_bindings_are_byte_identical():
    assert bind().canonical_json().encode() == bind().canonical_json().encode()


def test_citation_id_ignores_input_ordering_and_run_identity():
    baseline = bind()
    for seed in range(10):
        shuffled = list(FULL)
        random.Random(seed).shuffle(shuffled)
        again = bind(candidates=tuple(shuffled), evidence={"ev-1": ev("ev-1")})
        assert again.canonical_json() == baseline.canonical_json()
    other_run = ev("ev-1").model_copy(update={"run_id": "run-2", "task_id": "t-2"})
    moved = bind(evidence={"ev-1": other_run})
    assert moved.records[0].citation_id == baseline.records[0].citation_id


def test_citation_id_depends_on_content():
    base = bind().records[0].citation_id
    assert base is not None
    other_sha = bind(evidence={"ev-1": ev("ev-1", SHA_B)}).records[0].citation_id
    other_loc = (
        bind(evidence={"ev-1": ev("ev-1", locator="char:10-20")}).records[0].citation_id
    )
    other_year = (
        bind(candidates=(*FULL[:3], cand("year", "2018"), FULL[4]))
        .records[0]
        .citation_id
    )
    assert len({base, other_sha, other_loc, other_year}) == 4


@pytest.mark.parametrize("field", ["author", "year", "doi"])
def test_model_inferred_values_are_rejected_never_rendered(field):
    inferred = cand(field, "Hallucinated", "model_inferred", evidence_id=None)
    if field == "year":
        inferred = cand(field, "1999", "model_inferred", evidence_id=None)
    kept = tuple(c for c in FULL if c.field != field)
    out = bind(candidates=(*kept, inferred))
    assert any(
        g.reason is R.METADATA_UNRENDERABLE and g.field == field for g in out.gaps
    )
    assert "Hallucinated" not in out.rendered_text and "1999" not in out.rendered_text
    assert not out.clean


def test_required_field_only_model_inferred_is_incomplete_gap_not_a_guess():
    out = bind(
        candidates=(
            cand("author", "Smith"),
            cand("year", "2020", "model_inferred", evidence_id=None),
        )
    )
    (rec,) = out.records
    assert rec.status is CitationStatus.INCOMPLETE
    assert rec.citation_id is None
    assert rec.in_text == "[citation incomplete: no year]"
    reasons = {g.reason for g in out.gaps}
    assert {R.METADATA_UNRENDERABLE, R.METADATA_MISSING} <= reasons


def test_missing_doi_is_minted_partial_with_missing_fields():
    out = bind(candidates=FULL[:4])
    (rec,) = out.records
    assert rec.status is CitationStatus.MINTED_PARTIAL
    assert rec.missing_fields == ("doi",)
    assert rec.citation_id is not None and out.gaps == ()


def test_unsupported_claim_gets_marker_no_citation_and_gap():
    out = bind(status=S.UNSUPPORTED)
    (rec,) = out.records
    assert rec.status is CitationStatus.UNSUPPORTED and rec.citation_id is None
    assert "Vaswani" not in out.rendered_text
    assert [g.reason for g in out.gaps] == [R.CLAIM_UNSUPPORTED]


def test_missing_claim_support_record_is_a_gap_not_an_assumption():
    out = bind_citations(
        TEXT,
        (SPAN1,),
        evidence={"ev-1": ev("ev-1")},
        support={},
        metadata={"ev-1": FULL},
    )
    assert [g.reason for g in out.gaps] == [R.NO_CLAIM_SUPPORT]
    assert out.records[0].citation_id is None


def test_disputed_claim_carries_distinct_marker():
    out = bind(status=S.DISPUTED)
    assert out.records[0].in_text.endswith(" [disputed]")
    assert out.records[0].support is S.DISPUTED


def test_partial_support_marker():
    out = bind(status=S.PARTIALLY_SUPPORTED)
    assert out.records[0].in_text.endswith(" [partial support]")


def test_claim_without_evidence_slot_is_gap():
    out = bind(spans=(ClaimSpan(claim_id="c1", start=0, end=16),))
    assert out.records == ()
    assert [g.reason for g in out.gaps] == [R.NO_EVIDENCE_SLOT]


def test_evidence_not_in_run_is_per_claim_gap_not_failure():
    spans = (
        SPAN1,
        ClaimSpan(claim_id="c2", start=17, end=31, evidence_ids=("ghost",)),
    )
    out = bind(spans=spans)
    assert len(out.records) == 1
    (gap,) = out.gaps
    assert gap.reason is R.EVIDENCE_NOT_IN_RUN and gap.evidence_id == "ghost"


def test_metadata_citing_unknown_evidence_is_rejected():
    forged = cand("year", "2017", "registry", evidence_id="ghost")
    out = bind(candidates=(*FULL[:3], forged))
    assert out.records[0].metadata.year is None
    assert any(
        g.reason is R.EVIDENCE_NOT_IN_RUN and g.field == "year" for g in out.gaps
    )


def test_user_supplied_needs_no_evidence_id():
    c = (
        cand("author", "Lee", "user_supplied", None),
        cand("year", "2021", "user_supplied", None),
    )
    rec = bind(candidates=c).records[0]
    assert rec.metadata.year.provenance.value == "user_supplied"


def test_conflicting_values_bind_nothing():
    out = bind(candidates=(*FULL, cand("year", "2018")))
    assert out.records[0].metadata.year is None
    assert any(g.reason is R.METADATA_CONFLICT and g.field == "year" for g in out.gaps)


def test_residual_citation_prose_is_flagged_outside_rendered_spans():
    text = "Attention helps (Vaswani et al., 2017). Scale matters."
    out = bind(
        text=text,
        spans=(ClaimSpan(claim_id="c1", start=0, end=39, evidence_ids=("ev-1",)),),
    )
    prose = [g for g in out.gaps if g.reason is R.UNRENDERED_CITATION_PROSE]
    assert len(prose) == 1 and "Vaswani et al., 2017" in prose[0].detail
    assert not out.clean


def test_rendered_citations_are_not_flagged_as_prose():
    assert not [g for g in bind().gaps if g.reason is R.UNRENDERED_CITATION_PROSE]


@pytest.mark.parametrize(
    "span",
    [(0, 0), (5, 3), (0, 999)],
)
def test_bad_spans_raise(span):
    with pytest.raises((CitationBindingError, ValueError)):
        bind(
            spans=(
                ClaimSpan.model_construct(
                    claim_id="c1", start=span[0], end=span[1], evidence_ids=("ev-1",)
                ),
            )
        )


def test_duplicate_claim_ids_raise():
    with pytest.raises(CitationBindingError):
        bind(spans=(SPAN1, SPAN1))


def test_multiple_claims_offsets_and_markers_line_up():
    spans = (
        SPAN1,
        ClaimSpan(claim_id="c2", start=17, end=31, evidence_ids=("ev-1",)),
    )
    out = bind(spans=spans)
    assert out.rendered_text.count("(Vaswani et al., 2017)") == 2
    assert not out.gaps


def test_evidence_not_cited_by_the_verdict_is_not_bound():
    span = ClaimSpan(claim_id="c1", start=0, end=16, evidence_ids=("ev-2",))
    out = bind_citations(
        TEXT,
        (span,),
        evidence={"ev-1": ev("ev-1"), "ev-2": ev("ev-2", SHA_B)},
        support={"c1": sup("c1", S.SUPPORTED, "ev-1")},
        metadata={"ev-2": FULL},
    )
    assert out.records == ()
    (gap,) = out.gaps
    assert gap.reason is R.EVIDENCE_NOT_IN_SUPPORT and gap.evidence_id == "ev-2"


def test_conflicting_authors_at_one_position_bind_no_authors():
    out = bind(candidates=(*FULL, cand("author", "Zed", position=0)))
    assert out.records[0].metadata.authors == ()
    assert any(
        g.reason is R.METADATA_CONFLICT and g.field == "author" for g in out.gaps
    )


def test_reversed_author_orders_from_two_sources_conflict():
    out = bind(
        candidates=(
            cand("author", "A", position=0),
            cand("author", "B", position=1),
            cand("author", "B", position=0, evidence_id="ev-2"),
            cand("author", "A", position=1, evidence_id="ev-2"),
            cand("year", "2020"),
        ),
        evidence={"ev-1": ev("ev-1"), "ev-2": ev("ev-2", SHA_B)},
    )
    assert out.records[0].metadata.authors == ()
    assert any(g.reason is R.METADATA_CONFLICT for g in out.gaps)


def test_coauthors_sharing_a_surname_are_both_kept():
    out = bind(
        candidates=(
            cand("author", "Lee", position=0),
            cand("author", "Lee", position=1),
            cand("year", "2020"),
        )
    )
    assert out.records[0].in_text == "(Lee and Lee, 2020)"


def test_duplicate_identical_author_candidates_collapse():
    out = bind(
        candidates=(
            cand("author", "Lee"),
            cand("author", "Lee", "user_supplied", None),
            cand("year", "2020"),
        )
    )
    assert out.records[0].in_text == "(Lee, 2020)"


def test_span_end_inside_a_word_raises():
    with pytest.raises(CitationBindingError):
        bind(spans=(ClaimSpan(claim_id="c1", start=0, end=5, evidence_ids=("ev-1",)),))


def test_typed_citation_split_by_an_insertion_point_is_still_flagged():
    text = "Attention helps (Vaswani et al., 2017)."
    span = ClaimSpan(claim_id="c1", start=0, end=24, evidence_ids=("ev-1",))
    out = bind(text=text, spans=(span,))
    assert any(
        g.reason is R.UNRENDERED_CITATION_PROSE and "Vaswani et al., 2017" in g.detail
        for g in out.gaps
    )


def test_multiple_evidence_rows_per_claim_each_get_a_record():
    span = ClaimSpan(claim_id="c1", start=0, end=16, evidence_ids=("ev-2", "ev-1"))
    out = bind_citations(
        TEXT,
        (span,),
        evidence={"ev-1": ev("ev-1"), "ev-2": ev("ev-2", SHA_B)},
        support={"c1": sup("c1", S.SUPPORTED, "ev-1", "ev-2")},
        metadata={"ev-1": FULL, "ev-2": (cand("author", "Lee"), cand("year", "2020"))},
    )
    assert [r.evidence_id for r in out.records] == ["ev-1", "ev-2"]
    assert out.rendered_text.startswith(
        "Attention helps. (Vaswani et al., 2017) https://doi.org/10.1000/xyz (Lee, 2020)"
    )
    assert out.gaps == ()


def test_spans_with_equal_ends_both_render_in_stable_order():
    spans = (
        ClaimSpan(claim_id="b", start=0, end=16, evidence_ids=("ev-1",)),
        ClaimSpan(claim_id="a", start=10, end=16, evidence_ids=("ev-1",)),
    )
    out = bind(spans=spans)
    assert out.rendered_text.count("(Vaswani et al., 2017)") == 2
    assert out.gaps == ()
    assert out.canonical_json() == bind(spans=spans[::-1]).canonical_json()


def test_overlapping_spans_render_at_their_own_ends():
    spans = (
        ClaimSpan(claim_id="a", start=0, end=16, evidence_ids=("ev-1",)),
        ClaimSpan(claim_id="b", start=10, end=31, evidence_ids=("ev-1",)),
    )
    out = bind(spans=spans)
    assert out.rendered_text.count("(Vaswani et al., 2017)") == 2
    assert out.rendered_text.endswith(
        "Scale matters. (Vaswani et al., 2017) https://doi.org/10.1000/xyz"
    )


def test_doi_conflict_binds_no_doi():
    out = bind(candidates=(*FULL, cand("doi", "10.2000/other")))
    assert out.records[0].metadata.doi is None
    assert any(g.reason is R.METADATA_CONFLICT and g.field == "doi" for g in out.gaps)


def test_metadata_may_cite_a_different_evidence_row_in_the_run():
    out = bind(
        candidates=(*FULL[:3], cand("year", "2017", evidence_id="ev-2")),
        evidence={"ev-1": ev("ev-1"), "ev-2": ev("ev-2", SHA_B)},
    )
    assert out.records[0].metadata.year.evidence_id == "ev-2"
    assert not any(g.reason is R.EVIDENCE_NOT_IN_RUN for g in out.gaps)


def test_missing_support_renders_marker_with_no_support_value():
    out = bind_citations(
        TEXT,
        (SPAN1,),
        evidence={"ev-1": ev("ev-1")},
        support={},
        metadata={"ev-1": FULL},
    )
    assert out.records[0].support is None
    assert "[unsupported claim: no citation rendered]" in out.rendered_text
