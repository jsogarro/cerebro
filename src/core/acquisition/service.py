"""Acquiring a source and attaching it to a durable run as evidence.

This is the seam packet 4A's non-guarantee 3 names. Two properties are worth
reading the code for, because they are the ones a reviewer should attack.

**A caller cannot declare its own content trusted.** ``acquire`` has no
parameter that sets the acquired content's trust label — not an optional one,
not one with a default. The label comes from
:func:`~src.core.acquisition.sources.trust_for_source` over a closed enum whose
mapping is total and whose codomain excludes the trusted tier. The caller does
supply ``input_trust``, but that describes the *request* it is making, not the
content that comes back, and the boundary's own one-sided rule already forbids
untrusted input producing trusted-tier output. A caller declaring
``trusted_control`` on everything it controls still receives an artifact
labelled ``external_untrusted``.

**Every persisted evidence row has been resolved before it is written.** Spans
are resolved against the verbatim snapshot bytes *first*, for all of them, and
only then is anything persisted. A locator that cannot resolve therefore never
reaches a row, so "this citation does not resolve" is not a state the database
can hold.

**Failure is typed, never silent.** A denial, a timeout, an open breaker, a
tool that raised, and a snapshot the store cannot produce each map to a
specific :class:`~src.core.contracts.provenance.AbsentEvidenceReason`. Packet
4F reads that directly: a claim whose acquisition failed has a reason to cite
rather than an absence to interpret.

**Redaction happens after location, never before.** Snapshot bytes are held
verbatim and located verbatim. Everything this module composes for persistence
— artifact metadata, and any excerpt read back out through
:meth:`SourceAcquisitionService.read_excerpt` — is redacted on the way out of
the quarantine.
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final, final

from pydantic import JsonValue, ValidationError

from src.core.contracts.capabilities import ApprovalRef, CapabilityGrant
from src.core.contracts.pinning import PinnedVersions
from src.core.contracts.provenance import (
    AbsentEvidenceReason,
    Artifact,
    ArtifactStatus,
    Evidence,
    ProducerKind,
    ToolInvocation,
)
from src.core.contracts.redaction import redact, snapshot_digest
from src.core.contracts.trust import TrustClassification
from src.core.tools.boundary import CancellationToken, ToolBoundary
from src.core.tools.outcome import ToolOutcome, ToolOutcomeStatus
from src.core.tools.prompts import RenderedPrompt
from src.core.tools.secrets import SecretProvider
from src.repositories.artifact_repository import ArtifactRepository
from src.repositories.evidence_repository import EvidenceRepository

from .fetch_tool import SOURCE_FETCH_TOOL, SourceFetchOutput
from .resolution import locate, resolve
from .snapshots import SnapshotIntegrityError, SnapshotNotFoundError, SnapshotStore
from .sources import SourceType, trust_for_source

__all__ = [
    "SNAPSHOT_ARTIFACT_KIND",
    "AcquisitionOutcome",
    "AcquisitionStatus",
    "ExcerptRequest",
    "PromptlessAcquisitionError",
    "SourceAcquisitionService",
]

SNAPSHOT_ARTIFACT_KIND: Final[str] = "source_snapshot"

_FAILURE_REASONS: Final[dict[ToolOutcomeStatus, AbsentEvidenceReason]] = {
    ToolOutcomeStatus.DENIED: AbsentEvidenceReason.CAPABILITY_DENIED,
    ToolOutcomeStatus.TIMED_OUT: AbsentEvidenceReason.RETRIEVAL_FAILED,
    ToolOutcomeStatus.CIRCUIT_OPEN: AbsentEvidenceReason.RETRIEVAL_FAILED,
    ToolOutcomeStatus.FAILED: AbsentEvidenceReason.RETRIEVAL_FAILED,
    ToolOutcomeStatus.CANCELLED: AbsentEvidenceReason.RETRIEVAL_FAILED,
    ToolOutcomeStatus.INVALID_INPUT: AbsentEvidenceReason.RETRIEVAL_FAILED,
    # The tool answered, but with something its own schema rejects. The source
    # was identified and could not be snapshotted, which is what this reason
    # says — distinct from "retrieval failed", where nothing came back at all.
    ToolOutcomeStatus.INVALID_OUTPUT: AbsentEvidenceReason.SOURCE_INACCESSIBLE,
}


class PromptlessAcquisitionError(RuntimeError):
    """Raised when an acquisition produced no verified prompt binding.

    ``Evidence.prompt_binding`` is required by the frozen contract, and the
    only honest source for it is the binding the boundary verified and wrote to
    the durable invocation record. If that is absent, the alternative would be
    to mint one here from the caller's own object — which is precisely the
    unverifiable, caller-supplied binding the renderer seam was built to
    eliminate. Failing is the only answer that does not reintroduce it.
    """


class AcquisitionStatus(StrEnum):
    """Whether a source was acquired, or why it was not."""

    ACQUIRED = "acquired"
    UNAVAILABLE = "unavailable"


@final
@dataclass(frozen=True, slots=True)
class ExcerptRequest:
    """One span of a snapshot to record as evidence.

    A span rather than a locator string, so the locator is always built by
    :func:`~src.core.acquisition.resolution.locate` and is always in the one
    canonical spelling the grammar allows. A hand-written locator is how an
    off-by-one or a padded offset reaches a row that then cannot be
    re-resolved, and the uniqueness constraint on ``(run_id,
    snapshot_artifact_id, locator)`` is only sound while one span has one
    spelling.
    """

    scheme: str
    start: int
    end: int
    annotations: tuple[str, ...] = ()

    def locator(self) -> str:
        """Return the canonical locator for this span."""

        return locate(self.scheme, self.start, self.end, annotations=self.annotations)


@final
@dataclass(frozen=True, slots=True)
class AcquisitionOutcome:
    """What one acquisition produced, or the typed reason it produced nothing.

    Modelled on the boundary's own rule that no failure carries a body: an
    unavailable outcome has no artifact and no evidence, so a caller cannot
    read a partial result as a successful acquisition.
    """

    status: AcquisitionStatus
    invocation: ToolInvocation
    artifact: Artifact | None = None
    evidence: tuple[Evidence, ...] = ()
    absent_evidence_reason: AbsentEvidenceReason | None = None
    detail: str | None = None
    excerpts: tuple[bytes, ...] = field(default=())
    """The verbatim located bytes, in request order. Unredacted, and therefore
    for immediate in-process use only — anything published or persisted goes
    through :meth:`SourceAcquisitionService.read_excerpt`."""

    def __post_init__(self) -> None:
        if self.status is AcquisitionStatus.ACQUIRED:
            if self.artifact is None:
                raise ValueError("an acquired outcome carries its snapshot artifact")
            if self.absent_evidence_reason is not None:
                raise ValueError("an acquired outcome cites no absent-evidence reason")
            return
        if self.absent_evidence_reason is None:
            raise ValueError(
                "an unavailable outcome names why, so a claim resting on it can "
                "cite a typed reason rather than an absence"
            )
        if self.artifact is not None or self.evidence or self.excerpts:
            raise ValueError(
                "an unavailable outcome carries no artifact, evidence, or "
                "excerpt; a failure with a body is how a fabricated source "
                "reaches a caller"
            )


@final
class SourceAcquisitionService:
    """Acquires sources through the tool boundary and records them durably."""

    def __init__(
        self,
        *,
        boundary: ToolBoundary,
        store: SnapshotStore,
        artifacts: ArtifactRepository,
        evidence: EvidenceRepository,
        secret_provider: SecretProvider,
        clock: Callable[[], datetime],
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        """Wire the seam.

        ``secret_provider`` is required rather than defaulted, for the reason
        packet 4A gave about ``redact``: an omitted set of held secrets
        silently disables the exact-value layer, and a security guarantee lost
        by omission is invisible at the call site. A deployment holding no
        credentials passes ``NullSecretProvider`` and says so.

        The repositories are the Wave 3 pattern: they ``flush()``, and the
        caller owns the transaction. Nothing here commits, so an acquisition
        that fails part-way leaves the caller free to roll the whole thing
        back rather than inheriting a half-written snapshot record.
        """

        self._boundary = boundary
        self._store = store
        self._artifacts = artifacts
        self._evidence = evidence
        self._secret_provider = secret_provider
        self._clock = clock
        self._id_factory = id_factory if id_factory is not None else _uuid4_hex

    async def acquire(
        self,
        *,
        run_id: str,
        task_id: str,
        attempt_id: str,
        organization_id: str | None,
        source_type: SourceType,
        source_uri: str,
        capability_scope: str,
        grants: Sequence[CapabilityGrant],
        prompt: RenderedPrompt,
        excerpts: Sequence[ExcerptRequest] = (),
        input_trust: TrustClassification = TrustClassification.DERIVED_UNTRUSTED,
        approvals: Sequence[ApprovalRef] = (),
        pinned_versions: PinnedVersions | None = None,
        tool_name: str = SOURCE_FETCH_TOOL,
        cancellation: CancellationToken | None = None,
    ) -> AcquisitionOutcome:
        """Acquire one source, snapshot it, and record the spans cited from it.

        There is deliberately **no** trust parameter for the acquired content.
        ``input_trust`` describes the request being made — the label on the URI
        and any query that produced it — and is what a capability grant's
        ``max_input_trust`` is compared against. The label written onto the
        artifact and its evidence is derived from ``source_type`` alone.

        Args:
            run_id: The durable run this evidence belongs to.
            task_id: The task within it.
            attempt_id: The attempt making the call.
            organization_id: The authenticated tenant boundary.
            source_type: What kind of source this is. The sole input to the
                trust assignment.
            source_uri: What to acquire.
            capability_scope: The scope whose grant must authorize the call.
            grants: The capability grants in force for this task.
            prompt: The verified prompt of the turn that decided to acquire
                this source. Required: ``Evidence.prompt_binding`` is
                non-optional in the frozen contract, and the only honest source
                for it is the binding the boundary verifies and records.
            excerpts: The spans to record as evidence. Every one is resolved
                against the snapshot before anything is persisted.
            input_trust: The label on the request. Defaults to the least
                trusted, so an unconsidered call is the most constrained one.
            approvals: Approvals available to the capability decision.
            pinned_versions: The run's pinned component versions, against which
                the prompt's identity is checked.
            tool_name: The registered acquisition tool to invoke.
            cancellation: Cooperative cancellation for the call.

        Returns:
            The outcome: either the persisted artifact and evidence, or a typed
            reason no evidence exists.

        Raises:
            TypeError: ``source_type`` is not a
                :class:`~src.core.acquisition.sources.SourceType`.
            PromptlessAcquisitionError: The boundary recorded no prompt binding
                for a call that supplied one.
            UnresolvableLocatorError: A requested span does not resolve against
                the acquired snapshot. Raised before anything is persisted.
        """

        trust = trust_for_source(source_type)

        outcome = await self._boundary.invoke(
            tool_name=tool_name,
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            organization_id=organization_id,
            capability_scope=capability_scope,
            arguments={"source_uri": source_uri, "source_type": source_type.value},
            input_trust=input_trust,
            output_trust=trust,
            grants=grants,
            approvals=approvals,
            prompt=prompt,
            pinned_versions=pinned_versions,
            cancellation=cancellation,
        )

        if not outcome.succeeded:
            return _unavailable(
                outcome,
                reason=_FAILURE_REASONS[outcome.status],
                detail=outcome.detail,
            )

        try:
            handle = SourceFetchOutput.model_validate(
                dict(outcome.invocation.output or {})
            )
        except ValidationError as error:
            return _unavailable(
                outcome,
                reason=AbsentEvidenceReason.SOURCE_INACCESSIBLE,
                detail=f"acquisition handle is unreadable: {error}",
            )

        try:
            data = self._store.get(handle.storage_uri)
        except (SnapshotNotFoundError, SnapshotIntegrityError) as error:
            return _unavailable(
                outcome,
                reason=AbsentEvidenceReason.SOURCE_INACCESSIBLE,
                detail=str(error),
            )

        # The store already checks its own content against its address. This
        # checks the *tool's* claim against the store, which is a different
        # question: a handler that stored one thing and reported another is
        # caught here rather than believed.
        if snapshot_digest(data) != handle.content_sha256:
            return _unavailable(
                outcome,
                reason=AbsentEvidenceReason.SOURCE_INACCESSIBLE,
                detail=(
                    "the acquisition tool reported a digest the snapshot store "
                    "does not hold"
                ),
            )

        # Resolved for every span before anything is written, so an
        # unresolvable locator cannot reach a row and leave the database
        # holding a citation that does not resolve.
        locators = tuple(request.locator() for request in excerpts)
        located = tuple((locator, resolve(locator, data)) for locator in locators)

        binding = outcome.invocation.prompt_binding
        if binding is None:
            raise PromptlessAcquisitionError(
                "the boundary recorded no prompt binding for this acquisition, "
                "so there is no verified identity to attach to its evidence"
            )

        now = self._clock()
        artifact = Artifact(
            artifact_id=self._id_factory(),
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            kind=SNAPSHOT_ARTIFACT_KIND,
            media_type=handle.media_type,
            storage_uri=handle.storage_uri,
            content_sha256=handle.content_sha256,
            status=ArtifactStatus.FINAL,
            trust=trust,
            producer=tool_name,
            # The fetch is a system action; the *evidence* citing it is
            # attributed to the model turn that asked for it. The asymmetry is
            # deliberate: a snapshot is what a URL returned, and it would have
            # returned the same bytes under any prompt.
            producer_kind=ProducerKind.SYSTEM,
            created_at=now,
            metadata=self._metadata(
                source_type=source_type,
                source_uri=source_uri,
                handle=handle,
                invocation=outcome.invocation,
            ),
        )
        await self._artifacts.create_artifact(artifact, organization_id=organization_id)

        recorded: list[Evidence] = []
        for locator, _ in located:
            evidence = Evidence(
                evidence_id=self._id_factory(),
                run_id=run_id,
                task_id=task_id,
                source_type=source_type.value,
                source_uri=source_uri,
                snapshot_artifact_id=artifact.artifact_id,
                content_sha256=artifact.content_sha256,
                locator=locator,
                # An excerpt is a byte range of the snapshot, not a
                # transformation of it, so it carries the snapshot's own label.
                # `propagate_trust` governs derivations — a summary, an
                # embedding — and slicing is not one; running it here would
                # mark verbatim source text as model-shaped content.
                trust=trust,
                prompt_binding=binding,
                producer_tool_invocation_id=outcome.invocation.tool_invocation_id,
                acquired_at=now,
            )
            await self._evidence.record_evidence(
                evidence, organization_id=organization_id
            )
            recorded.append(evidence)

        return AcquisitionOutcome(
            status=AcquisitionStatus.ACQUIRED,
            invocation=outcome.invocation,
            artifact=artifact,
            evidence=tuple(recorded),
            excerpts=tuple(excerpt for _, excerpt in located),
        )

    async def read_excerpt(
        self, evidence: Evidence, *, organization_id: str | None = None
    ) -> str:
        """Return one evidence span as redacted text.

        This is the quarantine's exit. The snapshot is held and located
        verbatim; redaction is applied to the *extracted* excerpt, on its way
        into a prompt, an event, or anything a person reads. Doing it in the
        other order would shift every offset in the artifact this locator was
        taken over.

        Raises:
            SnapshotIntegrityError: The stored bytes do not hash to the digest
                this evidence cites, so the excerpt would not be what the
                evidence says it is.
            LookupError: No artifact row backs this evidence.
        """

        row = await self._artifacts.get_artifact(
            evidence.snapshot_artifact_id, organization_id=organization_id
        )
        if row is None:
            raise LookupError(
                f"evidence {evidence.evidence_id!r} cites snapshot artifact "
                f"{evidence.snapshot_artifact_id!r}, which does not exist"
            )
        data = self._store.get(row.storage_uri)
        if snapshot_digest(data) != evidence.content_sha256:
            raise SnapshotIntegrityError(
                f"evidence {evidence.evidence_id!r} cites digest "
                f"{evidence.content_sha256} but its snapshot holds "
                f"{snapshot_digest(data)}"
            )
        excerpt = resolve(evidence.locator, data)
        # `errors="replace"` rather than a raise: a span of a binary snapshot
        # is legitimately not text, and a reader is better served by a visibly
        # lossy rendering than by an exception that hides the citation.
        text = excerpt.decode("utf-8", errors="replace")
        redacted = redact(
            text, known_secret_values=self._secret_provider.secret_values()
        )
        return redacted if isinstance(redacted, str) else text

    def _metadata(
        self,
        *,
        source_type: SourceType,
        source_uri: str,
        handle: SourceFetchOutput,
        invocation: ToolInvocation,
    ) -> dict[str, JsonValue]:
        """Compose the artifact's metadata, redacted.

        Redacted because this is persisted and read back — a publication
        surface by the redaction contract's own definition. ``source_uri`` in
        particular can carry credentials in its userinfo, and it arrives here
        from a caller rather than through the boundary's input redaction.
        """

        raw: dict[str, JsonValue] = {
            "source_type": source_type.value,
            "source_uri": source_uri,
            "final_uri": handle.final_uri,
            "byte_length": handle.byte_length,
            "license": handle.license().as_metadata(),
            "tool_invocation_id": invocation.tool_invocation_id,
            "tool_name": invocation.tool_name,
            "tool_version": invocation.tool_version,
        }
        redacted = redact(
            raw, known_secret_values=self._secret_provider.secret_values()
        )
        return dict(redacted) if isinstance(redacted, dict) else raw


def _unavailable(
    outcome: ToolOutcome, *, reason: AbsentEvidenceReason, detail: str | None
) -> AcquisitionOutcome:
    return AcquisitionOutcome(
        status=AcquisitionStatus.UNAVAILABLE,
        invocation=outcome.invocation,
        absent_evidence_reason=reason,
        detail=detail,
    )


def _uuid4_hex() -> str:
    return uuid.uuid4().hex
