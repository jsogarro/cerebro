"""Resolution: the step that makes "no claim escaped" a structural fact.

An evaluator answers the claims it was asked about. Nothing in that shape says
what happened to the claims it was *not* asked about, and the gap does not
announce itself — a claim omitted from the output is a claim omitted from the
numerator and the denominator alike, which moves ``n(unsupported) /
n(material claims)`` in the flattering direction with no error anywhere.

So the seam is built the other way round. Resolution starts from the
:class:`~src.core.claims.materiality.ClaimInventory` — the denominator — and
produces exactly one :class:`~src.core.contracts.provenance.ClaimSupport` per
claim in it. A claim no evaluator reached is not missing from the result; it is
present, ``unsupported``, and carries
:attr:`~src.core.contracts.provenance.AbsentEvidenceReason.NOT_ATTEMPTED`, which
is what keeps "we never looked" distinguishable from "we looked and found
nothing" at the point a reviewer reads it.

**The totality check lives in the value, not in the function that builds it.**
:class:`ClaimSupportResolution` validates its own contents against the
inventory in ``__init__``, so a partial set is not something the resolver
declines to produce — it is something that cannot exist. Downstream code takes
``ClaimSupportResolution`` rather than a sequence of rows, and there is no
parameter through which an incomplete set can arrive. This is the same shape as
:class:`~src.core.tools.prompts.RenderedPrompt`, for the same reason: an
invariant a caller has to remember is an invariant.

**Disagreement is an error, not a merge.** A verdict naming a claim the
inventory does not contain, and two verdicts naming the same claim, both raise.
Dropping the first would hide a real divergence between what the evaluator read
and what the denominator counted; picking a winner for the second would apply a
precedence rule nobody chose, over two judgments that may well disagree.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, final

from src.core.claims.materiality import ClaimInventory, MaterialClaim
from src.core.contracts import (
    AbsentEvidenceReason,
    ClaimSupport,
    ClaimSupportStatus,
    PromptBinding,
)

__all__ = [
    "RESOLVER_EVALUATOR_ID",
    "RESOLVER_EVALUATOR_VERSION",
    "ClaimResolutionError",
    "ClaimSupportResolution",
    "ClaimSupportResolver",
    "ClaimVerdict",
    "DuplicateVerdictError",
    "IncompleteResolutionError",
    "UnknownClaimError",
]

RESOLVER_EVALUATOR_ID: Final[str] = "claim-support-resolver"
"""Names the resolver itself as the author of every fill-in verdict.

A fill-in row must not borrow an evaluator's identity. "No evaluator reached
this claim" is a statement by the resolver, and attributing it to an entailment
checker would make the audit trail claim a judgment that was never made.
"""

RESOLVER_EVALUATOR_VERSION: Final[str] = "1.0"

_UNEVALUATED_RULE: Final[str] = (
    "A material claim that no evaluator returned a verdict for is recorded as "
    "$status with absent_evidence_reason $reason, so that a claim nobody "
    "examined stays distinguishable from one examined and found unsupported. "
    "Claim: $claim_id"
)
"""The deterministic decision text a fill-in verdict is derived from.

This stands where an evaluator's prompt stands. It is not sent to a model —
nothing about this decision involves one — and the digests on the resulting
:class:`~src.core.contracts.provenance.PromptBinding` hash this rule and the
text it produced, so the identity is honest about what actually decided the
row. See the handoff note on ``ClaimSupport.prompt_binding`` being required
unconditionally: a deterministic evaluator has no prompt, and this is the
narrowest way to satisfy the contract without inventing one.
"""


class ClaimResolutionError(ValueError):
    """Base for every way a resolution can fail to account for its claims."""


class UnknownClaimError(ClaimResolutionError):
    """A verdict names a claim the inventory does not contain."""


class DuplicateVerdictError(ClaimResolutionError):
    """Two verdicts name the same claim."""


class IncompleteResolutionError(ClaimResolutionError):
    """The recorded supports are not exactly the inventory's material claims."""


@dataclass(frozen=True, slots=True)
class ClaimVerdict:
    """One evaluator's judgment about one claim.

    Deliberately smaller than :class:`ClaimSupport`: an evaluator knows what it
    decided and why, and should not be in the business of allocating record ids
    or restating which run and artifact it was called for. Those are the
    resolver's, which is also what stops an evaluator writing a row for a claim
    the inventory never listed.
    """

    claim_id: str
    status: ClaimSupportStatus
    explanation: str
    evaluator_id: str
    evaluator_version: str
    prompt_binding: PromptBinding
    evaluated_at: datetime
    evidence_ids: tuple[str, ...] = ()
    absent_evidence_reason: AbsentEvidenceReason | None = None


@final
@dataclass(frozen=True, slots=True)
class ClaimSupportResolution:
    """Exactly one verdict per material claim, checked on construction.

    The check is here rather than in :meth:`ClaimSupportResolver.resolve`
    because a value that has been validated once is worth more than a function
    that validates: every later reader — the recorder, the gate — receives a
    type whose existence already means "complete", and no future second
    construction site can skip the check.
    """

    inventory: ClaimInventory
    run_id: str
    supports: tuple[ClaimSupport, ...]
    _by_claim_id: Mapping[str, ClaimSupport] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        expected = {claim.claim_id for claim in self.inventory.claims}
        recorded = [support.claim_id for support in self.supports]

        duplicates = sorted({cid for cid in recorded if recorded.count(cid) > 1})
        if duplicates:
            raise IncompleteResolutionError(
                f"claims recorded more than once: {duplicates}"
            )

        missing = sorted(expected - set(recorded))
        if missing:
            raise IncompleteResolutionError(
                f"material claims left without a verdict: {missing}"
            )
        unlisted = sorted(set(recorded) - expected)
        if unlisted:
            raise IncompleteResolutionError(
                f"verdicts recorded for claims the inventory does not contain: "
                f"{unlisted}"
            )

        for support in self.supports:
            if support.run_id != self.run_id:
                raise IncompleteResolutionError(
                    f"claim support {support.claim_support_id!r} names run "
                    f"{support.run_id!r}, not {self.run_id!r}"
                )
            if support.artifact_id != self.inventory.artifact_id:
                raise IncompleteResolutionError(
                    f"claim support {support.claim_support_id!r} names artifact "
                    f"{support.artifact_id!r}, not "
                    f"{self.inventory.artifact_id!r}"
                )

        object.__setattr__(
            self,
            "_by_claim_id",
            {support.claim_id: support for support in self.supports},
        )

    def support_for(self, claim_id: str) -> ClaimSupport:
        """Return the one verdict recorded for ``claim_id``."""

        return self._by_claim_id[claim_id]

    @property
    def material_claim_count(self) -> int:
        """The gate's denominator, and the number of rows this will write."""

        return len(self.supports)

    @property
    def unsupported_count(self) -> int:
        """The gate's numerator.

        Only ``unsupported`` counts. ``partially_supported`` and ``disputed``
        are findings a reviewer reads, not failures a gate tallies — that
        separation is the reason the status model has four states rather than
        three.
        """

        return sum(
            1
            for support in self.supports
            if support.status is ClaimSupportStatus.UNSUPPORTED
        )

    @property
    def unsupported_ratio(self) -> float | None:
        """``unsupported_count / material_claim_count``, or ``None`` if empty.

        ``None`` rather than ``0.0`` because an empty denominator is an absence
        of information about the artifact, not a good result for it. Returning
        a number would let an artifact whose claims all fell out of the
        inventory pass a "ratio below threshold" gate on the strength of having
        said nothing measurable.
        """

        if not self.supports:
            return None
        return self.unsupported_count / self.material_claim_count


@final
class ClaimSupportResolver:
    """Turns an inventory plus whatever verdicts exist into a complete record."""

    __slots__ = ("_id_factory",)

    def __init__(self, *, id_factory: Callable[[], str]) -> None:
        self._id_factory = id_factory

    def resolve(
        self,
        *,
        inventory: ClaimInventory,
        run_id: str,
        verdicts: Iterable[ClaimVerdict],
        resolved_at: datetime,
    ) -> ClaimSupportResolution:
        """Produce one claim-support record for every claim in ``inventory``.

        Args:
            inventory: The denominator. Every claim in it gets a record.
            run_id: The run these judgments belong to.
            verdicts: Whatever evaluators returned, in any order, possibly
                empty. Every claim they do not cover is filled in.
            resolved_at: The timestamp for fill-in records — supplied rather
                than read from a clock so a resolution replays identically.

        Returns:
            A resolution whose existence means every material claim is covered.

        Raises:
            UnknownClaimError: A verdict names a claim not in ``inventory``.
            DuplicateVerdictError: Two verdicts name the same claim.
        """

        listed = {claim.claim_id: claim for claim in inventory.claims}
        by_claim: dict[str, ClaimVerdict] = {}
        for verdict in verdicts:
            if verdict.claim_id not in listed:
                raise UnknownClaimError(
                    f"verdict names claim {verdict.claim_id!r}, which is not a "
                    f"material claim of artifact {inventory.artifact_id!r}; the "
                    f"evaluator and the denominator disagree about what this "
                    f"artifact says"
                )
            if verdict.claim_id in by_claim:
                raise DuplicateVerdictError(
                    f"two verdicts were returned for claim "
                    f"{verdict.claim_id!r}; resolving that by precedence would "
                    f"discard a judgment nobody chose to discard"
                )
            by_claim[verdict.claim_id] = verdict

        supports = tuple(
            self._to_support(
                claim=claim,
                verdict=by_claim.get(claim.claim_id),
                inventory=inventory,
                run_id=run_id,
                resolved_at=resolved_at,
            )
            for claim in inventory.claims
        )
        return ClaimSupportResolution(
            inventory=inventory, run_id=run_id, supports=supports
        )

    def _to_support(
        self,
        *,
        claim: MaterialClaim,
        verdict: ClaimVerdict | None,
        inventory: ClaimInventory,
        run_id: str,
        resolved_at: datetime,
    ) -> ClaimSupport:
        if verdict is None:
            return self._unevaluated(
                claim=claim,
                inventory=inventory,
                run_id=run_id,
                resolved_at=resolved_at,
            )
        return ClaimSupport(
            claim_support_id=self._id_factory(),
            run_id=run_id,
            artifact_id=inventory.artifact_id,
            claim_id=claim.claim_id,
            claim_text=claim.text,
            status=verdict.status,
            evidence_ids=verdict.evidence_ids,
            absent_evidence_reason=verdict.absent_evidence_reason,
            evaluator_id=verdict.evaluator_id,
            evaluator_version=verdict.evaluator_version,
            prompt_binding=verdict.prompt_binding,
            explanation=verdict.explanation,
            evaluated_at=verdict.evaluated_at,
        )

    def _unevaluated(
        self,
        *,
        claim: MaterialClaim,
        inventory: ClaimInventory,
        run_id: str,
        resolved_at: datetime,
    ) -> ClaimSupport:
        """Record the claim nobody reached, as itself rather than as a gap."""

        explanation = _render_unevaluated_rule(claim.claim_id)
        return ClaimSupport(
            claim_support_id=self._id_factory(),
            run_id=run_id,
            artifact_id=inventory.artifact_id,
            claim_id=claim.claim_id,
            claim_text=claim.text,
            status=ClaimSupportStatus.UNSUPPORTED,
            evidence_ids=(),
            absent_evidence_reason=AbsentEvidenceReason.NOT_ATTEMPTED,
            evaluator_id=RESOLVER_EVALUATOR_ID,
            evaluator_version=RESOLVER_EVALUATOR_VERSION,
            prompt_binding=PromptBinding.for_rendered(
                prompt_id=RESOLVER_EVALUATOR_ID,
                prompt_version=RESOLVER_EVALUATOR_VERSION,
                template_source=_UNEVALUATED_RULE,
                rendered_text=explanation,
            ),
            explanation=explanation,
            evaluated_at=resolved_at,
        )


def _render_unevaluated_rule(claim_id: str) -> str:
    return (
        _UNEVALUATED_RULE.replace("$status", ClaimSupportStatus.UNSUPPORTED.value)
        .replace("$reason", AbsentEvidenceReason.NOT_ATTEMPTED.value)
        .replace("$claim_id", claim_id)
    )
