"""Degraded operation is a typed, observable state — never a success shape.

This is the defect packet 4-Char characterized, stated as a test suite. On the
live path today ``data_source`` is computed as ``"mcp_tools" if success else
"fallback"`` — checking only whether a call reported success, never whether a
fallback produced the value — so a fabricated fallback result reaches the user
labelled as a real tool result. The circuit breaker compounds it: one flaky
call and a sustained outage produce the identical shape, so a caller cannot
tell them apart.

Three properties are asserted against that:

1. Timeout, cancellation, open breaker, tool error, invalid input, and invalid
   output are six **distinct** observable states, not one generic failure.
2. **No failure carries a body.** There is nowhere for a fabricated value to
   ride, and reading a result from a call that did not produce one raises.
3. A successful outcome cannot be **manufactured** outside the boundary.
"""

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest
from pydantic import BaseModel

from src.core.contracts.capabilities import SensitivityClass
from src.core.contracts.trust import TrustClassification
from src.core.tools import (
    BreakerRegistry,
    CancellationToken,
    CircuitBreaker,
    RetryDisposition,
    ToolBoundary,
    ToolCallContext,
    ToolOutcomeNotSuccessfulError,
    ToolOutcomeStatus,
    ToolSpec,
)

from .conftest import EchoInput, EchoOutput, RecordingAuditStore, invoke_kwargs

SLOW_TOOL = "slow"
ANGRY_TOOL = "angry"
LIAR_TOOL = "liar"


def build(
    boundary_dependencies: dict[str, Any],
    *,
    name: str,
    handler: Any,
    timeout_seconds: float = 5.0,
    output_model: type[BaseModel] = EchoOutput,
    breakers: BreakerRegistry | None = None,
    classify_error: Any = None,
) -> ToolBoundary:
    if breakers is not None:
        boundary_dependencies["breakers"] = breakers
    boundary = ToolBoundary(**boundary_dependencies)
    boundary.register(
        ToolSpec(
            name=name,
            version="1.0.0",
            sensitivity=SensitivityClass.READ_ONLY,
            input_model=EchoInput,
            output_model=output_model,
            timeout_seconds=timeout_seconds,
            handler=handler,
            classify_error=classify_error,
        )
    )
    return boundary


class TestEachFailureIsItsOwnState:
    async def test_a_deadline_is_reported_as_a_timeout(
        self, boundary_dependencies: dict[str, Any]
    ) -> None:
        async def sleeper(
            args: EchoInput, context: ToolCallContext
        ) -> Mapping[str, Any]:
            await asyncio.sleep(30)
            return {"echoed": "never"}

        boundary = build(
            boundary_dependencies,
            name=SLOW_TOOL,
            handler=sleeper,
            timeout_seconds=0.01,
        )

        outcome = await boundary.invoke(**invoke_kwargs(tool_name=SLOW_TOOL))

        assert outcome.status is ToolOutcomeStatus.TIMED_OUT
        assert outcome.error_code == "timed_out"
        assert outcome.retry is RetryDisposition.RETRIABLE

    async def test_a_withdrawn_request_is_reported_as_cancelled(
        self, boundary_dependencies: dict[str, Any]
    ) -> None:
        started = asyncio.Event()

        async def waiter(
            args: EchoInput, context: ToolCallContext
        ) -> Mapping[str, Any]:
            started.set()
            await asyncio.sleep(30)
            return {"echoed": "never"}

        boundary = build(boundary_dependencies, name=SLOW_TOOL, handler=waiter)
        token = CancellationToken()

        call = asyncio.ensure_future(
            boundary.invoke(**invoke_kwargs(tool_name=SLOW_TOOL, cancellation=token))
        )
        await started.wait()
        token.cancel()
        outcome = await call

        assert outcome.status is ToolOutcomeStatus.CANCELLED
        assert outcome.error_code == "cancelled"
        assert outcome.retry is RetryDisposition.TERMINAL

    async def test_an_externally_cancelled_call_does_not_leave_the_tool_running(
        self, boundary_dependencies: dict[str, Any]
    ) -> None:
        """Cancelling the caller must reach the handler, not just the record.

        ``_call_handler`` races the handler against a deadline and the
        cancellation token inside ``asyncio.wait``. When the *enclosing*
        coroutine is cancelled — ``asyncio.wait_for`` around ``invoke``, or a
        request task being torn down — the ``CancelledError`` propagates
        straight out and the handler task is never cancelled. The boundary
        still writes an honest CANCELLED record, so from the outside the call
        looks stopped while the tool goes on running: for an EXTERNAL_WRITE or
        EXFILTRATION tool that is the side effect happening after the system
        reported it would not.

        Asserted on the handler's own view rather than on the record, because
        the record was never the part that was wrong.
        """

        started = asyncio.Event()
        reached_side_effect: list[str] = []
        saw_cancellation: list[str] = []

        async def slow_writer(
            args: EchoInput, context: ToolCallContext
        ) -> Mapping[str, Any]:
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                saw_cancellation.append("cancelled")
                raise
            reached_side_effect.append("wrote")
            return {"echoed": "never"}

        boundary = build(boundary_dependencies, name=SLOW_TOOL, handler=slow_writer)

        call = asyncio.ensure_future(
            boundary.invoke(**invoke_kwargs(tool_name=SLOW_TOOL))
        )
        await started.wait()
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

        # Give any orphaned handler the chance to finish its sleep and run on.
        await asyncio.sleep(0)

        assert saw_cancellation == ["cancelled"], (
            "the handler never saw a cancellation: the boundary recorded "
            "CANCELLED and left the tool running"
        )
        assert reached_side_effect == []

    async def test_cancellation_before_dispatch_never_starts_the_tool(
        self, boundary_dependencies: dict[str, Any]
    ) -> None:
        started: list[str] = []

        async def spy(args: EchoInput, context: ToolCallContext) -> Mapping[str, Any]:
            started.append("ran")
            return {"echoed": args.query}

        boundary = build(boundary_dependencies, name=SLOW_TOOL, handler=spy)
        token = CancellationToken()
        token.cancel()

        outcome = await boundary.invoke(
            **invoke_kwargs(tool_name=SLOW_TOOL, cancellation=token)
        )

        assert outcome.status is ToolOutcomeStatus.CANCELLED
        assert started == []

    async def test_an_open_breaker_is_not_a_generic_failure(
        self, boundary_dependencies: dict[str, Any]
    ) -> None:
        """One flaky call and a sustained outage must not look the same."""

        async def angry(args: EchoInput, context: ToolCallContext) -> Mapping[str, Any]:
            raise RuntimeError("upstream is down")

        boundary = build(
            boundary_dependencies,
            name=ANGRY_TOOL,
            handler=angry,
            breakers=BreakerRegistry(lambda: CircuitBreaker(failure_threshold=1)),
        )

        first = await boundary.invoke(**invoke_kwargs(tool_name=ANGRY_TOOL))
        second = await boundary.invoke(
            **invoke_kwargs(tool_name=ANGRY_TOOL, idempotency_key="second")
        )

        assert first.status is ToolOutcomeStatus.FAILED
        assert first.error_code == "tool_error"
        assert second.status is ToolOutcomeStatus.CIRCUIT_OPEN
        assert second.error_code == "circuit_open"
        assert first.status is not second.status

    async def test_the_open_breaker_never_reaches_the_tool(
        self, boundary_dependencies: dict[str, Any]
    ) -> None:
        attempts: list[str] = []

        async def angry(args: EchoInput, context: ToolCallContext) -> Mapping[str, Any]:
            attempts.append("call")
            raise RuntimeError("upstream is down")

        boundary = build(
            boundary_dependencies,
            name=ANGRY_TOOL,
            handler=angry,
            breakers=BreakerRegistry(lambda: CircuitBreaker(failure_threshold=1)),
        )

        await boundary.invoke(**invoke_kwargs(tool_name=ANGRY_TOOL))
        await boundary.invoke(
            **invoke_kwargs(tool_name=ANGRY_TOOL, idempotency_key="second")
        )

        assert attempts == ["call"]

    async def test_a_response_the_schema_rejects_is_not_a_tool_error(
        self, boundary_dependencies: dict[str, Any]
    ) -> None:
        """Schema drift and a poisoned response are the tool being wrong."""

        async def liar(args: EchoInput, context: ToolCallContext) -> Mapping[str, Any]:
            return {"unexpected": "shape"}

        boundary = build(boundary_dependencies, name=LIAR_TOOL, handler=liar)

        outcome = await boundary.invoke(**invoke_kwargs(tool_name=LIAR_TOOL))

        assert outcome.status is ToolOutcomeStatus.INVALID_OUTPUT
        assert outcome.retry is RetryDisposition.TERMINAL

    async def test_arguments_the_schema_rejects_are_recorded_not_raised(
        self, boundary: ToolBoundary, audit_store: RecordingAuditStore
    ) -> None:
        outcome = await boundary.invoke(**invoke_kwargs(arguments={"wrong": 1}))

        assert outcome.status is ToolOutcomeStatus.INVALID_INPUT
        assert len(audit_store.invocations) == 1

    async def test_every_failure_state_is_distinguishable(self) -> None:
        """No two failure states share an error code."""

        from src.core.tools.outcome import ERROR_CODES

        assert len(set(ERROR_CODES.values())) == len(ERROR_CODES)
        assert ToolOutcomeStatus.SUCCEEDED not in ERROR_CODES


class TestNoFailureCarriesAResult:
    @pytest.mark.parametrize(
        ("tool", "handler_name"),
        [(SLOW_TOOL, "sleeper"), (ANGRY_TOOL, "angry"), (LIAR_TOOL, "liar")],
    )
    async def test_a_failed_call_has_no_output_to_mistake_for_one(
        self, boundary_dependencies: dict[str, Any], tool: str, handler_name: str
    ) -> None:
        async def sleeper(
            args: EchoInput, context: ToolCallContext
        ) -> Mapping[str, Any]:
            await asyncio.sleep(30)
            return {"echoed": "never"}

        async def angry(args: EchoInput, context: ToolCallContext) -> Mapping[str, Any]:
            raise RuntimeError("upstream is down")

        async def liar(args: EchoInput, context: ToolCallContext) -> Mapping[str, Any]:
            return {"unexpected": "shape"}

        handlers = {"sleeper": sleeper, "angry": angry, "liar": liar}
        boundary = build(
            boundary_dependencies,
            name=tool,
            handler=handlers[handler_name],
            timeout_seconds=0.01,
        )

        outcome = await boundary.invoke(**invoke_kwargs(tool_name=tool))

        assert not outcome.succeeded
        assert outcome.invocation.output is None
        assert outcome.invocation.output_trust is None
        assert outcome.error_code is not None
        with pytest.raises(ToolOutcomeNotSuccessfulError):
            outcome.unwrap()


class TestASuccessfulOutcomeCannotBeManufactured:
    async def test_minting_one_outside_the_boundary_is_refused(
        self, boundary: ToolBoundary
    ) -> None:
        """Packet 4D migrates callers onto this boundary; none may fabricate.

        The record comes from a genuine successful call, so the only thing
        wrong with the attempt is *who is making it*. Without the mint guard, a
        migrated caller could wrap a fabricated fallback in a successful-looking
        outcome — the defect 4-Char found on the live path, relocated one layer
        up.
        """

        from src.core.tools import ToolBoundaryError, ToolOutcome

        real = await boundary.invoke(**invoke_kwargs())

        with pytest.raises(ToolBoundaryError, match="minted by the tool boundary"):
            ToolOutcome(
                mint=object(),
                status=ToolOutcomeStatus.SUCCEEDED,
                invocation=real.invocation,
                retry=RetryDisposition.TERMINAL,
            )

    async def test_a_failure_cannot_be_dressed_as_a_success(
        self, boundary: ToolBoundary
    ) -> None:
        """Even holding the token, the status and the record must agree."""

        from src.core.tools import ToolBoundaryError, ToolOutcome
        from src.core.tools.outcome import _MINT

        denied = await boundary.invoke(**invoke_kwargs(grants=[]))

        with pytest.raises(ToolBoundaryError, match="must be recorded as"):
            ToolOutcome(
                mint=_MINT,
                status=ToolOutcomeStatus.SUCCEEDED,
                invocation=denied.invocation,
                retry=RetryDisposition.TERMINAL,
            )

    async def test_a_real_success_carries_output_and_a_trust_label(
        self, boundary: ToolBoundary
    ) -> None:
        outcome = await boundary.invoke(**invoke_kwargs())

        assert outcome.succeeded
        assert outcome.unwrap() == {"echoed": "hello"}
        assert outcome.invocation.output_trust is TrustClassification.DERIVED_UNTRUSTED
        assert outcome.error_code is None
