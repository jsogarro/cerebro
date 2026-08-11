"""Recovery and retry classification, which decide what happens next.

The breaker's *open* state is tested elsewhere as an observable outcome. What
is tested here is the part a caller cannot see from one call: that a recovering
dependency is probed by one request rather than by all of them, that a failed
probe reopens immediately instead of granting the threshold again, and that an
unclassified error is terminal by default.

The default matters more than it looks. Classifying an unknown error as
retriable is how a call with side effects gets duplicated, so a tool that knows
an error is transient has to say so rather than benefit from an assumption.
"""

import asyncio
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from src.core.contracts.capabilities import SensitivityClass
from src.core.tools import (
    BreakerRegistry,
    BreakerState,
    CancellationToken,
    CircuitBreaker,
    RetryDisposition,
    ToolBoundary,
    ToolCallContext,
    ToolOutcomeStatus,
    ToolSpec,
)

from .conftest import NOW, EchoInput, EchoOutput, invoke_kwargs


class TestTheBreakerOpensAndRecovers:
    def test_it_stays_closed_below_the_threshold(self) -> None:
        breaker = CircuitBreaker(failure_threshold=3)

        breaker.record_failure(NOW)
        breaker.record_failure(NOW)

        assert breaker.state is BreakerState.CLOSED
        assert breaker.allows(NOW)

    def test_it_opens_at_the_threshold(self) -> None:
        breaker = CircuitBreaker(failure_threshold=2)

        breaker.record_failure(NOW)
        breaker.record_failure(NOW)

        assert breaker.state is BreakerState.OPEN
        assert not breaker.allows(NOW)

    def test_a_success_resets_the_count(self) -> None:
        breaker = CircuitBreaker(failure_threshold=2)

        breaker.record_failure(NOW)
        breaker.record_success()
        breaker.record_failure(NOW)

        assert breaker.state is BreakerState.CLOSED

    def test_the_cooldown_admits_exactly_one_probe(self) -> None:
        """Otherwise every waiting caller stampedes a recovering dependency."""

        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30)
        breaker.record_failure(NOW)
        later = NOW + timedelta(seconds=31)

        assert breaker.allows(later)
        assert breaker.state is BreakerState.HALF_OPEN
        assert not breaker.allows(later)
        assert not breaker.allows(later)

    def test_a_failed_probe_reopens_without_re_earning_the_threshold(self) -> None:
        breaker = CircuitBreaker(failure_threshold=5, cooldown_seconds=30)
        for _ in range(5):
            breaker.record_failure(NOW)
        later = NOW + timedelta(seconds=31)
        breaker.allows(later)

        breaker.record_failure(later)

        assert breaker.state is BreakerState.OPEN
        assert not breaker.allows(later)

    def test_a_successful_probe_closes_it(self) -> None:
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30)
        breaker.record_failure(NOW)
        later = NOW + timedelta(seconds=31)
        breaker.allows(later)

        breaker.record_success()

        assert breaker.state is BreakerState.CLOSED
        assert breaker.allows(later)

    def test_one_tools_outage_does_not_break_another(self) -> None:
        registry = BreakerRegistry(lambda: CircuitBreaker(failure_threshold=1))

        registry.for_tool("flaky").record_failure(NOW)

        assert registry.for_tool("flaky").state is BreakerState.OPEN
        assert registry.for_tool("healthy").state is BreakerState.CLOSED


class TestRetryClassification:
    async def test_an_unclassified_error_is_terminal(
        self, boundary_dependencies: dict[str, Any]
    ) -> None:
        async def angry(args: Any, context: ToolCallContext) -> Mapping[str, Any]:
            raise RuntimeError("who knows")

        boundary = _register(boundary_dependencies, handler=angry)

        outcome = await boundary.invoke(**invoke_kwargs(tool_name="classified"))

        assert outcome.status is ToolOutcomeStatus.FAILED
        assert outcome.retry is RetryDisposition.TERMINAL

    async def test_a_tool_may_declare_an_error_transient(
        self, boundary_dependencies: dict[str, Any]
    ) -> None:
        class TransientError(RuntimeError):
            pass

        async def angry(args: Any, context: ToolCallContext) -> Mapping[str, Any]:
            raise TransientError("try again")

        boundary = _register(
            boundary_dependencies,
            handler=angry,
            classify_error=lambda error: (
                RetryDisposition.RETRIABLE
                if isinstance(error, TransientError)
                else RetryDisposition.TERMINAL
            ),
        )

        outcome = await boundary.invoke(**invoke_kwargs(tool_name="classified"))

        assert outcome.status is ToolOutcomeStatus.FAILED
        assert outcome.retry is RetryDisposition.RETRIABLE

    async def test_the_classifier_does_not_apply_to_a_denial(
        self, boundary_dependencies: dict[str, Any]
    ) -> None:
        """A denial is a decision. Re-asking gets the same answer."""

        async def angry(args: Any, context: ToolCallContext) -> Mapping[str, Any]:
            raise RuntimeError("never reached")

        boundary = _register(
            boundary_dependencies,
            handler=angry,
            classify_error=lambda error: RetryDisposition.RETRIABLE,
        )

        outcome = await boundary.invoke(
            **invoke_kwargs(tool_name="classified", grants=[])
        )

        assert outcome.status is ToolOutcomeStatus.DENIED
        assert outcome.retry is RetryDisposition.TERMINAL


class TestAWithdrawnProbeReleasesTheTrialSlot:
    """A cancelled half-open trial must not strand the breaker.

    ``allows`` consumes the single trial slot when it grants one, and only
    ``record_success`` and ``record_failure`` give it back. Cancellation
    deliberately calls neither — a withdrawn request says nothing about the
    dependency's health — so before this was fixed the slot was consumed and
    never returned, and every later call to that tool was refused for the
    lifetime of the process.

    The distinction the breaker has to keep is between *health* and
    *occupancy*. A cancelled probe is silent about health, which is why it must
    not count as a failure; it is conclusive about occupancy, because the probe
    is over.
    """

    def test_an_abandoned_probe_does_not_count_as_a_failure(self) -> None:
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30)
        breaker.record_failure(NOW)
        later = NOW + timedelta(seconds=31)
        assert breaker.allows(later)

        breaker.record_abandoned()

        assert breaker.state is BreakerState.HALF_OPEN

    def test_the_next_caller_gets_the_slot_the_abandoned_probe_gave_up(
        self,
    ) -> None:
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30)
        breaker.record_failure(NOW)
        later = NOW + timedelta(seconds=31)
        breaker.allows(later)

        breaker.record_abandoned()

        assert breaker.allows(later), (
            "the trial slot was consumed by a probe that was withdrawn; "
            "without releasing it the breaker refuses every later call"
        )

    async def test_a_cancelled_trial_does_not_wedge_the_boundary(
        self, boundary_dependencies: dict[str, Any]
    ) -> None:
        """The guarantee as a caller sees it, not as the breaker records it."""

        started = asyncio.Event()
        fail = True

        async def flaky(args: Any, context: ToolCallContext) -> Mapping[str, Any]:
            if fail:
                raise RuntimeError("upstream is down")
            started.set()
            await asyncio.sleep(30)
            return {"echoed": "never"}

        clock: Any = boundary_dependencies["clock"]
        boundary = ToolBoundary(
            **boundary_dependencies,
            breakers=BreakerRegistry(
                lambda: CircuitBreaker(failure_threshold=1, cooldown_seconds=30)
            ),
        )
        boundary.register(
            ToolSpec(
                name="classified",
                version="1.0.0",
                sensitivity=SensitivityClass.READ_ONLY,
                input_model=EchoInput,
                output_model=EchoOutput,
                timeout_seconds=60.0,
                handler=flaky,
            )
        )

        first = await boundary.invoke(
            **invoke_kwargs(tool_name="classified", idempotency_key="k1")
        )
        assert first.status is ToolOutcomeStatus.FAILED

        # Past the cooldown: the next call is the single half-open probe.
        clock.now = NOW + timedelta(seconds=31)
        fail = False
        token = CancellationToken()
        probe = asyncio.ensure_future(
            boundary.invoke(
                **invoke_kwargs(
                    tool_name="classified", cancellation=token, idempotency_key="k2"
                )
            )
        )
        await started.wait()
        token.cancel()
        assert (await probe).status is ToolOutcomeStatus.CANCELLED

        after = await boundary.invoke(
            **invoke_kwargs(tool_name="classified", idempotency_key="k3")
        )

        assert after.status is not ToolOutcomeStatus.CIRCUIT_OPEN, (
            "the probe was withdrawn, not failed; the breaker must still admit "
            "a later caller instead of refusing every call for the lifetime "
            "of the process"
        )


def _register(
    boundary_dependencies: dict[str, Any],
    *,
    handler: Any,
    classify_error: Any = None,
) -> ToolBoundary:
    boundary = ToolBoundary(**boundary_dependencies)
    boundary.register(
        ToolSpec(
            name="classified",
            version="1.0.0",
            sensitivity=SensitivityClass.READ_ONLY,
            input_model=EchoInput,
            output_model=EchoOutput,
            timeout_seconds=1.0,
            handler=handler,
            classify_error=classify_error,
        )
    )
    return boundary
