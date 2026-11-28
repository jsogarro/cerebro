"""Fixture claim support: a label stands only when its quote is in the evidence."""

import hashlib
import json
from datetime import UTC, datetime

import pytest

from src.core.contracts import AbsentEvidenceReason, ClaimSupportStatus
from src.core.evaluation.fixture_support import (
    EvidenceExcerpt,
    FixtureManifest,
    FixtureManifestError,
    judge,
)

NOW = datetime(2026, 11, 27, tzinfo=UTC)
SNAPSHOT = "Aspirin reduced the risk of stroke in the trial cohort by 18 percent."
DIGEST = hashlib.sha256(SNAPSHOT.encode()).hexdigest()
CLAIM = "Aspirin reduced stroke risk by 18 percent."
NEGATED = "Aspirin did not reduce stroke risk by 18 percent."


def _manifest(**entry: object) -> FixtureManifest:
    base = {
        "claim_text": CLAIM,
        "status": "supported",
        "evidence_sha256": DIGEST,
        "quote": "reduced the risk of stroke in the trial cohort by 18 percent",
        "explanation": "Stated verbatim.",
    }
    text = json.dumps({"manifest_id": "m", "entries": [{**base, **entry}]})
    return FixtureManifest.from_json(text, hashlib.sha256(text.encode()).hexdigest())


def _evidence(excerpt: str | None = SNAPSHOT) -> list[EvidenceExcerpt]:
    return [EvidenceExcerpt("ev-1", DIGEST, excerpt)]


def _judge(manifest: FixtureManifest, text: str, evidence: list[EvidenceExcerpt]):
    return judge(manifest, [("c1", text)], evidence, evaluated_at=NOW)


def test_a_labelled_claim_whose_quote_is_in_the_evidence_is_supported() -> None:
    (verdict,) = _judge(_manifest(), CLAIM, _evidence()).verdicts

    assert verdict.status is ClaimSupportStatus.SUPPORTED
    assert verdict.evidence_ids == ("ev-1",)
    assert verdict.producer_kind.value == "system"
    assert "method=containment" in verdict.explanation


def test_a_negated_claim_is_not_inherited_from_its_positive_label() -> None:
    result = _judge(_manifest(), NEGATED, _evidence())

    assert result.verdicts == ()
    assert result.unmapped == 1


def test_a_tampered_span_is_never_supported() -> None:
    tampered = SNAPSHOT.replace("reduced", "increased")

    (verdict,) = _judge(_manifest(), CLAIM, _evidence(tampered)).verdicts

    assert verdict.status is ClaimSupportStatus.UNSUPPORTED
    assert "not in the evidence" in verdict.explanation


def test_unreadable_evidence_never_supports_and_is_an_integrity_failure() -> None:
    result = _judge(_manifest(), CLAIM, _evidence(None))

    (verdict,) = result.verdicts
    assert verdict.status is ClaimSupportStatus.UNSUPPORTED
    assert verdict.absent_evidence_reason is AbsentEvidenceReason.RETRIEVAL_FAILED
    assert result.integrity_failures == 1


def test_a_label_for_evidence_the_run_does_not_hold_is_unsupported() -> None:
    other = [EvidenceExcerpt("ev-9", "f" * 64, SNAPSHOT)]

    (verdict,) = _judge(_manifest(), CLAIM, other).verdicts

    assert verdict.status is ClaimSupportStatus.UNSUPPORTED
    assert verdict.evidence_ids == ()


def test_a_negative_label_is_recorded_unsupported() -> None:
    (verdict,) = _judge(
        _manifest(claim_text=NEGATED, status="unsupported"), NEGATED, _evidence()
    ).verdicts

    assert verdict.status is ClaimSupportStatus.UNSUPPORTED


def test_a_manifest_with_the_wrong_hash_is_refused() -> None:
    with pytest.raises(FixtureManifestError):
        FixtureManifest.from_json("{}", "0" * 64)


def test_unreadable_evidence_spares_claims_resting_on_good_evidence() -> None:
    other = "Zinc shortened colds in the trial."
    other_digest = hashlib.sha256(other.encode()).hexdigest()
    text = json.dumps(
        {
            "manifest_id": "m",
            "entries": [
                {
                    "claim_text": CLAIM,
                    "status": "supported",
                    "evidence_sha256": DIGEST,
                    "quote": "reduced the risk of stroke",
                    "explanation": "x",
                },
                {
                    "claim_text": "Zinc shortened colds.",
                    "status": "supported",
                    "evidence_sha256": other_digest,
                    "quote": "shortened colds",
                    "explanation": "x",
                },
            ],
        }
    )
    manifest = FixtureManifest.from_json(
        text, hashlib.sha256(text.encode()).hexdigest()
    )
    evidence = [
        EvidenceExcerpt("ev-1", DIGEST, None),
        EvidenceExcerpt("ev-2", other_digest, other),
    ]

    result = judge(
        manifest,
        [("c1", CLAIM), ("c2", "Zinc shortened colds.")],
        evidence,
        evaluated_at=NOW,
    )

    assert [v.status for v in result.verdicts] == [
        ClaimSupportStatus.UNSUPPORTED,
        ClaimSupportStatus.SUPPORTED,
    ]


@pytest.mark.parametrize(
    "bad",
    [{"year": 2020}, {"authors": "Smith"}, {"doi": 5}, {"quote": ""}],
)
def test_a_manifest_entry_of_the_wrong_shape_is_refused_at_load(bad: dict) -> None:
    base = {
        "claim_text": CLAIM,
        "status": "supported",
        "evidence_sha256": DIGEST,
        "quote": "q",
        "explanation": "e",
    }
    text = json.dumps({"manifest_id": "m", "entries": [{**base, **bad}]})

    with pytest.raises(FixtureManifestError):
        FixtureManifest.from_json(text, hashlib.sha256(text.encode()).hexdigest())
