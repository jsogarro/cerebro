"""Authorization at the boundary: deny by default, one clock, the grant's scope.

Three of packet 4A's findings meet here.

*Deny by default* is 4A's own structural property, but it is only a property of
the system if the boundary consults the decision correctly. 4-Char found the
mirror-image mistake on the live path — ``data_source`` computed from a success
flag alone — so the tests below check that a denial denies, that the tool is
never reached, and that a ``DENY`` carrying a ``grant_id`` (which every denial
past the first stage does) is not mistaken for permission.

*Non-guarantee 6* — approval expiry uses caller-supplied ``now``, so a stale
``now`` revives an expired approval. The boundary reads its clock once and
exposes no parameter through which an instant can be supplied.

*Non-guarantee 7* — ``capability_scope`` is not cross-validated against the
decision. The boundary sources it from the grant the decision named and refuses
to execute under a decision naming a grant it cannot resolve.
"""

import inspect
from datetime import timedelta
from typing import Any

import pytest

from src.core.contracts.capabilities import (
    CapabilityDecision,
    CapabilityDecisionEffect,
    CapabilityDenialReason,
    CapabilityRequest,
    SensitivityClass,
)
from src.core.contracts.redaction import boundary_digest, redact
from src.core.contracts.trust import TrustClassification
from src.core.tools import (
    CapabilityDecisionUnusableError,
    ToolBoundary,
    ToolOutcomeNotSuccessfulError,
    ToolOutcomeStatus,
    ToolSpec,
)

from .conftest import (
    ATTEMPT_ID,
    GRANT_ID,
    NOW,
    RUN_ID,
    SCOPE,
    TASK_ID,
    TOOL_NAME,
    TOOL_VERSION,
    FrozenClock,
    RecordingAuditStore,
    invoke_kwargs,
    make_approval,
    make_grant,
)


def request_fingerprint(
    *,
    arguments: dict[str, Any] | None = None,
    sensitivity: SensitivityClass = SensitivityClass.READ_ONLY,
    input_trust: TrustClassification = TrustClassification.USER_SUPPLIED,
) -> str:
    """Recompute the fingerprint the boundary will produce for a call.

    Built the same way the boundary builds it — from the *redacted* canonical
    input — so an approval in a test binds to the same request the boundary
    authorizes, rather than to one the test wished for.
    """

    payload = redact(dict(arguments or {"query": "hello"}))
    return CapabilityRequest(
        run_id=RUN_ID,
        task_id=TASK_ID,
        attempt_id=ATTEMPT_ID,
        tool_name=TOOL_NAME,
        tool_version=TOOL_VERSION,
        capability_scope=SCOPE,
        sensitivity=sensitivity,
        input_trust=input_trust,
        input_sha256=boundary_digest(payload),  # type: ignore[arg-type]
        requested_at=NOW,
    ).fingerprint()


class TestDenialIsTheDefault:
    async def test_no_grants_at_all_denies(
        self, boundary: ToolBoundary, audit_store: RecordingAuditStore
    ) -> None:
        outcome = await boundary.invoke(**invoke_kwargs(grants=[]))

        assert outcome.status is ToolOutcomeStatus.DENIED
        assert outcome.decision is not None
        assert (
            outcome.decision.denial_reason is CapabilityDenialReason.NO_MATCHING_GRANT
        )
        assert [i.status.value for i in audit_store.invocations] == ["denied"]

    async def test_a_denied_call_never_reaches_the_tool(
        self, boundary_dependencies: dict[str, Any]
    ) -> None:
        reached: list[str] = []

        async def spy(args: Any, context: Any) -> dict[str, str]:
            reached.append(context.tool_invocation_id)
            return {"echoed": args.query}

        from .conftest import EchoInput, EchoOutput

        boundary = ToolBoundary(**boundary_dependencies)
        boundary.register(
            ToolSpec(
                name=TOOL_NAME,
                version=TOOL_VERSION,
                sensitivity=SensitivityClass.READ_ONLY,
                input_model=EchoInput,
                output_model=EchoOutput,
                timeout_seconds=5.0,
                handler=spy,
            )
        )

        outcome = await boundary.invoke(**invoke_kwargs(grants=[]))

        assert outcome.status is ToolOutcomeStatus.DENIED
        assert reached == []

    async def test_a_denial_carries_no_result_to_read(
        self, boundary: ToolBoundary
    ) -> None:
        outcome = await boundary.invoke(**invoke_kwargs(grants=[]))

        assert outcome.invocation.output is None
        with pytest.raises(ToolOutcomeNotSuccessfulError):
            outcome.unwrap()

    async def test_a_grant_for_a_sibling_task_authorizes_nothing(
        self, boundary: ToolBoundary
    ) -> None:
        sibling = make_grant().model_copy(update={"task_id": "task-2"})

        outcome = await boundary.invoke(**invoke_kwargs(grants=[sibling]))

        assert outcome.status is ToolOutcomeStatus.DENIED

    async def test_input_too_untrusted_for_the_sink_is_denied(
        self, boundary: ToolBoundary
    ) -> None:
        strict = make_grant(max_input_trust=TrustClassification.APPLICATION)

        outcome = await boundary.invoke(
            **invoke_kwargs(
                grants=[strict],
                input_trust=TrustClassification.DERIVED_UNTRUSTED,
            )
        )

        assert outcome.status is ToolOutcomeStatus.DENIED
        assert outcome.decision is not None
        assert (
            outcome.decision.denial_reason
            is CapabilityDenialReason.INPUT_TRUST_EXCEEDS_GRANT
        )

    async def test_a_denial_naming_a_grant_is_still_a_denial(
        self, boundary: ToolBoundary
    ) -> None:
        """`decide_capability` names the grant that got furthest on a DENY.

        Reading "a grant is named" as permission is the same error 4-Char found
        on the live path, where success was inferred from one flag without
        checking the branch that produced it.
        """

        expired = make_grant(
            issued_at=NOW - timedelta(hours=2), expires_at=NOW - timedelta(hours=1)
        )

        outcome = await boundary.invoke(**invoke_kwargs(grants=[expired]))

        assert outcome.decision is not None
        assert outcome.decision.grant_id == GRANT_ID
        assert outcome.decision.effect is CapabilityDecisionEffect.DENY
        assert outcome.status is ToolOutcomeStatus.DENIED


class TestTheClockIsTheBoundarys:
    def test_invoke_exposes_no_instant_a_caller_could_supply(self) -> None:
        """Non-guarantee 6, closed at the signature.

        4A made ``now`` a parameter of ``decide_capability`` so decisions replay
        deterministically, and correctly noted that a caller passing a stale one
        revives an expired approval. Determinism is preserved by injecting the
        clock; the instant is not caller-supplied.
        """

        parameters = set(inspect.signature(ToolBoundary.invoke).parameters)

        assert parameters.isdisjoint({"now", "at", "as_of", "timestamp", "decided_at"})

    async def test_an_expired_approval_cannot_be_revived(
        self, boundary: ToolBoundary
    ) -> None:
        grant = make_grant(requires_approval=True)
        stale = make_approval(
            request_fingerprint=request_fingerprint(),
            approved_at=NOW - timedelta(hours=3),
            expires_at=NOW - timedelta(hours=2),
        )

        outcome = await boundary.invoke(
            **invoke_kwargs(grants=[grant], approvals=[stale])
        )

        assert outcome.status is ToolOutcomeStatus.DENIED
        assert outcome.decision is not None
        assert outcome.decision.denial_reason is CapabilityDenialReason.APPROVAL_EXPIRED

    async def test_a_live_approval_for_the_same_request_allows(
        self, boundary: ToolBoundary
    ) -> None:
        """The negative test above is only meaningful if the positive one passes."""

        grant = make_grant(requires_approval=True)
        approval = make_approval(request_fingerprint=request_fingerprint())

        outcome = await boundary.invoke(
            **invoke_kwargs(grants=[grant], approvals=[approval])
        )

        assert outcome.succeeded
        assert outcome.decision is not None
        assert outcome.decision.approval_id == "approval-1"

    async def test_an_approval_for_a_different_request_does_not_transfer(
        self, boundary: ToolBoundary
    ) -> None:
        grant = make_grant(requires_approval=True)
        elsewhere = make_approval(
            request_fingerprint=request_fingerprint(arguments={"query": "other"})
        )

        outcome = await boundary.invoke(
            **invoke_kwargs(grants=[grant], approvals=[elsewhere])
        )

        assert outcome.status is ToolOutcomeStatus.DENIED
        assert outcome.decision is not None
        assert (
            outcome.decision.denial_reason
            is CapabilityDenialReason.APPROVAL_FINGERPRINT_MISMATCH
        )

    async def test_the_authorization_instant_is_read_once(
        self, boundary: ToolBoundary, clock: FrozenClock
    ) -> None:
        """One reading governs authorization, the breaker, and ``requested_at``.

        Two readings is two instants that can disagree, and a decision recorded
        at an instant other than the one it was made at is not replayable. A
        completed call reads the clock a second time for ``completed_at``, which
        is a measurement of when the work finished rather than an input to any
        decision — hence the denied call here, where the whole call is one
        instant and the count is exact.
        """

        clock.reads = 0

        outcome = await boundary.invoke(**invoke_kwargs(grants=[]))

        assert clock.reads == 1
        assert outcome.decision is not None
        assert outcome.decision.decided_at == NOW
        assert outcome.invocation.requested_at == NOW
        assert outcome.invocation.completed_at == NOW

    async def test_a_naive_clock_is_refused_before_anything_is_authorized(
        self, boundary_dependencies: dict[str, Any], echo_spec: ToolSpec
    ) -> None:
        """A naive instant compares wrongly against grant and approval windows."""

        boundary_dependencies["clock"] = lambda: NOW.replace(tzinfo=None)
        built = ToolBoundary(**boundary_dependencies)
        built.register(echo_spec)

        with pytest.raises(ValueError, match="aware datetime"):
            await built.invoke(**invoke_kwargs())


class TestTheRecordedScopeComesFromTheGrant:
    async def test_the_invocation_carries_the_satisfying_grants_scope(
        self, boundary: ToolBoundary, audit_store: RecordingAuditStore
    ) -> None:
        grant = make_grant()

        outcome = await boundary.invoke(**invoke_kwargs(grants=[grant]))

        assert outcome.invocation.capability_scope == grant.capability_scope
        assert all(
            invocation.capability_scope == grant.capability_scope
            for invocation in audit_store.invocations
        )

    async def test_the_scope_recorded_is_the_grants_when_the_two_disagree(
        self, boundary_dependencies: dict[str, Any], echo_spec: ToolSpec
    ) -> None:
        """Non-guarantee 7, made falsifiable.

        Under ``decide_capability`` the request's scope and the grant's are
        always equal on an ``ALLOW`` — its identity match requires it — so the
        test above cannot distinguish a boundary that sources the recorded scope
        from the authorizing grant from one that echoes back caller input. It
        passes either way, which makes it worthless as evidence for the property
        4A actually asked for.

        Forcing them apart requires a decider that allows a pairing
        ``decide_capability`` never would. What is asserted is not that this
        pairing is legal — it is not — but *which of the two values the boundary
        writes down* when it has both. A record whose scope is caller-supplied
        is a record that attests to nothing.
        """

        grant = make_grant(capability_scope="scope-the-grant-authorized")

        def decider(**kwargs: Any) -> CapabilityDecision:
            return CapabilityDecision(
                effect=CapabilityDecisionEffect.ALLOW,
                request_fingerprint="a" * 64,
                grant_id=GRANT_ID,
                decided_at=NOW,
            )

        boundary_dependencies["decide"] = decider
        built = ToolBoundary(**boundary_dependencies)
        built.register(echo_spec)

        outcome = await built.invoke(
            **invoke_kwargs(grants=[grant], capability_scope="scope-the-caller-claimed")
        )

        assert outcome.invocation.capability_scope == "scope-the-grant-authorized"

    async def test_an_allow_naming_an_unresolvable_grant_executes_nothing(
        self, boundary_dependencies: dict[str, Any], echo_spec: ToolSpec
    ) -> None:
        """The cross-validation 4A asked for, made reachable.

        ``decide_capability`` always names a grant from the list it was given,
        so this state is unreachable through it. It is reachable through any
        future decider, a caching layer, or a bug — and the safe response is not
        to execute under a permission that cannot be recorded. Constructed with
        ``model_construct`` because the validated model forbids it, the same way
        4A reached its own decision-time backstop.
        """

        def lying_decider(**kwargs: Any) -> CapabilityDecision:
            return CapabilityDecision.model_construct(
                effect=CapabilityDecisionEffect.ALLOW,
                request_fingerprint="a" * 64,
                grant_id="a-grant-nobody-issued",
                approval_id=None,
                denial_reason=None,
                decided_at=NOW,
            )

        boundary_dependencies["decide"] = lying_decider
        built = ToolBoundary(**boundary_dependencies)
        built.register(echo_spec)

        with pytest.raises(CapabilityDecisionUnusableError, match="cannot be recorded"):
            await built.invoke(**invoke_kwargs())
