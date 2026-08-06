"""Totality: every material claim leaves the resolver carrying a verdict.

The failure this guards against is not a wrong verdict — it is *no* verdict.
A claim nobody evaluated, silently absent from the output, costs nothing
visible: the gate divides a smaller numerator by a smaller denominator and
reports a healthier number. So these tests are mostly about what cannot be
constructed rather than about what a correct run produces.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from src.core.claims import build_inventory
from src.core.claims.resolution import (
    RESOLVER_EVALUATOR_ID,
    ClaimSupportResolution,
    ClaimSupportResolver,
    ClaimVerdict,
    DuplicateVerdictError,
    IncompleteResolutionError,
    UnknownClaimError,
)
from src.core.contracts import (
    AbsentEvidenceReason,
    ClaimSupport,
    ClaimSupportStatus,
    ProducerKind,
    PromptBinding,
)

NOW = datetime(2026, 8, 6, tzinfo=UTC)
RUN_ID = "run-1"
ARTIFACT_ID = "artifact-1"

TWO_CLAIM_SOURCE = (
    "The model improves accuracy by five percent.\n"
    "Latency fell from 210ms to 180ms in the same run.\n"
)

THREE_CLAIM_SOURCE = (
    "The model improves accuracy by five percent.\n"
    "Latency fell from 210ms to 180ms in the same run.\n"
    "Memory use was unchanged across every configuration tested.\n"
)

EVALUATOR_BINDING = PromptBinding(
    prompt_id="entailment-check",
    prompt_version="1.0",
    template_sha256="a" * 64,
    rendered_sha256="b" * 64,
)


def _ids() -> Iterator[str]:
    counter = 0
    while True:
        counter += 1
        yield f"claim-support-{counter}"


def _resolver() -> ClaimSupportResolver:
    sequence = _ids()
    return ClaimSupportResolver(id_factory=lambda: next(sequence))


def _inventory(source: str = TWO_CLAIM_SOURCE):  # type: ignore[no-untyped-def]
    return build_inventory(
        artifact_id=ARTIFACT_ID,
        artifact_kind="research_report",
        source=source,
    )


def _verdict(claim_id: str, **overrides: object) -> ClaimVerdict:
    values: dict[str, object] = {
        "claim_id": claim_id,
        "status": ClaimSupportStatus.SUPPORTED,
        "evidence_ids": ("evidence-1",),
        "explanation": "Table 3 reports the same figure.",
        "evaluator_id": "entailment-evaluator",
        "evaluator_version": "1.0",
        "producer_kind": ProducerKind.MODEL_TURN,
        "prompt_binding": EVALUATOR_BINDING,
        "evaluated_at": NOW,
    }
    values.update(overrides)
    return ClaimVerdict(**values)  # type: ignore[arg-type]


def test_a_claim_no_evaluator_reached_is_recorded_unsupported_and_not_attempted() -> (
    None
):
    inventory = _inventory()
    assert inventory.material_claim_count == 2, "need one evaluated, one not"

    resolution = _resolver().resolve(
        inventory=inventory,
        run_id=RUN_ID,
        verdicts=[_verdict(inventory.claims[0].claim_id)],
        resolved_at=NOW,
    )

    filled = resolution.support_for(inventory.claims[1].claim_id)
    assert filled.status is ClaimSupportStatus.UNSUPPORTED
    assert filled.absent_evidence_reason is AbsentEvidenceReason.NOT_ATTEMPTED
    assert filled.evidence_ids == ()


def test_never_evaluated_is_distinguishable_from_evaluated_and_unsupported() -> None:
    """Both are ``unsupported``. Only one of them is a finding.

    This is the reason the four-state model keeps ``absent_evidence_reason``
    rather than folding "we looked and found nothing" together with "we never
    looked": a triage queue has to separate a retrieval gap from a claim the
    sources genuinely do not carry, and the status alone cannot.
    """
    inventory = _inventory()
    evaluated_unsupported = _verdict(
        inventory.claims[0].claim_id,
        status=ClaimSupportStatus.UNSUPPORTED,
        evidence_ids=(),
        absent_evidence_reason=AbsentEvidenceReason.NO_SOURCE_FOUND,
        explanation="Searched the corpus; nothing addresses this figure.",
    )

    resolution = _resolver().resolve(
        inventory=inventory,
        run_id=RUN_ID,
        verdicts=[evaluated_unsupported],
        resolved_at=NOW,
    )

    looked = resolution.support_for(inventory.claims[0].claim_id)
    never_looked = resolution.support_for(inventory.claims[1].claim_id)

    assert looked.status is never_looked.status is ClaimSupportStatus.UNSUPPORTED
    assert looked.absent_evidence_reason is AbsentEvidenceReason.NO_SOURCE_FOUND
    assert never_looked.absent_evidence_reason is AbsentEvidenceReason.NOT_ATTEMPTED
    assert looked.evaluator_id != never_looked.evaluator_id


def test_a_fill_in_verdict_says_no_model_decided_it() -> None:
    """The audit question is "was this judgment made by a model?".

    A fill-in row is the resolver's own statement that nobody looked, so it
    declares ``system`` and names no prompt. Until packet 4A amended the
    contract, ``prompt_binding`` was required unconditionally and the only way
    to write this row was to hash an invented template — every digest honest,
    the identity a fabrication, and this question unanswerable from the record.
    """
    inventory = _inventory()

    resolution = _resolver().resolve(
        inventory=inventory, run_id=RUN_ID, verdicts=[], resolved_at=NOW
    )

    for support in resolution.supports:
        assert support.producer_kind is ProducerKind.SYSTEM
        assert support.prompt_binding is None
        assert support.evaluator_id == RESOLVER_EVALUATOR_ID


def test_a_deterministic_evaluator_may_return_a_verdict_with_no_prompt() -> None:
    """Wave 5's schema, citation and numerical checks call no model at all.

    They must be able to record a verdict without naming a prompt, or every one
    of them inherits the fabrication this seam just stopped doing.
    """
    inventory = _inventory()
    deterministic = _verdict(
        inventory.claims[0].claim_id,
        producer_kind=ProducerKind.SYSTEM,
        prompt_binding=None,
        evaluator_id="citation-resolution-check",
        explanation="Every citation resolved to a snapshot.",
    )

    resolution = _resolver().resolve(
        inventory=inventory,
        run_id=RUN_ID,
        verdicts=[deterministic],
        resolved_at=NOW,
    )

    support = resolution.support_for(inventory.claims[0].claim_id)
    assert support.producer_kind is ProducerKind.SYSTEM
    assert support.prompt_binding is None
    assert support.evaluator_id == "citation-resolution-check"


def test_a_model_verdict_that_names_no_prompt_is_refused() -> None:
    """The other half of the biconditional, which is the half that protects.

    Making ``prompt_binding`` optional is only safe because declaring
    ``model_turn`` still requires one. Without this, the amendment would have
    turned a fabricated attribution into a missing one — and a model judgment
    recorded as unattributable is exactly what D4b exists to prevent.
    """
    inventory = _inventory()
    unattributed = _verdict(
        inventory.claims[0].claim_id,
        producer_kind=ProducerKind.MODEL_TURN,
        prompt_binding=None,
    )
    assert unattributed.producer_kind is ProducerKind.MODEL_TURN

    with pytest.raises(ValueError, match="model_turn"):
        _resolver().resolve(
            inventory=inventory,
            run_id=RUN_ID,
            verdicts=[unattributed],
            resolved_at=NOW,
        )


def test_every_material_claim_appears_exactly_once() -> None:
    inventory = _inventory()

    resolution = _resolver().resolve(
        inventory=inventory,
        run_id=RUN_ID,
        verdicts=[],
        resolved_at=NOW,
    )

    recorded = [support.claim_id for support in resolution.supports]
    assert sorted(recorded) == sorted(claim.claim_id for claim in inventory.claims)
    assert len(recorded) == len(set(recorded))


def test_a_resolution_missing_a_claim_cannot_be_constructed() -> None:
    """The set-level check is the subject, so every row must be well-formed.

    Without the per-row precondition below, this test would pass just as
    happily against a constructor that rejected a malformed ``ClaimSupport``
    and never compared the set to the inventory at all — and a green suite
    would look identical either way.
    """
    inventory = _inventory()
    covered = _support_for(inventory.claims[0].claim_id)
    assert isinstance(covered, ClaimSupport), "the one row must be valid on its own"
    assert covered.claim_id == inventory.claims[0].claim_id
    assert inventory.material_claim_count == 2

    with pytest.raises(IncompleteResolutionError) as error:
        ClaimSupportResolution(
            inventory=inventory,
            run_id=RUN_ID,
            supports=(covered,),
        )

    assert inventory.claims[1].claim_id in str(error.value)


def test_a_resolution_carrying_a_claim_the_inventory_does_not_contain_is_refused() -> (
    None
):
    """An extra row is as much a mismatch as a missing one.

    It means the evaluator and the denominator disagree about what the artifact
    says, and accepting it would let the numerator count a claim the
    denominator never counted.
    """
    inventory = _inventory()
    stranger_id = "char:9000-9100"
    assert stranger_id not in {claim.claim_id for claim in inventory.claims}

    supports = tuple(_support_for(claim.claim_id) for claim in inventory.claims)
    supports += (_support_for(stranger_id),)

    with pytest.raises(IncompleteResolutionError) as error:
        ClaimSupportResolution(inventory=inventory, run_id=RUN_ID, supports=supports)

    assert stranger_id in str(error.value)


def test_a_verdict_about_an_unlisted_claim_raises_rather_than_being_dropped() -> None:
    inventory = _inventory()
    stranger_id = "char:9000-9100"
    assert stranger_id not in {claim.claim_id for claim in inventory.claims}

    with pytest.raises(UnknownClaimError):
        _resolver().resolve(
            inventory=inventory,
            run_id=RUN_ID,
            verdicts=[_verdict(stranger_id)],
            resolved_at=NOW,
        )


def test_two_verdicts_for_one_claim_raise_rather_than_one_quietly_winning() -> None:
    inventory = _inventory()
    claim_id = inventory.claims[0].claim_id
    first = _verdict(claim_id, status=ClaimSupportStatus.SUPPORTED)
    second = _verdict(
        claim_id,
        status=ClaimSupportStatus.UNSUPPORTED,
        evidence_ids=(),
        absent_evidence_reason=AbsentEvidenceReason.NO_SOURCE_FOUND,
    )
    assert first.status is not second.status, "the two must actually disagree"

    with pytest.raises(DuplicateVerdictError):
        _resolver().resolve(
            inventory=inventory,
            run_id=RUN_ID,
            verdicts=[first, second],
            resolved_at=NOW,
        )


def test_an_evaluator_verdict_reaches_the_record_unchanged() -> None:
    inventory = _inventory()
    claim_id = inventory.claims[0].claim_id

    resolution = _resolver().resolve(
        inventory=inventory,
        run_id=RUN_ID,
        verdicts=[
            _verdict(
                claim_id,
                status=ClaimSupportStatus.DISPUTED,
                evidence_ids=("evidence-1", "evidence-2"),
            )
        ],
        resolved_at=NOW,
    )

    support = resolution.support_for(claim_id)
    assert support.status is ClaimSupportStatus.DISPUTED
    assert support.evidence_ids == ("evidence-1", "evidence-2")
    assert support.evaluator_id == "entailment-evaluator"
    assert support.prompt_binding == EVALUATOR_BINDING
    assert support.claim_text == inventory.claims[0].text
    assert support.artifact_id == ARTIFACT_ID
    assert support.run_id == RUN_ID


def test_the_gate_reads_two_integers_from_one_place() -> None:
    inventory = _inventory()

    resolution = _resolver().resolve(
        inventory=inventory,
        run_id=RUN_ID,
        verdicts=[_verdict(inventory.claims[0].claim_id)],
        resolved_at=NOW,
    )

    assert resolution.material_claim_count == 2
    assert resolution.unsupported_count == 1
    assert resolution.unsupported_ratio == 0.5


def test_only_unsupported_counts_toward_the_gate() -> None:
    """``disputed`` and ``partially_supported`` are findings, not failures.

    Widening the numerator to "anything short of supported" is the change this
    guards, and it is an easy one to make while tidying: it reads as stricter,
    and it would fail a release for every claim two sources argued about. The
    four-state model exists precisely so a reviewer can see those without a
    gate counting them.
    """
    inventory = _inventory(THREE_CLAIM_SOURCE)
    assert inventory.material_claim_count == 3

    resolution = _resolver().resolve(
        inventory=inventory,
        run_id=RUN_ID,
        verdicts=[
            _verdict(inventory.claims[0].claim_id, status=ClaimSupportStatus.DISPUTED),
            _verdict(
                inventory.claims[1].claim_id,
                status=ClaimSupportStatus.PARTIALLY_SUPPORTED,
            ),
            _verdict(inventory.claims[2].claim_id, status=ClaimSupportStatus.SUPPORTED),
        ],
        resolved_at=NOW,
    )

    assert {support.status for support in resolution.supports} == {
        ClaimSupportStatus.DISPUTED,
        ClaimSupportStatus.PARTIALLY_SUPPORTED,
        ClaimSupportStatus.SUPPORTED,
    }
    assert resolution.unsupported_count == 0
    assert resolution.unsupported_ratio == 0.0


def test_an_empty_denominator_reports_no_ratio_rather_than_a_passing_one() -> None:
    """``0/0`` is an absence of information, not a clean bill of health.

    Returning ``0.0`` here would let an artifact whose claims all fell out of
    the inventory sail through a "ratio below threshold" gate.
    """
    inventory = _inventory("## Results\n")
    assert inventory.material_claim_count == 0

    resolution = _resolver().resolve(
        inventory=inventory, run_id=RUN_ID, verdicts=[], resolved_at=NOW
    )

    assert resolution.unsupported_ratio is None


def _support_for(claim_id: str) -> ClaimSupport:
    return ClaimSupport(
        claim_support_id=f"cs-{claim_id}",
        run_id=RUN_ID,
        artifact_id=ARTIFACT_ID,
        claim_id=claim_id,
        claim_text="A claim.",
        status=ClaimSupportStatus.SUPPORTED,
        evidence_ids=("evidence-1",),
        evaluator_id="entailment-evaluator",
        evaluator_version="1.0",
        producer_kind=ProducerKind.MODEL_TURN,
        prompt_binding=EVALUATOR_BINDING,
        explanation="Because the source says so.",
        evaluated_at=NOW,
    )
