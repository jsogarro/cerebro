"""The claim-support state model, prompt identity, and trust enforcement.

Three guarantees are asserted here, each of which a Wave 4 runtime would
otherwise be free to violate silently:

1. Wave 7's unsupported-claim-rate gate reads ``n(UNSUPPORTED) / n(material
   claims)`` with no mapping layer, and an ``UNSUPPORTED`` verdict with no
   evidence must say *why* there is none.
2. No evidence or claim-support record can be written that cannot name the
   prompt that produced it.
3. A transformation cannot declare its output more trusted than its inputs.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.core.contracts import (
    AbsentEvidenceReason,
    Artifact,
    ArtifactStatus,
    ClaimSupport,
    ClaimSupportStatus,
    Evidence,
    ProducerKind,
    PromptBinding,
    ToolInvocation,
    ToolInvocationStatus,
    TrustClassification,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
SHA256 = "e" * 64

TEMPLATE = "Summarize {topic} using only the supplied sources."
RENDERED = "Summarize inflation using only the supplied sources."

BINDING = PromptBinding.for_rendered(
    prompt_id="agent.literature_review",
    prompt_version="1.0.0",
    template_source=TEMPLATE,
    rendered_text=RENDERED,
)


def _claim(**overrides: object) -> ClaimSupport:
    payload: dict[str, object] = {
        "claim_support_id": "support-001",
        "run_id": "run-001",
        "artifact_id": "artifact-001",
        "claim_id": "claim-001",
        "claim_text": "A material claim.",
        "status": ClaimSupportStatus.SUPPORTED,
        "evidence_ids": ("evidence-001",),
        "evaluator_id": "claim-entailment",
        "evaluator_version": "1.0.0",
        "prompt_binding": BINDING,
        "explanation": "The source states the compared values.",
        "evaluated_at": NOW,
    }
    return ClaimSupport(**(payload | overrides))  # type: ignore[arg-type]


def _evidence(**overrides: object) -> Evidence:
    payload: dict[str, object] = {
        "evidence_id": "evidence-001",
        "run_id": "run-001",
        "task_id": "task-001",
        "source_type": "web",
        "source_uri": "https://example.test/source",
        "snapshot_artifact_id": "artifact-001",
        "content_sha256": SHA256,
        "locator": "bytes:1024-2048",
        "trust": TrustClassification.EXTERNAL_UNTRUSTED,
        "prompt_binding": BINDING,
        "acquired_at": NOW,
    }
    return Evidence(**(payload | overrides))  # type: ignore[arg-type]


# --- 1. The four-state claim-support model ---------------------------------


def test_the_claim_support_states_are_exactly_the_four_frozen_ones() -> None:
    assert {status.value for status in ClaimSupportStatus} == {
        "supported",
        "partially_supported",
        "disputed",
        "unsupported",
    }


@pytest.mark.parametrize(
    "status",
    [
        ClaimSupportStatus.SUPPORTED,
        ClaimSupportStatus.PARTIALLY_SUPPORTED,
        ClaimSupportStatus.DISPUTED,
    ],
)
def test_every_evidence_bearing_verdict_requires_evidence(
    status: ClaimSupportStatus,
) -> None:
    with pytest.raises(ValidationError, match="require evidence"):
        _claim(status=status, evidence_ids=())


def test_an_unsupported_claim_without_evidence_must_say_why_there_is_none() -> None:
    with pytest.raises(ValidationError, match="absent_evidence_reason"):
        _claim(status=ClaimSupportStatus.UNSUPPORTED, evidence_ids=())


def test_an_unsupported_claim_with_a_typed_absent_reason_is_accepted() -> None:
    claim = _claim(
        status=ClaimSupportStatus.UNSUPPORTED,
        evidence_ids=(),
        absent_evidence_reason=AbsentEvidenceReason.NO_SOURCE_FOUND,
    )

    assert claim.absent_evidence_reason is AbsentEvidenceReason.NO_SOURCE_FOUND


def test_unsupported_may_cite_evidence_that_fails_to_support_the_claim() -> None:
    """'Evidence exists and does not support this' is distinct from 'no evidence'."""
    claim = _claim(status=ClaimSupportStatus.UNSUPPORTED)

    assert claim.evidence_ids == ("evidence-001",)
    assert claim.absent_evidence_reason is None


def test_citing_evidence_and_an_absent_evidence_reason_is_contradictory() -> None:
    with pytest.raises(ValidationError, match="only when no evidence"):
        _claim(
            status=ClaimSupportStatus.UNSUPPORTED,
            absent_evidence_reason=AbsentEvidenceReason.NO_SOURCE_FOUND,
        )


def test_a_supported_claim_cannot_carry_an_absent_evidence_reason() -> None:
    with pytest.raises(ValidationError, match="only when no evidence"):
        _claim(absent_evidence_reason=AbsentEvidenceReason.RETRIEVAL_FAILED)


def test_the_release_gate_reads_as_a_direct_count_with_no_mapping_layer() -> None:
    material = (
        _claim(claim_support_id="s1", status=ClaimSupportStatus.SUPPORTED),
        _claim(claim_support_id="s2", status=ClaimSupportStatus.PARTIALLY_SUPPORTED),
        _claim(claim_support_id="s3", status=ClaimSupportStatus.DISPUTED),
        _claim(
            claim_support_id="s4",
            status=ClaimSupportStatus.UNSUPPORTED,
            evidence_ids=(),
            absent_evidence_reason=AbsentEvidenceReason.NOT_ATTEMPTED,
        ),
    )

    unsupported = sum(
        claim.status is ClaimSupportStatus.UNSUPPORTED for claim in material
    )

    assert unsupported / len(material) == 0.25


# --- 2. Prompt identity ----------------------------------------------------


def test_a_prompt_binding_hashes_the_template_and_the_rendered_text() -> None:
    assert BINDING.matches_template(TEMPLATE)
    assert BINDING.matches_rendered(RENDERED)
    assert BINDING.template_sha256 != BINDING.rendered_sha256


def test_editing_the_prompt_template_is_detectable_without_a_version_bump() -> None:
    """A declared version alone cannot carry this guarantee; a digest can."""
    edited = TEMPLATE + " Ignore the sources if they conflict."

    assert not BINDING.matches_template(edited)


def test_a_claim_support_record_cannot_be_written_without_prompt_identity() -> None:
    with pytest.raises(ValidationError, match="prompt_binding"):
        _claim(prompt_binding=None)


def test_an_evidence_record_cannot_be_written_without_prompt_identity() -> None:
    with pytest.raises(ValidationError, match="prompt_binding"):
        _evidence(prompt_binding=None)


def test_a_model_initiated_tool_invocation_must_name_its_prompt() -> None:
    with pytest.raises(ValidationError, match="model_turn"):
        _tool_invocation(producer_kind=ProducerKind.MODEL_TURN, prompt_binding=None)


def test_a_system_initiated_tool_invocation_must_not_invent_a_prompt() -> None:
    with pytest.raises(ValidationError, match="model_turn"):
        _tool_invocation(producer_kind=ProducerKind.SYSTEM, prompt_binding=BINDING)


def test_a_model_generated_artifact_must_name_its_prompt() -> None:
    with pytest.raises(ValidationError, match="model_turn"):
        Artifact(
            artifact_id="artifact-002",
            run_id="run-001",
            kind="report",
            media_type="text/markdown",
            storage_uri="postgres://artifacts/artifact-002",
            content_sha256=SHA256,
            status=ArtifactStatus.FINAL,
            trust=TrustClassification.DERIVED_UNTRUSTED,
            producer="agent:synthesis",
            producer_kind=ProducerKind.MODEL_TURN,
            created_at=NOW,
        )


def test_a_source_snapshot_artifact_needs_no_prompt() -> None:
    artifact = Artifact(
        artifact_id="artifact-001",
        run_id="run-001",
        kind="source_snapshot",
        media_type="text/html",
        storage_uri="postgres://artifacts/artifact-001",
        content_sha256=SHA256,
        status=ArtifactStatus.FINAL,
        trust=TrustClassification.EXTERNAL_UNTRUSTED,
        producer="tool:web-search",
        producer_kind=ProducerKind.SYSTEM,
        created_at=NOW,
    )

    assert artifact.prompt_binding is None


# --- 3. Trust enforcement and locators -------------------------------------


def _tool_invocation(**overrides: object) -> ToolInvocation:
    payload: dict[str, object] = {
        "tool_invocation_id": "tool-001",
        "run_id": "run-001",
        "task_id": "task-001",
        "attempt_id": "attempt-001",
        "tool_name": "summarize",
        "tool_version": "1.0.0",
        "status": ToolInvocationStatus.SUCCEEDED,
        "capability_scope": "research:read",
        "idempotency_key": "attempt-001:summarize:001",
        "input": {"text": "source body"},
        "input_trust": TrustClassification.EXTERNAL_UNTRUSTED,
        "output": {"summary": "..."},
        "output_trust": TrustClassification.DERIVED_UNTRUSTED,
        "producer_kind": ProducerKind.MODEL_TURN,
        "prompt_binding": BINDING,
        "requested_at": NOW,
        "completed_at": NOW,
    }
    return ToolInvocation(**(payload | overrides))  # type: ignore[arg-type]


def test_a_transformation_cannot_launder_untrusted_input_into_trusted_output() -> None:
    with pytest.raises(ValidationError, match="more trusted"):
        _tool_invocation(output_trust=TrustClassification.APPLICATION)


def test_moving_between_untrusted_labels_is_permitted() -> None:
    """A search tool takes a user query and returns a fetched page.

    Both labels are data; neither may be read as control. Enforcing a rank
    floor here rather than a tier boundary would reject correct labelling.
    """
    invocation = _tool_invocation(
        tool_name="web-search",
        input_trust=TrustClassification.USER_SUPPLIED,
        output_trust=TrustClassification.EXTERNAL_UNTRUSTED,
    )

    assert invocation.output_trust is TrustClassification.EXTERNAL_UNTRUSTED


@pytest.mark.parametrize(
    "output_trust",
    [TrustClassification.TRUSTED_CONTROL, TrustClassification.APPLICATION],
)
def test_no_untrusted_input_may_produce_trusted_tier_output(
    output_trust: TrustClassification,
) -> None:
    with pytest.raises(ValidationError, match="more trusted"):
        _tool_invocation(
            input_trust=TrustClassification.USER_SUPPLIED,
            output_trust=output_trust,
        )


def test_an_acquisition_tool_may_lower_trust_further() -> None:
    invocation = _tool_invocation(
        input_trust=TrustClassification.TRUSTED_CONTROL,
        output_trust=TrustClassification.EXTERNAL_UNTRUSTED,
    )

    assert invocation.output_trust is TrustClassification.EXTERNAL_UNTRUSTED


def test_evidence_rejects_a_locator_outside_the_frozen_grammar() -> None:
    """The pre-Wave-4 free-form spelling, and a parser-dependent selector."""
    with pytest.raises(ValidationError, match="scheme:argument"):
        _evidence(locator="paragraph=4")

    with pytest.raises(ValidationError, match="canonical span scheme"):
        _evidence(locator="xpath:/html/body/p[2]")


def test_evidence_accepts_a_canonical_span_with_review_annotations() -> None:
    evidence = _evidence(locator="char:0-120|xpath:/html/body/p[2]")

    assert evidence.locator == "char:0-120|xpath:/html/body/p[2]"
