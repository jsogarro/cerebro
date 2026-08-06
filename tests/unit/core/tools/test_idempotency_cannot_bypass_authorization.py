"""An idempotency key identifies a request. It is not a capability.

**The defect these tests were written against.** The replay lookup ran *before*
the capability decision and keyed on `(run_id, organization_id,
idempotency_key)` alone. Any caller able to choose a key could therefore
present a key from a prior authorized call, name a different tool and an
escalated `capability_scope`, supply no grant at all — and receive the earlier
result as a SUCCEEDED outcome with `decision=None`. Authorization was never
reached. The control was present, correct, and unreachable.

**Two things were wrong, and both are fixed.** The comparison was missing, but
the *ordering* was the root cause: a check that runs after a `return` is not a
check. Authorization now always runs, before any lookup, so the bypass is
closed structurally rather than by a comparison that a future edit could get
subtly wrong. The identity comparison then sits on top of it as the thing that
distinguishes a genuine retry from a collision.

**Why a conflict raises instead of being recorded.** `agent_tool_invocations`
carries `UniqueConstraint(attempt_id, idempotency_key)`, so a conflict cannot
be written as a second row under that key — the key already belongs to a
different request. It is also the "asked to do something that cannot be done
safely" class rather than the "here is what happened to your call" class: there
is no defensible result to return and nothing safe to record.

Scenario ids refer to the adversarial corpus in `tests/security/adversarial/`.
"""

from typing import Any

import pytest
from pydantic import BaseModel

from src.core.contracts.capabilities import SensitivityClass
from src.core.tools import (
    IdempotencyConflictError,
    ToolBoundary,
    ToolCallContext,
    ToolOutcomeStatus,
    ToolSpec,
)

from .conftest import (
    RUN_ID,
    EchoInput,
    EchoOutput,
    RecordingAuditStore,
    invoke_kwargs,
    make_grant,
)

SHARED_KEY = "caller-chosen-key"
PRIVILEGED = "delete_project"


class DeleteInput(BaseModel):
    project_id: str


class DeleteOutput(BaseModel):
    deleted: str


@pytest.fixture
def privileged_calls() -> list[str]:
    return []


@pytest.fixture
def boundary_with_privileged_tool(
    boundary_dependencies: dict[str, Any],
    echo_spec: ToolSpec,
    privileged_calls: list[str],
) -> ToolBoundary:
    """A boundary holding a benign tool and a destructive one."""

    async def delete(args: DeleteInput, context: ToolCallContext) -> dict[str, str]:
        privileged_calls.append(args.project_id)
        return {"deleted": args.project_id}

    boundary = ToolBoundary(**boundary_dependencies)
    boundary.register(echo_spec)
    boundary.register(
        ToolSpec(
            name=PRIVILEGED,
            version="1.0.0",
            sensitivity=SensitivityClass.READ_ONLY,
            input_model=DeleteInput,
            output_model=DeleteOutput,
            timeout_seconds=5.0,
            handler=delete,
        )
    )
    return boundary


class TestAKeyCannotCarryAuthorization:
    async def test_a_reused_key_cannot_reach_an_ungranted_tool(
        self,
        boundary_with_privileged_tool: ToolBoundary,
        privileged_calls: list[str],
        audit_store: RecordingAuditStore,
    ) -> None:
        """privesc-03. The reported CRITICAL, reproduced.

        The first call is legitimately authorized. The second names a
        destructive tool under an escalated scope with **no grant**, reusing
        the key. Before the fix this returned the first call's result as a
        success with no decision at all.
        """

        first = await boundary_with_privileged_tool.invoke(
            **invoke_kwargs(idempotency_key=SHARED_KEY)
        )
        assert first.succeeded

        second = await boundary_with_privileged_tool.invoke(
            **invoke_kwargs(
                tool_name=PRIVILEGED,
                capability_scope="admin:delete_project",
                arguments={"project_id": "p-1"},
                idempotency_key=SHARED_KEY,
                grants=[],
            )
        )

        # Denied, not conflicted: with no grant the call never reaches the dedup
        # lookup at all. That is the ordering doing the work — the escalation is
        # refused by authorization, which is a stronger place to stop it than a
        # key comparison downstream.
        assert second.status is ToolOutcomeStatus.DENIED
        assert second.decision is not None
        assert privileged_calls == []
        assert second.invocation.output is None
        assert second.invocation.tool_name == PRIVILEGED

    async def test_an_authorized_caller_still_cannot_collect_another_calls_result(
        self,
        boundary_with_privileged_tool: ToolBoundary,
        privileged_calls: list[str],
    ) -> None:
        """The other half of privesc-03: dedup reached, and it refuses.

        Here the caller *is* granted the destructive tool, so authorization
        allows and the lookup is actually consulted. The recorded row under that
        key belongs to a different request, so it is a conflict — the caller is
        not handed the earlier tool's result under their own tool's name.
        """

        first = await boundary_with_privileged_tool.invoke(
            **invoke_kwargs(idempotency_key=SHARED_KEY)
        )
        assert first.succeeded

        with pytest.raises(IdempotencyConflictError, match="different request"):
            await boundary_with_privileged_tool.invoke(
                **invoke_kwargs(
                    tool_name=PRIVILEGED,
                    capability_scope="admin:delete_project",
                    arguments={"project_id": "p-1"},
                    idempotency_key=SHARED_KEY,
                    grants=[
                        make_grant(
                            tool_name=PRIVILEGED,
                            capability_scope="admin:delete_project",
                        )
                    ],
                )
            )

        assert privileged_calls == []

    async def test_the_escalated_request_is_authorized_on_its_own_terms(
        self, boundary_with_privileged_tool: ToolBoundary
    ) -> None:
        """Authorization runs *before* any lookup, so it cannot be skipped.

        Same escalation without a key collision: the answer is a denial reached
        by actually deciding, not by failing to find a cache entry.
        """

        outcome = await boundary_with_privileged_tool.invoke(
            **invoke_kwargs(
                tool_name=PRIVILEGED,
                capability_scope="admin:delete_project",
                arguments={"project_id": "p-1"},
                grants=[],
            )
        )

        assert outcome.status is ToolOutcomeStatus.DENIED
        assert outcome.decision is not None

    async def test_a_replayed_result_now_carries_the_decision_that_allowed_it(
        self, boundary: ToolBoundary
    ) -> None:
        """A success with `decision=None` was the tell, and it is gone.

        Every SUCCEEDED outcome — replayed or freshly executed — names the
        authorization that permitted this caller to have it.
        """

        first = await boundary.invoke(**invoke_kwargs(idempotency_key=SHARED_KEY))
        second = await boundary.invoke(**invoke_kwargs(idempotency_key=SHARED_KEY))

        assert first.succeeded and second.succeeded
        assert second.decision is not None

    async def test_a_replay_still_requires_current_authorization(
        self, boundary: ToolBoundary
    ) -> None:
        """A grant revoked between the two calls stops the second one.

        The recorded result is not a bearer token. If dedup ran first, a
        caller who has since lost the grant would still be served.
        """

        first = await boundary.invoke(**invoke_kwargs(idempotency_key=SHARED_KEY))
        assert first.succeeded

        second = await boundary.invoke(
            **invoke_kwargs(idempotency_key=SHARED_KEY, grants=[])
        )

        assert second.status is ToolOutcomeStatus.DENIED


class TestARecordedDenialIsNotAnAnswerForSomeoneElse:
    async def test_an_authorized_caller_is_not_served_an_earlier_denial(
        self, boundary_dependencies: dict[str, Any], echo_spec: ToolSpec
    ) -> None:
        """Found while verifying the bypass fix, and it is the same defect class.

        An unauthorized attempt records a DENIED row under the key. The
        authorized caller then presents the same key with byte-identical
        parameters, so the identity comparison matches and the denial would be
        replayed to them. A refusal earned by one caller must never become
        another caller's answer — authorization is decided fresh every call, so
        its outcome cannot be cached.
        """

        runs: list[str] = []

        async def counting(args: EchoInput, context: ToolCallContext) -> dict[str, str]:
            runs.append(args.query)
            return {"echoed": args.query}

        boundary = ToolBoundary(**boundary_dependencies)
        boundary.register(
            ToolSpec(
                name="echo",
                version="1.0.0",
                sensitivity=SensitivityClass.READ_ONLY,
                input_model=EchoInput,
                output_model=EchoOutput,
                timeout_seconds=5.0,
                handler=counting,
            )
        )

        denied = await boundary.invoke(
            **invoke_kwargs(idempotency_key=SHARED_KEY, grants=[])
        )
        assert denied.status is ToolOutcomeStatus.DENIED

        allowed = await boundary.invoke(**invoke_kwargs(idempotency_key=SHARED_KEY))

        assert allowed.succeeded
        assert allowed.unwrap() == {"echoed": "hello"}
        assert runs == ["hello"]


class TestAKeyIdentifiesOneRequestsContent:
    async def test_the_same_key_with_mutated_input_is_a_conflict(
        self, boundary_dependencies: dict[str, Any], echo_spec: ToolSpec
    ) -> None:
        """replay-03. Neither served from cache nor executed as new work.

        Executing would be unaudited work under a key that already belongs to
        a different request; serving the cached result would answer a question
        nobody asked.
        """

        runs: list[str] = []

        async def counting(args: EchoInput, context: ToolCallContext) -> dict[str, str]:
            runs.append(args.query)
            return {"echoed": args.query}

        boundary = ToolBoundary(**boundary_dependencies)
        boundary.register(
            ToolSpec(
                name="echo",
                version="1.0.0",
                sensitivity=SensitivityClass.READ_ONLY,
                input_model=EchoInput,
                output_model=EchoOutput,
                timeout_seconds=5.0,
                handler=counting,
            )
        )

        await boundary.invoke(
            **invoke_kwargs(idempotency_key=SHARED_KEY, arguments={"query": "original"})
        )

        with pytest.raises(IdempotencyConflictError, match="different request"):
            await boundary.invoke(
                **invoke_kwargs(
                    idempotency_key=SHARED_KEY, arguments={"query": "mutated"}
                )
            )

        assert runs == ["original"]

    async def test_a_bit_identical_resubmission_is_served_from_the_record(
        self, boundary_dependencies: dict[str, Any], echo_spec: ToolSpec
    ) -> None:
        """replay-01. Legitimate dedup must keep working.

        This is the test that stops the fix from being "refuse everything",
        which would pass every attack scenario and break the feature.
        """

        runs: list[str] = []

        async def counting(args: EchoInput, context: ToolCallContext) -> dict[str, str]:
            runs.append(args.query)
            return {"echoed": args.query}

        boundary = ToolBoundary(**boundary_dependencies)
        boundary.register(
            ToolSpec(
                name="echo",
                version="1.0.0",
                sensitivity=SensitivityClass.READ_ONLY,
                input_model=EchoInput,
                output_model=EchoOutput,
                timeout_seconds=5.0,
                handler=counting,
            )
        )

        first = await boundary.invoke(**invoke_kwargs(idempotency_key=SHARED_KEY))
        second = await boundary.invoke(**invoke_kwargs(idempotency_key=SHARED_KEY))

        assert runs == ["hello"]
        assert second.succeeded
        assert second.unwrap() == first.unwrap()
        assert (
            second.invocation.tool_invocation_id == first.invocation.tool_invocation_id
        )

    async def test_the_same_key_under_a_different_run_is_independent(
        self, boundary_dependencies: dict[str, Any], echo_spec: ToolSpec
    ) -> None:
        """replay-04. Run identity is part of the dedup key, not an afterthought."""

        runs: list[str] = []

        async def counting(args: EchoInput, context: ToolCallContext) -> dict[str, str]:
            runs.append(context.run_id)
            return {"echoed": args.query}

        boundary = ToolBoundary(**boundary_dependencies)
        boundary.register(
            ToolSpec(
                name="echo",
                version="1.0.0",
                sensitivity=SensitivityClass.READ_ONLY,
                input_model=EchoInput,
                output_model=EchoOutput,
                timeout_seconds=5.0,
                handler=counting,
            )
        )

        await boundary.invoke(**invoke_kwargs(idempotency_key=SHARED_KEY))
        await boundary.invoke(
            **invoke_kwargs(
                idempotency_key=SHARED_KEY,
                run_id="run-2",
                grants=[make_grant().model_copy(update={"run_id": "run-2"})],
            )
        )

        assert runs == [RUN_ID, "run-2"]

    async def test_a_key_reused_across_tool_versions_is_a_conflict(
        self, boundary_dependencies: dict[str, Any]
    ) -> None:
        """Version is part of what a key identifies.

        Two versions of one tool are two different programs; a result from one
        is not a result from the other.
        """

        async def handler(args: EchoInput, context: ToolCallContext) -> dict[str, str]:
            return {"echoed": args.query}

        store: RecordingAuditStore = boundary_dependencies["audit_store"]
        boundary = ToolBoundary(**boundary_dependencies)
        boundary.register(
            ToolSpec(
                name="echo",
                version="1.0.0",
                sensitivity=SensitivityClass.READ_ONLY,
                input_model=EchoInput,
                output_model=EchoOutput,
                timeout_seconds=5.0,
                handler=handler,
            )
        )

        first = await boundary.invoke(**invoke_kwargs(idempotency_key=SHARED_KEY))
        store.stored[(RUN_ID, "org-1", SHARED_KEY)] = first.invocation.model_copy(
            update={"tool_version": "2.0.0"}
        )

        with pytest.raises(IdempotencyConflictError):
            await boundary.invoke(**invoke_kwargs(idempotency_key=SHARED_KEY))
