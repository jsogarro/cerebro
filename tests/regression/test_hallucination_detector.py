"""Regression tests for trusted-source hallucination checks."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from src.services.evaluation_regression import (
    ExpectedCitation,
    GoldenDatasetCase,
    load_golden_dataset,
)
from src.services.hallucination_detector import HallucinationDetector

DATASET_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "golden_dataset.json"


def test_detector_accepts_supported_claims_and_known_citations() -> None:
    case = _golden_case()

    report = HallucinationDetector().detect(
        " ".join(case.expected_insights),
        citations=case.expected_citations,
        trusted_sources=case.trusted_sources,
    )

    assert report.has_hallucinations is False
    assert len(report.supported_claims) == len(case.expected_insights)
    assert report.unsupported_claims == ()
    assert report.contradicted_claims == ()
    assert report.missing_citations == ()


def test_detector_flags_false_claim_from_trusted_source_contradictions() -> None:
    case = _golden_case()
    contradicted_claim = "CRISPR-Cas9 never cuts off-target genomic sites."
    trusted_source = replace(
        case.trusted_sources[0],
        contradicted_claims=(contradicted_claim,),
    )

    report = HallucinationDetector().detect(
        f"{case.expected_insights[0]} {contradicted_claim}",
        citations=case.expected_citations,
        trusted_sources=(trusted_source,),
    )

    assert report.has_hallucinations is True
    assert [claim.claim for claim in report.contradicted_claims] == [
        contradicted_claim,
    ]
    assert report.unsupported_claims == ()


def test_detector_flags_unsupported_claim_without_source_evidence() -> None:
    case = _golden_case()
    unsupported_claim = "CRISPR-Cas9 cures every inherited disease today."

    report = HallucinationDetector().detect(
        f"{case.expected_insights[0]} {unsupported_claim}",
        citations=case.expected_citations,
        trusted_sources=case.trusted_sources,
    )

    assert report.has_hallucinations is True
    assert [claim.claim for claim in report.unsupported_claims] == [
        unsupported_claim,
    ]
    assert report.contradicted_claims == ()


def test_detector_flags_citations_missing_from_trusted_sources() -> None:
    case = _golden_case()
    invented_citation = ExpectedCitation(
        title="Invented Genome Editing Trial",
        url="https://example.invalid/invented-genome-editing-trial",
        source_type="paper",
    )

    report = HallucinationDetector().detect(
        case.expected_insights[0],
        citations=(invented_citation,),
        trusted_sources=case.trusted_sources,
    )

    assert report.has_hallucinations is True
    assert len(report.missing_citations) == 1
    assert report.missing_citations[0].citation.title == invented_citation.title


def test_detector_accepts_mapping_inputs_from_golden_fixture_shape() -> None:
    case = _golden_case()
    citation = {
        "title": case.expected_citations[0].title,
        "url": case.expected_citations[0].url,
        "source_type": case.expected_citations[0].source_type,
    }
    trusted_source = {
        "title": case.trusted_sources[0].title,
        "url": case.trusted_sources[0].url,
        "source_type": case.trusted_sources[0].source_type,
        "snippet": case.trusted_sources[0].snippet,
        "supported_claims": list(case.trusted_sources[0].supported_claims),
        "contradicted_claims": [],
    }

    report = HallucinationDetector().detect(
        case.expected_insights[0],
        citations=(citation,),
        trusted_sources=(trusted_source,),
    )

    assert report.has_hallucinations is False
    assert report.citation_checks[0].present_in_trusted_sources is True


def _golden_case() -> GoldenDatasetCase:
    return load_golden_dataset(DATASET_PATH).cases[0]
