"""A circuit breaker whose open state is visible rather than absorbed.

Packet 4-Char's finding is the design constraint: in the existing integration
the breaker compounds with a fallback, so "one call was flaky" and "this
dependency has been down for ten minutes" reach the caller as the same
success-shaped value. The breaker itself was not the defect — hiding its state
was.

So this breaker never produces a result. It answers one question — may this
call be attempted — and the boundary turns a refusal into a distinct
``CIRCUIT_OPEN`` outcome with its own error code and its own retry
disposition. Nothing here substitutes a value for a call it declined to make.

The state machine is the ordinary three-state one. ``HALF_OPEN`` admits exactly
one trial call: without that limit, every caller waiting on a recovering
dependency stampedes it at the instant the cooldown expires.

Time is supplied by the boundary's clock rather than read here, for the same
reason packet 4A made ``now`` a parameter of the capability decision — a
breaker that reads the wall clock cannot be replayed or tested deterministically.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, final


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


DEFAULT_FAILURE_THRESHOLD: Final[int] = 5
DEFAULT_COOLDOWN_SECONDS: Final[float] = 30.0


@final
class CircuitBreaker:
    """Per-tool failure accounting for one process.

    Scope is deliberately stated rather than implied: this is process-local.
    It bounds the damage one worker does to a failing dependency; it is not a
    cluster-wide judgment about that dependency's health, and nothing here
    should be read as one.
    """

    __slots__ = (
        "_consecutive_failures",
        "_cooldown",
        "_half_open_in_flight",
        "_opened_at",
        "_state",
        "_threshold",
    )

    def __init__(
        self,
        *,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be positive")
        self._threshold = failure_threshold
        self._cooldown = timedelta(seconds=cooldown_seconds)
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: datetime | None = None
        self._half_open_in_flight = False

    @property
    def state(self) -> BreakerState:
        return self._state

    def allows(self, now: datetime) -> bool:
        """Return whether a call may be attempted at ``now``.

        Consumes the single half-open trial slot when it grants one, so a burst
        of callers arriving at the end of a cooldown sends one probe rather
        than all of them.
        """

        if self._state is BreakerState.CLOSED:
            return True
        if self._state is BreakerState.HALF_OPEN:
            if self._half_open_in_flight:
                return False
            self._half_open_in_flight = True
            return True
        if self._opened_at is not None and now - self._opened_at >= self._cooldown:
            self._state = BreakerState.HALF_OPEN
            self._half_open_in_flight = True
            return True
        return False

    def record_success(self) -> None:
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = None
        self._half_open_in_flight = False

    def record_abandoned(self) -> None:
        """Give back the trial slot for a probe that was withdrawn.

        Cancellation is silent about the dependency's health, so this must not
        count as a failure — but it is conclusive about *occupancy*: the probe
        is over, and the slot it consumed has to go back or no later caller can
        ever take it.

        Keeping health and occupancy separate is the whole point. Routing a
        cancelled probe through :meth:`record_failure` would reopen the breaker
        on a request the caller merely withdrew, and routing it through
        :meth:`record_success` would close a breaker on no evidence at all.
        """

        self._half_open_in_flight = False

    def record_failure(self, now: datetime) -> None:
        """Count a failure, opening once the threshold is reached.

        A failure during the half-open trial reopens immediately rather than
        incrementing toward the threshold again: the probe is the evidence, and
        making callers re-earn the threshold would hammer a dependency that has
        just demonstrated it is still down.
        """

        self._half_open_in_flight = False
        if self._state is BreakerState.HALF_OPEN:
            self._open(now)
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold:
            self._open(now)

    def _open(self, now: datetime) -> None:
        self._state = BreakerState.OPEN
        self._opened_at = now
        self._consecutive_failures = self._threshold


@final
class BreakerRegistry:
    """One breaker per tool, created on first use."""

    __slots__ = ("_breakers", "_factory")

    def __init__(self, factory: Callable[[], CircuitBreaker] | None = None) -> None:
        self._factory = factory if factory is not None else CircuitBreaker
        self._breakers: dict[str, CircuitBreaker] = {}

    def for_tool(self, tool_name: str) -> CircuitBreaker:
        breaker = self._breakers.get(tool_name)
        if breaker is None:
            breaker = self._factory()
            self._breakers[tool_name] = breaker
        return breaker


__all__ = [
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_FAILURE_THRESHOLD",
    "BreakerRegistry",
    "BreakerState",
    "CircuitBreaker",
]
