"""What counts as a material claim — the denominator Wave 7 divides by.

A release gate reads ``n(unsupported) / n(material claims)``. Both halves are
decisions, and only one of them is obviously so. This module is the other one,
written down rather than left implicit in whatever code happened to build the
list.

**Materiality is a deny-list, and that direction is the point.** A claim that
never enters the inventory is not evaluated, not counted, and not missed:
the gate reads a smaller denominator and a better ratio, and nothing anywhere
fails. An allow-list policy — *material iff it looks like a claim* — makes that
the normal outcome for every statement whose shape the policy did not
anticipate. So every segment of a deliverable artifact is material **unless**
it matches an enumerated exclusion, each exclusion is named, and the excluded
segments are kept in the inventory rather than dropped, so the denominator's
derivation can be audited instead of trusted.

**Two totality properties make "no statement was skipped" checkable:**

1. :func:`segment_source` covers the source exactly — the segments are
   contiguous, non-overlapping, and reassemble to the original string
   character for character. There is no gap for a statement to fall into.
2. :func:`build_inventory` gives every segment exactly one disposition:
   material, or excluded with a reason. There is no third outcome and no
   silent drop.

**An unclassified artifact kind raises.** Scoping materiality by artifact kind
is necessary — a source snapshot's prose is the *source's* claims, not ours —
but a kind nobody has classified must not default to "not deliverable". That
default yields an empty inventory, and an empty denominator is indistinguishable
downstream from a clean sheet: ``0/0`` fails no gate. A new artifact kind
therefore stops the pipeline until someone classifies it.

**What this module does not establish.** Whether a given exclusion is *correct*
is a policy-quality question, not a structural one. Excluding a real assertion
as a heading is still possible; what is not possible is excluding it invisibly.
"""

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from src.core.contracts.locators import canonical_span

__all__ = [
    "DELIVERABLE_ARTIFACT_KINDS",
    "MATERIALITY_POLICY_ID",
    "MATERIALITY_POLICY_VERSION",
    "MINIMUM_ASSERTION_WORDS",
    "NON_DELIVERABLE_ARTIFACT_KINDS",
    "ClaimExclusionReason",
    "ClaimInventory",
    "ExcludedSegment",
    "MaterialClaim",
    "SourceSegment",
    "UnclassifiedArtifactKindError",
    "build_inventory",
    "segment_source",
]

MATERIALITY_POLICY_ID: Final[str] = "material-claim"
"""Stable name for this policy, recorded wherever a denominator is reported."""

MATERIALITY_POLICY_VERSION: Final[str] = "1.0"
"""Bump on any change that can move a segment between material and excluded.

A gate number is only comparable across runs that used the same policy.
"""

MINIMUM_ASSERTION_WORDS: Final[int] = 4
"""Below this, a segment is a heading or a label rather than an assertion.

The floor is a real risk to the denominator — a terse assertion ("Latency
halved.") is excluded — and it is the one exclusion here that trades recall for
noise. It is kept because section headings otherwise dominate the denominator
of every report, and it is recorded as an exclusion rather than a drop so the
trade is visible in the inventory.
"""

DELIVERABLE_ARTIFACT_KINDS: Final[frozenset[str]] = frozenset(
    {
        "analysis",
        "answer",
        "comparative_analysis",
        "literature_review",
        "report",
        "research_report",
        "summary",
    }
)
"""Artifact kinds whose prose asserts something *this system* is claiming."""

NON_DELIVERABLE_ARTIFACT_KINDS: Final[frozenset[str]] = frozenset(
    {
        "plan",
        "prompt_log",
        "source_snapshot",
        "tool_output",
        "trace",
    }
)
"""Artifact kinds whose prose is input, intermediate state, or someone else's.

A source snapshot is the clearest case: its sentences are assertions by the
source, and holding this system to them would count every retrieved page's
claims against its own gate.
"""


class ClaimExclusionReason(StrEnum):
    """Why a segment of a deliverable artifact is not a material claim.

    Typed for the same reason :class:`AbsentEvidenceReason` is: the set of
    exclusions applied to a corpus is a number someone has to review, and prose
    reasons cannot be aggregated.
    """

    NOT_A_DELIVERABLE_ARTIFACT = "not_a_deliverable_artifact"
    """The artifact's kind is registered as not carrying our own claims."""

    NO_ALPHABETIC_CONTENT = "no_alphabetic_content"
    """Whitespace, punctuation, or rules — the separators segmentation keeps."""

    INTERROGATIVE = "interrogative"
    """A question asserts nothing, so there is nothing to support."""

    BELOW_MINIMUM_ASSERTION_LENGTH = "below_minimum_assertion_length"
    """Shorter than :data:`MINIMUM_ASSERTION_WORDS`; a heading or a label."""


class UnclassifiedArtifactKindError(ValueError):
    """An artifact kind belongs to neither registered set.

    Deliberately not a silent "not deliverable": that produces an empty
    denominator, and an empty denominator passes every gate.
    """


@dataclass(frozen=True, slots=True)
class SourceSegment:
    """One contiguous piece of an artifact's text, with its char offsets.

    ``text`` is the verbatim slice including the whitespace that follows it, so
    the segments of a source concatenate back to that source exactly.
    """

    text: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class MaterialClaim:
    """One claim that counts toward the denominator.

    ``claim_id`` is the claim's canonical span within the artifact, spelled in
    the frozen locator grammar. That makes the id deterministic and stable
    across re-evaluations of the same artifact without a registry to consult —
    re-running the policy over the same bytes yields the same ids, which is what
    lets an append-only claim-support history line up.
    """

    claim_id: str
    text: str
    span: tuple[int, int]


@dataclass(frozen=True, slots=True)
class ExcludedSegment:
    """One segment the policy kept out of the denominator, and why."""

    span: tuple[int, int]
    text: str
    reason: ClaimExclusionReason


@dataclass(frozen=True, slots=True)
class ClaimInventory:
    """The frozen denominator for one artifact.

    ``source_sha256`` binds the inventory to the exact text it segmented, so a
    denominator can be checked against the artifact it claims to describe
    rather than assumed to match.
    """

    artifact_id: str
    artifact_kind: str
    source_sha256: str
    policy_id: str
    policy_version: str
    claims: tuple[MaterialClaim, ...]
    exclusions: tuple[ExcludedSegment, ...]

    def __post_init__(self) -> None:
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError(
                f"claim ids must be unique within artifact {self.artifact_id!r}"
            )

    @property
    def material_claim_count(self) -> int:
        """The gate's denominator."""

        return len(self.claims)


_BOUNDARY = re.compile(r"(?<=[.!?])[ \t]+|\n+")


def segment_source(source: str) -> tuple[SourceSegment, ...]:
    """Split ``source`` into contiguous, non-overlapping, total segments.

    The separator that ends a segment is kept *on* that segment rather than
    discarded, which is what makes reassembly exact. A splitter that dropped
    whitespace would still look correct on every readable sample while leaving
    no way to prove nothing fell between two segments.
    """

    segments: list[SourceSegment] = []
    cursor = 0
    for match in _BOUNDARY.finditer(source):
        end = match.end()
        if end <= cursor:
            continue
        segments.append(SourceSegment(text=source[cursor:end], start=cursor, end=end))
        cursor = end
    if cursor < len(source):
        segments.append(
            SourceSegment(text=source[cursor:], start=cursor, end=len(source))
        )
    return tuple(segments)


def build_inventory(
    *, artifact_id: str, artifact_kind: str, source: str
) -> ClaimInventory:
    """Derive the material-claim denominator for one artifact.

    Args:
        artifact_id: The artifact whose text this is.
        artifact_kind: Must be registered in exactly one of
            :data:`DELIVERABLE_ARTIFACT_KINDS` or
            :data:`NON_DELIVERABLE_ARTIFACT_KINDS`.
        source: The artifact's text, verbatim.

    Returns:
        Every segment of ``source``, partitioned into material claims and
        named exclusions. The two together account for the whole source.

    Raises:
        UnclassifiedArtifactKindError: ``artifact_kind`` is registered in
            neither set, so whether its statements are material is unknown.
    """

    deliverable = _is_deliverable(artifact_kind)

    claims: list[MaterialClaim] = []
    exclusions: list[ExcludedSegment] = []
    for segment in segment_source(source):
        text, span = _tighten(segment)
        reason = _exclusion_for(text, deliverable=deliverable)
        if reason is not None:
            exclusions.append(ExcludedSegment(span=span, text=text, reason=reason))
            continue
        claims.append(
            MaterialClaim(
                claim_id=canonical_span("char", span[0], span[1]),
                text=text,
                span=span,
            )
        )

    return ClaimInventory(
        artifact_id=artifact_id,
        artifact_kind=artifact_kind,
        source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        policy_id=MATERIALITY_POLICY_ID,
        policy_version=MATERIALITY_POLICY_VERSION,
        claims=tuple(claims),
        exclusions=tuple(exclusions),
    )


def _is_deliverable(artifact_kind: str) -> bool:
    if artifact_kind in DELIVERABLE_ARTIFACT_KINDS:
        return True
    if artifact_kind in NON_DELIVERABLE_ARTIFACT_KINDS:
        return False
    raise UnclassifiedArtifactKindError(
        f"artifact kind {artifact_kind!r} is registered as neither deliverable "
        f"nor non-deliverable; classify it rather than letting it contribute "
        f"an empty claim denominator"
    )


def _tighten(segment: SourceSegment) -> tuple[str, tuple[int, int]]:
    """Return the segment's content without its surrounding whitespace.

    The offsets move with the text so a claim id spans what the claim says, not
    the blank line after it.
    """

    lead = len(segment.text) - len(segment.text.lstrip())
    trail = len(segment.text) - len(segment.text.rstrip())
    return (
        segment.text.strip(),
        (segment.start + lead, segment.end - trail),
    )


def _exclusion_for(text: str, *, deliverable: bool) -> ClaimExclusionReason | None:
    if not deliverable:
        return ClaimExclusionReason.NOT_A_DELIVERABLE_ARTIFACT
    if not any(character.isalpha() for character in text):
        return ClaimExclusionReason.NO_ALPHABETIC_CONTENT
    if text.endswith("?"):
        return ClaimExclusionReason.INTERROGATIVE
    if len(text.split()) < MINIMUM_ASSERTION_WORDS:
        return ClaimExclusionReason.BELOW_MINIMUM_ASSERTION_LENGTH
    return None
