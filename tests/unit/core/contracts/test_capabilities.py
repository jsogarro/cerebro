"""Capability grants are task-scoped and deny by default.

The property under test is not "allow works". It is that **every** path which
is not an explicit, live, satisfied grant returns a denial — including the
paths a caller forgets to think about: no grants at all, a grant for a
neighbouring task, an expired grant, a grant that tolerates less taint than the
input carries, and an approval replayed from a different request.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.core.contracts import TrustClassification
from src.core.contracts.capabilities import (
    APPROVAL_REQUIRED_SENSITIVITIES,
    ApprovalRef,
    CapabilityDecision,
    CapabilityDecisionEffect,
    CapabilityDenialReason,
    CapabilityGrant,
    CapabilityRequest,
    SensitivityClass,
    decide_capability,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _request(**overrides: object) -> CapabilityRequest:
    payload: dict[str, object] = {
        "run_id": "run-001",
        "task_id": "task-001",
        "attempt_id": "attempt-001",
        "tool_name": "web-search",
        "tool_version": "1.0.0",
        "capability_scope": "research:read",
        "sensitivity": SensitivityClass.READ_ONLY,
        "input_trust": TrustClassification.USER_SUPPLIED,
        "input_sha256": "a" * 64,
        "requested_at": NOW,
    }
    return CapabilityRequest(**(payload | overrides))  # type: ignore[arg-type]


def _grant(**overrides: object) -> CapabilityGrant:
    payload: dict[str, object] = {
        "grant_id": "grant-001",
        "run_id": "run-001",
        "task_id": "task-001",
        "capability_scope": "research:read",
        "tool_name": "web-search",
        "tool_versions": ("1.0.0",),
        "sensitivity": SensitivityClass.READ_ONLY,
        "max_input_trust": TrustClassification.EXTERNAL_UNTRUSTED,
        "requires_approval": False,
        "issued_at": NOW - timedelta(minutes=1),
        "expires_at": NOW + timedelta(minutes=10),
    }
    return CapabilityGrant(**(payload | overrides))  # type: ignore[arg-type]


def test_a_satisfied_grant_allows() -> None:
    decision = decide_capability(request=_request(), grants=(_grant(),), now=NOW)

    assert decision.effect is CapabilityDecisionEffect.ALLOW
    assert decision.grant_id == "grant-001"
    assert decision.denial_reason is None


def test_no_grants_at_all_denies() -> None:
    decision = decide_capability(request=_request(), grants=(), now=NOW)

    assert decision.effect is CapabilityDecisionEffect.DENY
    assert decision.denial_reason is CapabilityDenialReason.NO_MATCHING_GRANT
    assert decision.grant_id is None


def test_a_grant_for_a_neighbouring_task_denies() -> None:
    """Capabilities are issued per task, not as a global tool catalog."""
    decision = decide_capability(
        request=_request(), grants=(_grant(task_id="task-002"),), now=NOW
    )

    assert decision.effect is CapabilityDecisionEffect.DENY
    assert decision.denial_reason is CapabilityDenialReason.NO_MATCHING_GRANT


def test_a_grant_for_a_different_run_denies() -> None:
    decision = decide_capability(
        request=_request(), grants=(_grant(run_id="run-002"),), now=NOW
    )

    assert decision.effect is CapabilityDecisionEffect.DENY


def test_a_grant_for_a_different_scope_or_tool_denies() -> None:
    for override in (
        {"capability_scope": "research:write"},
        {"tool_name": "http-post"},
    ):
        decision = decide_capability(
            request=_request(), grants=(_grant(**override),), now=NOW
        )

        assert decision.effect is CapabilityDecisionEffect.DENY, override


def test_an_ungranted_tool_version_denies() -> None:
    decision = decide_capability(
        request=_request(tool_version="2.0.0"), grants=(_grant(),), now=NOW
    )

    assert decision.denial_reason is CapabilityDenialReason.TOOL_VERSION_NOT_GRANTED


def test_an_expired_grant_denies() -> None:
    decision = decide_capability(
        request=_request(), grants=(_grant(),), now=NOW + timedelta(hours=1)
    )

    assert decision.denial_reason is CapabilityDenialReason.GRANT_EXPIRED


def test_a_grant_not_yet_in_force_denies() -> None:
    decision = decide_capability(
        request=_request(), grants=(_grant(),), now=NOW - timedelta(hours=1)
    )

    assert decision.denial_reason is CapabilityDenialReason.GRANT_NOT_YET_VALID


def test_input_taint_beyond_what_the_grant_tolerates_denies() -> None:
    """The source-to-sink rule: derived-untrusted content may not reach this sink."""
    decision = decide_capability(
        request=_request(input_trust=TrustClassification.DERIVED_UNTRUSTED),
        grants=(_grant(max_input_trust=TrustClassification.APPLICATION),),
        now=NOW,
    )

    assert decision.denial_reason is CapabilityDenialReason.INPUT_TRUST_EXCEEDS_GRANT


def test_a_sensitivity_mismatch_denies() -> None:
    decision = decide_capability(
        request=_request(sensitivity=SensitivityClass.INTERNAL_WRITE),
        grants=(_grant(),),
        now=NOW,
    )

    assert decision.denial_reason is CapabilityDenialReason.SENSITIVITY_MISMATCH


def test_external_write_and_exfiltration_always_require_approval() -> None:
    assert (
        frozenset({SensitivityClass.EXTERNAL_WRITE, SensitivityClass.EXFILTRATION})
        == APPROVAL_REQUIRED_SENSITIVITIES
    )


def test_a_grant_cannot_waive_approval_for_a_sensitive_sink() -> None:
    for sensitivity in APPROVAL_REQUIRED_SENSITIVITIES:
        with pytest.raises(ValidationError, match="requires approval"):
            _grant(sensitivity=sensitivity, requires_approval=False)


def test_a_sensitive_action_without_approval_denies() -> None:
    decision = decide_capability(
        request=_request(sensitivity=SensitivityClass.EXTERNAL_WRITE),
        grants=(
            _grant(sensitivity=SensitivityClass.EXTERNAL_WRITE, requires_approval=True),
        ),
        now=NOW,
    )

    assert decision.denial_reason is CapabilityDenialReason.APPROVAL_MISSING


def test_an_approval_for_a_different_request_cannot_be_replayed() -> None:
    request = _request(sensitivity=SensitivityClass.EXTERNAL_WRITE)
    other = _request(sensitivity=SensitivityClass.EXTERNAL_WRITE, input_sha256="b" * 64)
    grant = _grant(sensitivity=SensitivityClass.EXTERNAL_WRITE, requires_approval=True)
    approval = ApprovalRef(
        approval_id="approval-001",
        grant_id=grant.grant_id,
        request_fingerprint=other.fingerprint(),
        approved_by="user-001",
        approved_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )

    decision = decide_capability(
        request=request, grants=(grant,), approvals=(approval,), now=NOW
    )

    assert (
        decision.denial_reason is CapabilityDenialReason.APPROVAL_FINGERPRINT_MISMATCH
    )


def test_an_expired_approval_denies() -> None:
    request = _request(sensitivity=SensitivityClass.EXFILTRATION)
    grant = _grant(sensitivity=SensitivityClass.EXFILTRATION, requires_approval=True)
    approval = ApprovalRef(
        approval_id="approval-001",
        grant_id=grant.grant_id,
        request_fingerprint=request.fingerprint(),
        approved_by="user-001",
        approved_at=NOW - timedelta(hours=2),
        expires_at=NOW - timedelta(hours=1),
    )

    decision = decide_capability(
        request=request, grants=(grant,), approvals=(approval,), now=NOW
    )

    assert decision.denial_reason is CapabilityDenialReason.APPROVAL_EXPIRED


def test_a_matching_live_approval_allows_a_sensitive_action() -> None:
    request = _request(sensitivity=SensitivityClass.EXTERNAL_WRITE)
    grant = _grant(sensitivity=SensitivityClass.EXTERNAL_WRITE, requires_approval=True)
    approval = ApprovalRef(
        approval_id="approval-001",
        grant_id=grant.grant_id,
        request_fingerprint=request.fingerprint(),
        approved_by="user-001",
        approved_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
    )

    decision = decide_capability(
        request=request, grants=(grant,), approvals=(approval,), now=NOW
    )

    assert decision.effect is CapabilityDecisionEffect.ALLOW
    assert decision.approval_id == "approval-001"


def test_an_allow_decision_cannot_exist_without_a_grant() -> None:
    with pytest.raises(ValidationError, match="names the grant"):
        CapabilityDecision(
            effect=CapabilityDecisionEffect.ALLOW,
            request_fingerprint="c" * 64,
            grant_id=None,
            denial_reason=None,
            decided_at=NOW,
        )


def test_a_deny_decision_cannot_exist_without_a_reason() -> None:
    with pytest.raises(ValidationError, match="denial reason"):
        CapabilityDecision(
            effect=CapabilityDecisionEffect.DENY,
            request_fingerprint="c" * 64,
            grant_id=None,
            denial_reason=None,
            decided_at=NOW,
        )


def test_the_decision_is_order_independent_across_equivalent_grants() -> None:
    matching = _grant(grant_id="grant-b")
    stale = _grant(
        grant_id="grant-a",
        issued_at=NOW - timedelta(hours=2),
        expires_at=NOW - timedelta(hours=1),
    )

    forward = decide_capability(request=_request(), grants=(stale, matching), now=NOW)
    reverse = decide_capability(request=_request(), grants=(matching, stale), now=NOW)

    assert forward.effect is CapabilityDecisionEffect.ALLOW
    assert forward.grant_id == reverse.grant_id == "grant-b"


def test_a_request_fingerprint_covers_every_field_that_changes_authorization() -> None:
    baseline = _request().fingerprint()

    for override in (
        {"tool_name": "http-post"},
        {"tool_version": "2.0.0"},
        {"capability_scope": "research:write"},
        {"sensitivity": SensitivityClass.EXTERNAL_WRITE},
        {"input_trust": TrustClassification.DERIVED_UNTRUSTED},
        {"input_sha256": "b" * 64},
        {"task_id": "task-002"},
        {"run_id": "run-002"},
    ):
        assert _request(**override).fingerprint() != baseline, override


def test_a_grant_must_expire() -> None:
    with pytest.raises(ValidationError, match="expires_at"):
        _grant(expires_at=NOW - timedelta(days=1), issued_at=NOW)


def test_a_grant_must_name_at_least_one_tool_version() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        _grant(tool_versions=())
