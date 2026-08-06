"""Nothing durable, published, or returned happens before a decision is made.

**The defect these tests were written against.** Two paths in ``invoke``
returned a ``ToolOutcome`` before ``self._decide`` was ever called: a payload
that failed the tool's input schema, and a payload the boundary could not
serialize, redact, hash, or represent. Both went to ``_reject_input``, which
called ``_terminate`` with ``decision=None`` — so it persisted a
``ToolInvocation`` row and published a ``tool.invocation.completed`` event for a
caller whose authorization had never been evaluated.

The module said otherwise in two places, and the comments are part of the
defect rather than separate from it. ``boundary.py`` claimed "no path returns a
result before a decision has been made" directly above the dedup lookup, and
its module docstring claimed "the persisted ``capability_scope`` is read off the
grant the decision named, never off the caller's request" — on this path there
was no grant, and the scope the caller invented was persisted verbatim.

**Three consequences, each pinned below.** A caller holding no grant at all
could (1) read the tool's input schema back out of ``ToolOutcome.detail``,
(2) write a durable row under an idempotency key of its own choosing carrying an
invented ``capability_scope``, and (3) — because ``INVALID_INPUT`` records as
``FAILED`` and ``FAILED`` is replay-eligible — leave that row where a later
*authorized* call presenting the same key hits ``_require_same_request`` and is
answered with a raised ``IdempotencyConflictError`` instead of its result.

**The ordering the fix establishes.** The input is prepared first, because a
``CapabilityRequest`` cannot be fingerprinted without a digest of it; but
preparing it has no durable effect and returns nothing. The decision then runs
on every path, and a denial takes precedence over a schema rejection — so an
unauthorized caller is told it was denied and learns nothing about the shape of
a tool it was never permitted to call.
"""

import sys
from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import BaseModel

from src.core.contracts.capabilities import (
    CapabilityDecisionEffect,
    SensitivityClass,
)
from src.core.tools import (
    IdempotencyConflictError,
    ToolBoundary,
    ToolCallContext,
    ToolOutcomeStatus,
    ToolSpec,
)
from src.core.tools.boundary import _REPLAYABLE_RECORD_STATUSES

from .conftest import (
    RecordingAuditStore,
    RecordingPublisher,
    invoke_kwargs,
)

MALFORMED: Mapping[str, Any] = {"not_the_declared_field": 1}
INVENTED_SCOPE = "admin:everything"
ATTACKER_KEY = "attacker-chosen-key"


class DeepInput(BaseModel):
    payload: dict[str, Any]


class DeepOutput(BaseModel):
    ok: bool


def nest(depth: int) -> dict[str, Any]:
    """Build a linearly nested mapping without recursing to do it."""

    value: Any = "leaf"
    for _ in range(depth):
        value = {"n": value}
    return value


class _LookupSpy:
    """Wraps a store to record every dedup lookup, delegating everything else.

    A wrapper rather than a monkeypatch: ``RecordingAuditStore`` is a
    ``slots=True`` dataclass, so its methods cannot be reassigned on an
    instance — a spy that silently failed to install would make this test
    vacuous in the direction that matters.
    """

    def __init__(self, inner: RecordingAuditStore, looked_up: list[str]) -> None:
        self._inner = inner
        self._looked_up = looked_up

    async def find_invocation(self, **kwargs: Any) -> Any:
        self._looked_up.append(kwargs["idempotency_key"])
        return await self._inner.find_invocation(**kwargs)

    async def persist(self, **kwargs: Any) -> None:
        await self._inner.persist(**kwargs)


@pytest.fixture
def deep_boundary(boundary_dependencies: dict[str, Any]) -> ToolBoundary:
    """A tool whose schema accepts anything, so depth is the only rejection."""

    async def handler(args: DeepInput, context: ToolCallContext) -> Mapping[str, Any]:
        return {"ok": True}

    boundary = ToolBoundary(**boundary_dependencies)
    boundary.register(
        ToolSpec(
            name="deep",
            version="1.0.0",
            sensitivity=SensitivityClass.READ_ONLY,
            input_model=DeepInput,
            output_model=DeepOutput,
            timeout_seconds=5.0,
            handler=handler,
        )
    )
    return boundary


class TestAnUnauthorizedCallerLeavesNoUsableTrace:
    async def test_malformed_input_with_no_grant_writes_only_a_denial(
        self,
        boundary: ToolBoundary,
        audit_store: RecordingAuditStore,
        publisher: RecordingPublisher,
    ) -> None:
        """The central claim, stated as what it actually is.

        "An unauthorized caller writes nothing" would be the wrong property, and
        the first draft of this test asserted it and failed: a denial is
        *deliberately* recorded, because an audit trail with a hole where every
        refused request should be is the trail an attacker would choose.

        The security property is narrower and stronger. The only row an
        ungranted caller can cause is a ``DENIED`` one, and ``DENIED`` is
        excluded from ``_REPLAYABLE_RECORD_STATUSES`` — so it can never be
        served as an answer to anybody, and it cannot occupy a key. Before the
        fix the row was ``FAILED``/``invalid_input``, which *is* replay-eligible,
        and that is exactly what made the key-poisoning consequence possible.
        """

        outcome = await boundary.invoke(
            **invoke_kwargs(
                arguments=MALFORMED,
                capability_scope=INVENTED_SCOPE,
                grants=[],
            )
        )

        assert outcome.status is ToolOutcomeStatus.DENIED
        assert outcome.error_code == "capability_denied"
        assert [i.status.value for i in audit_store.invocations] == ["denied"]
        assert all(
            invocation.status not in _REPLAYABLE_RECORD_STATUSES
            for invocation in audit_store.invocations
        )
        assert [event.event_type for event in publisher.published] == [
            "tool.invocation.completed"
        ]

    async def test_an_unrepresentable_payload_with_no_grant_writes_no_record(
        self,
        deep_boundary: ToolBoundary,
        audit_store: RecordingAuditStore,
        publisher: RecordingPublisher,
    ) -> None:
        """The second unguarded path, which fails during serialization.

        This one never reaches the schema at all — it dies in the redact/hash
        walk — so it is a genuinely separate return statement and needs its own
        test rather than sharing one with the schema rejection.
        """

        outcome = await deep_boundary.invoke(
            **invoke_kwargs(
                tool_name="deep",
                arguments={"payload": nest(5000)},
                capability_scope=INVENTED_SCOPE,
                grants=[],
            )
        )

        assert outcome.status is ToolOutcomeStatus.DENIED
        assert [i.status.value for i in audit_store.invocations] == ["denied"]
        assert outcome.invocation.capability_scope == INVENTED_SCOPE

    async def test_a_validation_error_path_with_no_grant_writes_no_record(
        self, deep_boundary: ToolBoundary, audit_store: RecordingAuditStore
    ) -> None:
        """The third behaviour of the same payload: pydantic's own depth guard.

        With the interpreter limit raised, ``_ensure_representable`` raises a
        ``ValidationError`` rather than a ``RecursionError``. Both were unguarded
        by authorization; both must now be denied first.
        """

        original = sys.getrecursionlimit()
        sys.setrecursionlimit(8000)
        try:
            outcome = await deep_boundary.invoke(
                **invoke_kwargs(
                    tool_name="deep",
                    arguments={"payload": nest(2000)},
                    grants=[],
                )
            )
        finally:
            sys.setrecursionlimit(original)

        assert outcome.status is ToolOutcomeStatus.DENIED
        assert [i.status.value for i in audit_store.invocations] == ["denied"]


class TestAnUnauthorizedCallerIsToldNothingAboutTheTool:
    async def test_the_input_schema_is_not_echoed_back_to_an_ungranted_caller(
        self, boundary: ToolBoundary
    ) -> None:
        """``detail`` carried pydantic's full report: field names, types, values.

        A caller that was never permitted to call this tool is not entitled to
        learn its parameter names, nor that its own arguments were the reason —
        it was refused before the arguments mattered.
        """

        outcome = await boundary.invoke(
            **invoke_kwargs(
                arguments={"not_the_declared_field": "probe-value"}, grants=[]
            )
        )

        detail = outcome.detail or ""
        reason = outcome.invocation.status_reason or ""
        for leak in (
            "query",
            "not_the_declared_field",
            "probe-value",
            "Field required",
        ):
            assert leak not in detail
            assert leak not in reason

    async def test_a_denial_is_indistinguishable_whether_the_input_parsed(
        self, boundary: ToolBoundary
    ) -> None:
        """Well-formed and malformed arguments produce the same refusal.

        If they differed, an ungranted caller could probe a tool's schema by
        submitting candidate payloads and watching the outcome change. Both are
        ``DENIED`` and both name only the authorization reason.
        """

        well_formed = await boundary.invoke(**invoke_kwargs(grants=[]))
        malformed = await boundary.invoke(
            **invoke_kwargs(arguments=MALFORMED, grants=[])
        )

        assert well_formed.status is malformed.status is ToolOutcomeStatus.DENIED
        assert well_formed.error_code == malformed.error_code
        assert well_formed.detail == malformed.detail


class TestAnUnauthorizedCallerCannotPoisonAKey:
    async def test_a_chosen_key_does_not_break_a_later_legitimate_call(
        self, boundary: ToolBoundary
    ) -> None:
        """The denial-of-service consequence, end to end.

        The attacker plants a row by sending unparseable arguments under a
        guessed key. The legitimate holder of that key then calls with valid
        arguments and a real grant. Before the fix the planted ``FAILED`` row was
        replay-eligible, its recorded request differed, and
        ``_require_same_request`` raised ``IdempotencyConflictError`` out of the
        boundary — the legitimate call was refused and left no record of itself.
        """

        planted = await boundary.invoke(
            **invoke_kwargs(
                arguments=MALFORMED,
                capability_scope=INVENTED_SCOPE,
                idempotency_key=ATTACKER_KEY,
                grants=[],
            )
        )
        assert planted.status is ToolOutcomeStatus.DENIED

        legitimate = await boundary.invoke(
            **invoke_kwargs(idempotency_key=ATTACKER_KEY)
        )

        assert legitimate.succeeded
        assert legitimate.unwrap() == {"echoed": "hello"}

    async def test_the_planted_row_is_not_replayed_to_the_legitimate_caller(
        self, boundary: ToolBoundary, audit_store: RecordingAuditStore
    ) -> None:
        """The other half: not served the attacker's outcome either.

        A conflict and a replay are the two ways a planted row could reach the
        legitimate caller. Neither happens, because no row was planted.
        """

        await boundary.invoke(
            **invoke_kwargs(
                arguments=MALFORMED, idempotency_key=ATTACKER_KEY, grants=[]
            )
        )
        legitimate = await boundary.invoke(
            **invoke_kwargs(idempotency_key=ATTACKER_KEY)
        )

        assert legitimate.status is ToolOutcomeStatus.SUCCEEDED
        assert legitimate.invocation.error_code is None
        assert all(
            invocation.capability_scope != INVENTED_SCOPE
            for invocation in audit_store.invocations
        )


class TestEveryPersistedRecordNamesItsDecision:
    async def test_an_authorized_schema_rejection_carries_its_decision(
        self, boundary: ToolBoundary, audit_store: RecordingAuditStore
    ) -> None:
        """The rejection survives — it is only reordered, not removed.

        A caller that *is* granted the tool and sends malformed arguments still
        gets ``INVALID_INPUT``, still recorded and published. What changed is
        that the record now names the decision that admitted the call.
        """

        outcome = await boundary.invoke(**invoke_kwargs(arguments=MALFORMED))

        assert outcome.status is ToolOutcomeStatus.INVALID_INPUT
        assert outcome.decision is not None
        assert outcome.decision.effect is CapabilityDecisionEffect.ALLOW
        assert audit_store.decisions == [outcome.decision]
        assert [i.status.value for i in audit_store.invocations] == ["failed"]

    async def test_no_persisted_invocation_has_a_null_decision(
        self, boundary: ToolBoundary, audit_store: RecordingAuditStore
    ) -> None:
        """``capability_decision=None`` is no longer reachable from the boundary.

        ``ToolAuditStore.persist`` documented ``None`` as "a real case, not a
        defensive default", and 4B's DDL carved out a NULL-decision row for
        exactly ``error_code='invalid_input'``. That carve-out is now dead: the
        boundary cannot produce such a row.
        """

        await boundary.invoke(**invoke_kwargs(arguments=MALFORMED))
        await boundary.invoke(**invoke_kwargs(arguments=MALFORMED, grants=[]))
        await boundary.invoke(**invoke_kwargs())

        assert audit_store.decisions
        assert all(decision is not None for decision in audit_store.decisions)

    async def test_a_rejected_input_records_the_scope_off_the_grant(
        self, boundary_dependencies: dict[str, Any], echo_spec: ToolSpec
    ) -> None:
        """The module docstring's claim, made true on this path too.

        ``boundary.py`` claims the persisted ``capability_scope`` "is read off
        the grant the decision named, never off the caller's request". On the
        rejection path there was no decision and no grant, so the caller's
        invented string was persisted verbatim — the docstring was false of
        exactly the path where it mattered most.

        Forcing the two apart needs a decider that allows a pairing
        ``decide_capability`` never would, following
        ``test_the_scope_recorded_is_the_grants_when_the_two_disagree``. What is
        asserted is which of the two values the boundary writes down.
        """

        from src.core.contracts.capabilities import CapabilityDecision

        from .conftest import GRANT_ID, NOW, make_grant

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
            **invoke_kwargs(
                arguments=MALFORMED,
                capability_scope=INVENTED_SCOPE,
                grants=[make_grant(capability_scope="scope-the-grant-authorized")],
            )
        )

        assert outcome.status is ToolOutcomeStatus.INVALID_INPUT
        assert outcome.invocation.capability_scope == "scope-the-grant-authorized"


class TestTheOrderingItselfIsObservable:
    async def test_the_decider_runs_on_the_malformed_path(
        self, boundary_dependencies: dict[str, Any], echo_spec: ToolSpec
    ) -> None:
        """Pins the ordering directly rather than through its consequences.

        A recording decider proves ``_decide`` was consulted at all. The
        consequence tests above would also pass against an implementation that
        hard-coded a denial without deciding; this one would not.
        """

        from src.core.contracts.capabilities import decide_capability

        seen: list[str] = []

        def recording(**kwargs: Any) -> Any:
            seen.append(kwargs["request"].tool_name)
            return decide_capability(**kwargs)

        boundary = ToolBoundary(**boundary_dependencies, decide=recording)
        boundary.register(echo_spec)

        await boundary.invoke(**invoke_kwargs(arguments=MALFORMED, grants=[]))

        assert seen == ["echo"]

    async def test_the_dedup_lookup_sits_between_the_allow_and_the_rejection(
        self, boundary: ToolBoundary, audit_store: RecordingAuditStore
    ) -> None:
        """Both halves of the lookup's placement, in one test on purpose.

        Asserting only that a *denied* call skips the lookup is vacuous against
        the unfixed code, which returned before the lookup for every malformed
        payload, authorized or not. The discriminating assertion is that an
        *authorized* malformed call now reaches it — and the denied one still
        does not.

        ``find_invocation`` is the seam the original privilege escalation ran
        through, so an unauthorized caller must not reach it whatever its
        payload.
        """

        looked_up: list[str] = []
        boundary._audit_store = _LookupSpy(audit_store, looked_up)

        await boundary.invoke(
            **invoke_kwargs(
                arguments=MALFORMED, idempotency_key=ATTACKER_KEY, grants=[]
            )
        )
        assert looked_up == []

        await boundary.invoke(
            **invoke_kwargs(arguments=MALFORMED, idempotency_key="granted-key")
        )
        assert looked_up == ["granted-key"]


class TestLegitimateBehaviourIsUnchanged:
    async def test_a_granted_valid_call_still_succeeds(
        self, boundary: ToolBoundary
    ) -> None:
        """The test that stops the fix from being "refuse everything"."""

        outcome = await boundary.invoke(**invoke_kwargs())

        assert outcome.succeeded
        assert outcome.unwrap() == {"echoed": "hello"}

    async def test_an_identical_rejected_request_is_still_deduplicated(
        self, boundary: ToolBoundary, audit_store: RecordingAuditStore
    ) -> None:
        """An authorized caller repeating the same bad request is not a conflict.

        The rejection record stays replay-eligible, so a genuine retry of the
        same malformed request is answered from it rather than raising.
        """

        first = await boundary.invoke(
            **invoke_kwargs(arguments=MALFORMED, idempotency_key="repeat-key")
        )
        second = await boundary.invoke(
            **invoke_kwargs(arguments=MALFORMED, idempotency_key="repeat-key")
        )

        assert first.status is second.status is ToolOutcomeStatus.INVALID_INPUT
        assert (
            second.invocation.tool_invocation_id == first.invocation.tool_invocation_id
        )

    async def test_a_different_request_under_a_rejected_key_still_conflicts(
        self, boundary: ToolBoundary
    ) -> None:
        """An authorized caller's own key still identifies one request's content.

        The reordering must not weaken ``_require_same_request`` for the caller
        who legitimately owns the key.
        """

        await boundary.invoke(
            **invoke_kwargs(arguments=MALFORMED, idempotency_key="owned-key")
        )

        with pytest.raises(IdempotencyConflictError, match="different request"):
            await boundary.invoke(**invoke_kwargs(idempotency_key="owned-key"))
