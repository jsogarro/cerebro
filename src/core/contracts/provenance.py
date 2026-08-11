"""Tool, artifact, evidence, and claim-provenance records.

Three Wave 4 guarantees are enforced here rather than merely described.

**Prompt identity.** Every **model-produced** record names the prompt that
produced it. Prompts resolve at runtime by agent type, so a declared version
cannot carry this: editing a prompt body leaves the version string untouched
and silently changes what an already-admitted run means.
:class:`PromptBinding` therefore carries digests of both the template source
and the exact rendered text, which no edit can leave unchanged.

Records that legitimately have no prompt say so with :class:`ProducerKind`
rather than by omission, so the absence is typed instead of forgotten. All four
provenance records follow the same rule, and the uniformity is deliberate: an
*unconditional* requirement does not produce the guarantee, it produces
fabrication. A deterministic evaluator — Wave 5's schema, citation-resolution,
entailment, contradiction, and numerical checks, and the resolver that writes
``unsupported``/``not_attempted`` for a claim nobody examined — has no prompt to
name, and forcing one would mint a binding over a template that was never sent
to a model. That record would satisfy every digest check while being a lie the
contract vouches for. Deterministic verdicts stay attributable through
``evaluator_id``/``evaluator_version`` and the run's pinned component digest.

**No trust laundering.** A tool invocation with untrusted input cannot declare
trusted-tier output. The constraint is on the tier, not the rank: moving
between untrusted labels is legitimate — a search tool consumes a
``user_supplied`` query and returns an ``external_untrusted`` page — while
lowering trust further is always permitted, so an acquisition tool reading a
trusted instruction and returning an untrusted web page is exactly right.

**Resolvable evidence.** A locator must conform to the frozen grammar in
:mod:`src.core.contracts.locators`, whose first segment is a parser-free span
into an immutable, content-addressed snapshot.
"""

import hashlib
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from .base import (
    ContentSha256,
    ContractId,
    ContractModel,
    IdempotencyKey,
    JsonObject,
    Version,
    require_unique,
)
from .locators import InvalidLocatorError, parse_locator
from .trust import TrustClassification, is_trusted_tier

__all__ = [
    "AbsentEvidenceReason",
    "Artifact",
    "ArtifactStatus",
    "ClaimSupport",
    "ClaimSupportStatus",
    "Evidence",
    "ProducerKind",
    "PromptBinding",
    "ToolInvocation",
    "ToolInvocationStatus",
    "TrustClassification",
]


class ToolInvocationStatus(StrEnum):
    REQUESTED = "requested"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    DENIED = "denied"


class ArtifactStatus(StrEnum):
    DRAFT = "draft"
    FINAL = "final"
    INVALIDATED = "invalidated"


class ProducerKind(StrEnum):
    """Whether a record was produced under a prompt, or by the system.

    This exists so "no prompt" is a decision rather than a default. A nullable
    ``prompt_binding`` alone would let a model-produced record be written with
    the field simply left unset.
    """

    MODEL_TURN = "model_turn"
    SYSTEM = "system"


class ClaimSupportStatus(StrEnum):
    """The verdict connecting one material claim to evidence.

    ``unsupported`` is deliberately the only negative state that a release gate
    counts. The earlier model conflated two different things under
    ``insufficient`` — "some evidence, not enough" and "no evidence at all" —
    which made the gate ambiguous at exactly the point it is read.
    ``partially_supported`` now carries the former; ``unsupported`` carries the
    latter and says why. ``disputed`` replaces ``contradicted``, because
    sources disagreeing with each other is not the same as a source refuting
    the claim, and treating them alike hid the distinction a reviewer needs.
    """

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    DISPUTED = "disputed"
    UNSUPPORTED = "unsupported"


EVIDENCE_BEARING_STATUSES: frozenset[ClaimSupportStatus] = frozenset(
    {
        ClaimSupportStatus.SUPPORTED,
        ClaimSupportStatus.PARTIALLY_SUPPORTED,
        ClaimSupportStatus.DISPUTED,
    }
)
"""Verdicts that are meaningless without evidence to point at."""


class AbsentEvidenceReason(StrEnum):
    """Why an unsupported claim has no evidence at all.

    Typed rather than free text because a release gate and a triage queue both
    read it: "the fetch was blocked" and "we never looked" call for different
    responses, and a prose explanation cannot be aggregated across a corpus.
    The human-readable ``explanation`` accompanies it; it does not replace it.
    """

    NOT_ATTEMPTED = "not_attempted"
    """No retrieval was run for this claim."""

    NO_SOURCE_FOUND = "no_source_found"
    """Retrieval ran and returned nothing relevant."""

    RETRIEVAL_FAILED = "retrieval_failed"
    """Retrieval errored, timed out, or tripped a circuit breaker."""

    SOURCE_INACCESSIBLE = "source_inaccessible"
    """A source was identified but could not be fetched or snapshotted."""

    CAPABILITY_DENIED = "capability_denied"
    """No capability grant permitted the lookup this claim needed."""


class PromptBinding(ContractModel):
    """The identity of the prompt that produced one provenance record.

    ``prompt_version`` is what the codebase declares. ``template_sha256`` and
    ``rendered_sha256`` are what actually ran. The declared version is
    convenient; the digests are the guarantee, because a prompt edit that skips
    a version bump changes both digests and cannot change the version.

    ``rendered_sha256`` covers the **redacted** prompt text — the exact bytes
    sent to the model. That obliges the renderer to redact the *assembled* text
    as its final step, before hashing. Redacting the variables beforehand is not
    sufficient: a variable that never crossed the tool boundary, such as a query
    arriving straight from the API or a value read back from storage, reaches
    the renderer unredacted. Because redaction is idempotent, scrubbing an
    assembled prompt whose parts were already scrubbed changes nothing, so the
    final pass is safe to apply unconditionally.

    Nothing in this contract enforces that the digests correspond to the
    material they claim to cover — :meth:`for_rendered` computes honest ones,
    but a caller may construct this model with arbitrary hex. Enforcement
    belongs to the renderer seam, which must pair the digests with the material
    and refuse a mismatch.
    """

    prompt_id: ContractId
    prompt_version: Version
    template_sha256: ContentSha256
    rendered_sha256: ContentSha256

    @classmethod
    def for_rendered(
        cls,
        *,
        prompt_id: str,
        prompt_version: str,
        template_source: str,
        rendered_text: str,
    ) -> Self:
        """Build a binding by hashing the template and the text it rendered.

        Constructing a binding this way rather than by hand is what keeps the
        digests honest: there is no path where a caller supplies a digest that
        does not correspond to text it actually has.
        """

        return cls(
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            template_sha256=_digest(template_source),
            rendered_sha256=_digest(rendered_text),
        )

    def matches_template(self, template_source: str) -> bool:
        """Return whether ``template_source`` is the template this pinned."""

        return self.template_sha256 == _digest(template_source)

    def matches_rendered(self, rendered_text: str) -> bool:
        """Return whether ``rendered_text`` is the text this pinned."""

        return self.rendered_sha256 == _digest(rendered_text)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_producer_binding(
    producer_kind: ProducerKind, prompt_binding: PromptBinding | None
) -> None:
    """Enforce the biconditional between producer kind and prompt identity."""

    if producer_kind is ProducerKind.MODEL_TURN and prompt_binding is None:
        raise ValueError("a model_turn record must name the prompt that produced it")
    if producer_kind is ProducerKind.SYSTEM and prompt_binding is not None:
        raise ValueError("only a model_turn record may carry a prompt_binding")


class ToolInvocation(ContractModel):
    """An auditable request to one versioned tool capability."""

    tool_invocation_id: ContractId
    run_id: ContractId
    task_id: ContractId
    attempt_id: ContractId
    tool_name: ContractId
    tool_version: Version
    status: ToolInvocationStatus
    capability_scope: ContractId
    idempotency_key: IdempotencyKey
    input: JsonObject
    input_trust: TrustClassification
    output: JsonObject | None = None
    output_trust: TrustClassification | None = None
    producer_kind: ProducerKind = ProducerKind.SYSTEM
    prompt_binding: PromptBinding | None = None
    error_code: ContractId | None = None
    status_reason: str | None = Field(default=None, min_length=1)
    requested_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_status_fields(self) -> Self:
        terminal = self.status in {
            ToolInvocationStatus.SUCCEEDED,
            ToolInvocationStatus.FAILED,
            ToolInvocationStatus.CANCELLED,
            ToolInvocationStatus.TIMED_OUT,
            ToolInvocationStatus.DENIED,
        }
        if terminal != (self.completed_at is not None):
            raise ValueError("Only terminal tool invocations require completed_at")
        if self.completed_at is not None and self.completed_at < self.requested_at:
            raise ValueError("completed_at cannot precede requested_at")
        if self.status is ToolInvocationStatus.SUCCEEDED and (
            self.output is None or self.output_trust is None
        ):
            raise ValueError("Successful tool invocations require output provenance")
        if (
            self.status
            in {
                ToolInvocationStatus.FAILED,
                ToolInvocationStatus.TIMED_OUT,
                ToolInvocationStatus.DENIED,
            }
            and self.error_code is None
        ):
            raise ValueError("Failed tool invocations require error_code")
        _validate_producer_binding(self.producer_kind, self.prompt_binding)
        self._validate_output_trust()
        return self

    def _validate_output_trust(self) -> None:
        """Reject untrusted input laundered into trusted output.

        The constraint is on the **tier**, not the rank. Moving between
        untrusted labels is legitimate and common — a search tool takes a
        ``user_supplied`` query and returns an ``external_untrusted`` page, and
        neither may be read as control. What no tool may do is consume data and
        hand back something labelled as control.

        The rule is also one-sided: an acquisition tool invoked under trusted
        control returns external content and must be free to say so.
        """

        if self.output_trust is None:
            return
        if is_trusted_tier(self.output_trust) and not is_trusted_tier(self.input_trust):
            raise ValueError(
                f"output_trust {self.output_trust.value!r} is more trusted than "
                f"{self.input_trust.value!r} input permits; untrusted input "
                "cannot produce trusted-tier output"
            )


class Artifact(ContractModel):
    """Immutable content metadata stored outside transient agent context."""

    artifact_id: ContractId
    run_id: ContractId
    task_id: ContractId | None = None
    attempt_id: ContractId | None = None
    kind: ContractId
    media_type: ContractId
    storage_uri: str = Field(min_length=1)
    content_sha256: ContentSha256
    status: ArtifactStatus
    trust: TrustClassification
    producer: ContractId
    producer_kind: ProducerKind = ProducerKind.SYSTEM
    prompt_binding: PromptBinding | None = None
    created_at: AwareDatetime
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_producer(self) -> Self:
        _validate_producer_binding(self.producer_kind, self.prompt_binding)
        return self


class Evidence(ContractModel):
    """Stable locator into an immutable source snapshot.

    ``content_sha256`` is the digest of the snapshot's verbatim acquired bytes,
    unredacted, per the redaction contract: evidence integrity means "this is
    what the source said", and scrubbing the snapshot would both change that
    and shift every offset ``locator`` depends on.
    """

    evidence_id: ContractId
    run_id: ContractId
    task_id: ContractId
    source_type: ContractId
    source_uri: str = Field(min_length=1)
    snapshot_artifact_id: ContractId
    content_sha256: ContentSha256
    locator: str = Field(min_length=1)
    trust: TrustClassification
    producer_kind: ProducerKind
    prompt_binding: PromptBinding | None = None
    producer_tool_invocation_id: ContractId | None = None
    parent_evidence_ids: tuple[ContractId, ...] = ()
    acquired_at: AwareDatetime

    @model_validator(mode="after")
    def validate_provenance_chain(self) -> Self:
        if self.evidence_id in self.parent_evidence_ids:
            raise ValueError("Evidence cannot derive from itself")
        require_unique(self.parent_evidence_ids, field_name="parent_evidence_ids")
        try:
            parse_locator(self.locator)
        except InvalidLocatorError as error:
            raise ValueError(str(error)) from error
        _validate_producer_binding(self.producer_kind, self.prompt_binding)
        return self


class ClaimSupport(ContractModel):
    """A versioned evaluator judgment connecting one claim to evidence.

    ``prompt_binding`` names the **evaluator's** prompt — the one that produced
    this judgment. The prompt that generated the claim itself is named by the
    ``prompt_binding`` of the artifact identified by ``artifact_id``.
    """

    claim_support_id: ContractId
    run_id: ContractId
    artifact_id: ContractId
    claim_id: ContractId
    claim_text: str = Field(min_length=1)
    status: ClaimSupportStatus
    evidence_ids: tuple[ContractId, ...] = ()
    absent_evidence_reason: AbsentEvidenceReason | None = None
    evaluator_id: ContractId
    evaluator_version: Version
    producer_kind: ProducerKind
    prompt_binding: PromptBinding | None = None
    explanation: str = Field(min_length=1)
    evaluated_at: AwareDatetime

    @model_validator(mode="after")
    def validate_evidence_links(self) -> Self:
        require_unique(self.evidence_ids, field_name="evidence_ids")
        _validate_producer_binding(self.producer_kind, self.prompt_binding)

        if self.status in EVIDENCE_BEARING_STATUSES and not self.evidence_ids:
            raise ValueError(
                f"{self.status.value} claims require evidence; only "
                f"{ClaimSupportStatus.UNSUPPORTED.value} may have none"
            )
        if self.evidence_ids and self.absent_evidence_reason is not None:
            raise ValueError(
                "absent_evidence_reason is set only when no evidence is cited"
            )
        if (
            self.status is ClaimSupportStatus.UNSUPPORTED
            and not self.evidence_ids
            and self.absent_evidence_reason is None
        ):
            raise ValueError(
                "an unsupported claim with no evidence requires an explicit "
                "absent_evidence_reason"
            )
        if (
            self.status is not ClaimSupportStatus.UNSUPPORTED
            and self.absent_evidence_reason is not None
        ):
            raise ValueError(
                "absent_evidence_reason is set only when no evidence is cited"
            )
        return self
