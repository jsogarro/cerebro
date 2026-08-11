"""Deterministic trust-label ordering and propagation.

Labelling content at ingestion is worthless if the label a transformation
produces is a matter of convention. This module makes it a function.

The five ``TrustClassification`` labels form a **total order** from most to
least trusted. A total order — rather than a partial one — is what lets the
combination rule for a multi-input transformation be a plain maximum, so two
callers can never disagree about the label of the same transformation.

``DERIVED_UNTRUSTED`` sits *below* ``EXTERNAL_UNTRUSTED`` deliberately. Content
that has passed through a model can carry an injected instruction reformatted
to look like control text, which is strictly harder to defend against than the
raw external content it came from. Ranking derived content lowest means the
propagation rule can never launder taint by round-tripping content through a
summarizer.

Two laws hold for :func:`propagate_trust`, and both are tested:

*No laundering*
    ``trust_rank(propagate_trust(inputs)) >= max(trust_rank(i) for i in inputs)``.
    A transformation never increases trust.

*Idempotence*
    ``propagate_trust((propagate_trust(inputs),)) == propagate_trust(inputs)``.
    Re-labelling an already-labelled output is a no-op, so a pipeline stage
    that re-runs cannot ratchet a label downward on every pass.

Note the division of labour between the two rules exported here.
:func:`propagate_trust` computes the *default* label for a derivation — parse,
summarize, embed — and its rank-based no-laundering law governs that default.
:func:`is_trusted_tier` expresses the weaker, security-relevant constraint that
``ToolInvocation`` actually enforces on a declared output label, because a tool
may legitimately declare a different untrusted label than a pure derivation
would produce. Enforcing the rank law on every declared output would reject
correct acquisition; enforcing the tier boundary rejects only laundering.
"""

from collections.abc import Iterable
from enum import StrEnum


class TrustClassification(StrEnum):
    """How far a piece of content is from trusted control.

    This lives here rather than in :mod:`src.core.contracts.provenance` so the
    label and the rules governing it are one unit, and so the provenance
    contracts can enforce those rules without a circular import.
    """

    TRUSTED_CONTROL = "trusted_control"
    APPLICATION = "application"
    USER_SUPPLIED = "user_supplied"
    EXTERNAL_UNTRUSTED = "external_untrusted"
    DERIVED_UNTRUSTED = "derived_untrusted"


_TRUST_RANK: dict[TrustClassification, int] = {
    TrustClassification.TRUSTED_CONTROL: 0,
    TrustClassification.APPLICATION: 1,
    TrustClassification.USER_SUPPLIED: 2,
    TrustClassification.EXTERNAL_UNTRUSTED: 3,
    TrustClassification.DERIVED_UNTRUSTED: 4,
}

_TRUST_PRESERVING: frozenset[TrustClassification] = frozenset(
    {TrustClassification.TRUSTED_CONTROL, TrustClassification.APPLICATION}
)


TRUSTED_TIER: frozenset[TrustClassification] = _TRUST_PRESERVING
"""Labels that may be read as control. Everything else is data."""


def trust_rank(label: TrustClassification) -> int:
    """Return the total-order rank of ``label``; 0 is the most trusted."""

    return _TRUST_RANK[label]


def is_trusted_tier(label: TrustClassification) -> bool:
    """Return whether ``label`` may be read as control rather than as data.

    The tier boundary — not the finer rank order — is the security-relevant
    line. ``USER_SUPPLIED``, ``EXTERNAL_UNTRUSTED``, and ``DERIVED_UNTRUSTED``
    differ in provenance, but all three are data that must never be executed as
    instructions. Enforcing a *rank* floor on a transformation's output would
    misclassify legitimate acquisition: a search tool takes a user's query
    (``USER_SUPPLIED``) and returns a fetched page (``EXTERNAL_UNTRUSTED``),
    which is a correct labelling and not laundering, because it never crosses
    into the trusted tier.
    """

    return label in TRUSTED_TIER


def at_least_as_trusted(label: TrustClassification, floor: TrustClassification) -> bool:
    """Return whether ``label`` is at least as trusted as ``floor``."""

    return _TRUST_RANK[label] <= _TRUST_RANK[floor]


def propagate_trust(
    inputs: Iterable[TrustClassification],
) -> TrustClassification:
    """Return the label of a transformation's output from its inputs' labels.

    The rule is: take the least-trusted input. If it is control or application
    content, the output keeps that label — transforming trusted material does
    not manufacture untrusted material. Otherwise the output is
    ``DERIVED_UNTRUSTED``, because anything downstream of user-supplied or
    external content is model-shaped content that must never be read as
    control.

    Args:
        inputs: Every input label consumed by one transformation. Order is
            irrelevant; the result depends only on the multiset.

    Returns:
        The output's trust label.

    Raises:
        ValueError: ``inputs`` is empty. A transformation with no inputs is not
            a transformation, and defaulting it would silently mint a label.
    """

    labels = tuple(inputs)
    if not labels:
        raise ValueError("propagate_trust requires at least one input label")

    worst = max(labels, key=lambda label: _TRUST_RANK[label])
    if worst in _TRUST_PRESERVING:
        return worst
    return TrustClassification.DERIVED_UNTRUSTED


__all__ = [
    "TRUSTED_TIER",
    "TrustClassification",
    "at_least_as_trusted",
    "is_trusted_tier",
    "propagate_trust",
    "trust_rank",
]
