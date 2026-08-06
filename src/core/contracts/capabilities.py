"""Task-scoped, deny-by-default capability grants.

``ToolInvocation.capability_scope`` names a grant. This module gives that name
semantics.

**Deny by default, structurally.** There is no deny rule and no rule ordering.
A request is authorized if and only if some grant matches it and every
constraint on that grant is satisfied; :func:`decide_capability` returns a
denial on every other path, including the ones a caller never considered. Two
consequences follow. First, the decision is *order-independent*: because there
is no "first match wins" precedence between an allow rule and a deny rule,
shuffling the grant list cannot change the outcome. Second, a denial can never
be produced by an exception falling through to an allow, because the allow is
only reachable from inside the loop that proved a grant satisfied.

**Task-scoped, not a catalog.** A grant names ``run_id`` *and* ``task_id``. A
capability issued for one task authorizes nothing in its sibling, so a
compromised task cannot reach a tool its neighbour was allowed to call.

**Source-to-sink.** ``max_input_trust`` is the least-trusted input a grant
tolerates, compared against the request's ``input_trust`` on the total order in
:mod:`src.core.contracts.trust`. That single comparison is the flow rule: a
sink granted ``max_input_trust=APPLICATION`` can never be reached by
derived-untrusted content, whatever tool is asking.

**Approval for sensitive sinks.** ``EXTERNAL_WRITE`` and ``EXFILTRATION``
require approval unconditionally. A grant that tries to waive it is rejected at
construction, so the guarantee holds even for a caller that never consults
:func:`decide_capability`. Approvals bind to a request *fingerprint*, so an
approval cannot be replayed against a different request.

Per the Wave 4 non-goal: this is an authorization decision over typed grants.
It is not an allowlist of tool names, and nothing here treats prompt wording or
a classifier as a boundary.
"""

import hashlib
from collections.abc import Sequence
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from .base import ContentSha256, ContractId, ContractModel, Version, require_unique
from .provenance import TrustClassification
from .trust import at_least_as_trusted


class SensitivityClass(StrEnum):
    """What an invocation can do to the world outside its own run."""

    READ_ONLY = "read_only"
    """No effect outside the run."""

    INTERNAL_WRITE = "internal_write"
    """Writes inside the requesting tenant's own store."""

    EXTERNAL_WRITE = "external_write"
    """Mutates a third-party system."""

    EXFILTRATION = "exfiltration"
    """Sends run content to a destination outside the tenant boundary."""


APPROVAL_REQUIRED_SENSITIVITIES: frozenset[SensitivityClass] = frozenset(
    {SensitivityClass.EXTERNAL_WRITE, SensitivityClass.EXFILTRATION}
)
"""Sinks whose approval requirement no grant may waive."""


class CapabilityDecisionEffect(StrEnum):
    """The outcome of one capability check. There is no third state."""

    ALLOW = "allow"
    DENY = "deny"


class CapabilityDenialReason(StrEnum):
    """Why a request was denied, precisely enough to act on."""

    NO_MATCHING_GRANT = "no_matching_grant"
    TOOL_VERSION_NOT_GRANTED = "tool_version_not_granted"
    GRANT_NOT_YET_VALID = "grant_not_yet_valid"
    GRANT_EXPIRED = "grant_expired"
    SENSITIVITY_MISMATCH = "sensitivity_mismatch"
    INPUT_TRUST_EXCEEDS_GRANT = "input_trust_exceeds_grant"
    APPROVAL_MISSING = "approval_missing"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_FINGERPRINT_MISMATCH = "approval_fingerprint_mismatch"


class CapabilityRequest(ContractModel):
    """One tool invocation presented for authorization.

    ``input_sha256`` is the digest of the **redacted** canonical input, per the
    redaction contract. Including it in the fingerprint is what binds an
    approval to the exact payload a human approved.
    """

    run_id: ContractId
    task_id: ContractId
    attempt_id: ContractId
    tool_name: ContractId
    tool_version: Version
    capability_scope: ContractId
    sensitivity: SensitivityClass
    input_trust: TrustClassification
    input_sha256: ContentSha256
    requested_at: AwareDatetime

    def fingerprint(self) -> ContentSha256:
        """Return a stable digest of everything that determines authorization.

        ``requested_at`` is excluded so that a retry of the same request keeps
        its approval; every field that could change the decision is included,
        so a *different* request never inherits one.
        """

        material = "\x1f".join(
            (
                self.run_id,
                self.task_id,
                self.attempt_id,
                self.tool_name,
                self.tool_version,
                self.capability_scope,
                self.sensitivity.value,
                self.input_trust.value,
                self.input_sha256,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


class CapabilityGrant(ContractModel):
    """One task-scoped permission to invoke one tool under stated constraints."""

    grant_id: ContractId
    run_id: ContractId
    task_id: ContractId
    capability_scope: ContractId
    tool_name: ContractId
    tool_versions: tuple[Version, ...] = Field(min_length=1)
    sensitivity: SensitivityClass
    max_input_trust: TrustClassification
    requires_approval: bool
    issued_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def validate_grant(self) -> Self:
        require_unique(self.tool_versions, field_name="tool_versions")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        if self.sensitivity in APPROVAL_REQUIRED_SENSITIVITIES and (
            not self.requires_approval
        ):
            raise ValueError(
                f"a {self.sensitivity.value} sink always requires approval; "
                "a grant cannot waive it"
            )
        return self


class ApprovalRef(ContractModel):
    """A human approval bound to one grant and one exact request."""

    approval_id: ContractId
    grant_id: ContractId
    request_fingerprint: ContentSha256
    approved_by: ContractId
    approved_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.expires_at <= self.approved_at:
            raise ValueError("expires_at must be after approved_at")
        return self


class CapabilityDecision(ContractModel):
    """The auditable result of one capability check."""

    effect: CapabilityDecisionEffect
    request_fingerprint: ContentSha256
    grant_id: ContractId | None = None
    approval_id: ContractId | None = None
    denial_reason: CapabilityDenialReason | None = None
    decided_at: AwareDatetime

    @model_validator(mode="after")
    def validate_effect_fields(self) -> Self:
        if self.effect is CapabilityDecisionEffect.ALLOW:
            if self.grant_id is None:
                raise ValueError("an allow decision names the grant that permitted it")
            if self.denial_reason is not None:
                raise ValueError("an allow decision cannot carry a denial reason")
        elif self.denial_reason is None:
            raise ValueError("a deny decision must carry a denial reason")
        elif self.approval_id is not None:
            raise ValueError("a deny decision cannot cite an approval")
        return self


# Constraint checks run in this order. When no grant is satisfied, the reason
# reported is the one from the grant that got furthest, which is the most
# actionable: "your approval expired" beats "no matching grant" when both are
# true of different grants.
_DENIAL_STAGES: tuple[CapabilityDenialReason, ...] = (
    CapabilityDenialReason.TOOL_VERSION_NOT_GRANTED,
    CapabilityDenialReason.SENSITIVITY_MISMATCH,
    CapabilityDenialReason.GRANT_NOT_YET_VALID,
    CapabilityDenialReason.GRANT_EXPIRED,
    CapabilityDenialReason.INPUT_TRUST_EXCEEDS_GRANT,
    CapabilityDenialReason.APPROVAL_MISSING,
    CapabilityDenialReason.APPROVAL_EXPIRED,
    CapabilityDenialReason.APPROVAL_FINGERPRINT_MISMATCH,
)


def _identity_matches(grant: CapabilityGrant, request: CapabilityRequest) -> bool:
    return (
        grant.run_id == request.run_id
        and grant.task_id == request.task_id
        and grant.capability_scope == request.capability_scope
        and grant.tool_name == request.tool_name
    )


def _evaluate(
    grant: CapabilityGrant,
    request: CapabilityRequest,
    approvals: Sequence[ApprovalRef],
    now: AwareDatetime,
    fingerprint: str,
) -> tuple[CapabilityDenialReason, None] | tuple[None, ApprovalRef | None]:
    """Return either the denial reason for ``grant``, or the approval it used."""

    if request.tool_version not in grant.tool_versions:
        return CapabilityDenialReason.TOOL_VERSION_NOT_GRANTED, None
    if grant.sensitivity is not request.sensitivity:
        return CapabilityDenialReason.SENSITIVITY_MISMATCH, None
    if now < grant.issued_at:
        return CapabilityDenialReason.GRANT_NOT_YET_VALID, None
    if now >= grant.expires_at:
        return CapabilityDenialReason.GRANT_EXPIRED, None
    if not at_least_as_trusted(request.input_trust, grant.max_input_trust):
        return CapabilityDenialReason.INPUT_TRUST_EXCEEDS_GRANT, None

    # The second disjunct is defence in depth, and is unreachable through any
    # validated grant: `CapabilityGrant.validate_grant` already rejects a
    # sensitive sink with `requires_approval=False` at construction, so that is
    # the load-bearing enforcement. Deleting this clause therefore breaks no
    # test that builds grants normally.
    #
    # Keep it anyway. If the constructor validator is ever relaxed, this
    # becomes the sole thing standing between an unapproved grant and an
    # external write, and a reviewer reading only this function would otherwise
    # have to infer the guarantee from a different class.
    # `test_the_decision_time_approval_backstop_holds_without_the_constructor`
    # reaches it via `model_construct`, which is the only way to obtain the
    # invalid grant that the constructor forbids.
    requires_approval = (
        grant.requires_approval or grant.sensitivity in APPROVAL_REQUIRED_SENSITIVITIES
    )
    if not requires_approval:
        return None, None

    candidates = [
        approval for approval in approvals if approval.grant_id == grant.grant_id
    ]
    if not candidates:
        return CapabilityDenialReason.APPROVAL_MISSING, None

    matched = [
        approval
        for approval in candidates
        if approval.request_fingerprint == fingerprint
    ]
    if not matched:
        return CapabilityDenialReason.APPROVAL_FINGERPRINT_MISMATCH, None

    live = [
        approval
        for approval in matched
        if approval.approved_at <= now < approval.expires_at
    ]
    if not live:
        return CapabilityDenialReason.APPROVAL_EXPIRED, None

    return None, min(live, key=lambda approval: approval.approval_id)


def decide_capability(
    *,
    request: CapabilityRequest,
    grants: Sequence[CapabilityGrant],
    now: AwareDatetime,
    approvals: Sequence[ApprovalRef] = (),
) -> CapabilityDecision:
    """Authorize ``request`` against ``grants``, denying by default.

    Args:
        request: The invocation presented for authorization.
        grants: Every grant issued to the requesting task. An empty sequence
            is a valid input and denies.
        now: The decision instant, supplied rather than read from the clock so
            the decision is reproducible in a replay and in a test.
        approvals: Approvals available to satisfy a sensitive-sink grant.

    Returns:
        An ``ALLOW`` decision naming the grant that permitted the request, or a
        ``DENY`` decision naming why. There is no third outcome and no path
        that returns ``ALLOW`` without a satisfied grant.
    """

    fingerprint = request.fingerprint()
    candidates = sorted(
        (grant for grant in grants if _identity_matches(grant, request)),
        key=lambda grant: grant.grant_id,
    )
    if not candidates:
        return CapabilityDecision(
            effect=CapabilityDecisionEffect.DENY,
            request_fingerprint=fingerprint,
            denial_reason=CapabilityDenialReason.NO_MATCHING_GRANT,
            decided_at=now,
        )

    furthest: CapabilityDenialReason = CapabilityDenialReason.NO_MATCHING_GRANT
    furthest_grant_id: str | None = None
    for grant in candidates:
        reason, approval = _evaluate(grant, request, approvals, now, fingerprint)
        if reason is None:
            return CapabilityDecision(
                effect=CapabilityDecisionEffect.ALLOW,
                request_fingerprint=fingerprint,
                grant_id=grant.grant_id,
                approval_id=None if approval is None else approval.approval_id,
                decided_at=now,
            )
        if _stage_of(reason) >= _stage_of(furthest):
            furthest = reason
            furthest_grant_id = grant.grant_id

    return CapabilityDecision(
        effect=CapabilityDecisionEffect.DENY,
        request_fingerprint=fingerprint,
        grant_id=furthest_grant_id,
        denial_reason=furthest,
        decided_at=now,
    )


def _stage_of(reason: CapabilityDenialReason) -> int:
    if reason not in _DENIAL_STAGES:
        return -1
    return _DENIAL_STAGES.index(reason)


__all__ = [
    "APPROVAL_REQUIRED_SENSITIVITIES",
    "ApprovalRef",
    "CapabilityDecision",
    "CapabilityDecisionEffect",
    "CapabilityDenialReason",
    "CapabilityGrant",
    "CapabilityRequest",
    "SensitivityClass",
    "decide_capability",
]
