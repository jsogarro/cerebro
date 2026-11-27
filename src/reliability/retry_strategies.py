"""Circuit breaker used by the MASR router."""

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, TypeVar

from structlog import get_logger

logger = get_logger()

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration."""

    failure_threshold: int = 5
    success_threshold: int = 2
    timeout: float = 60.0
    half_open_requests: int = 1
    exclude_exceptions: tuple[type[Exception], ...] = ()


@dataclass
class CircuitBreakerMetrics:
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    state_transitions: list[tuple[Any, Any, datetime]] = field(default_factory=list)
    last_failure_time: datetime | None = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            "total_calls": self.total_calls,
            "successful_calls": self.successful_calls,
            "failed_calls": self.failed_calls,
            "rejected_calls": self.rejected_calls,
            "failure_rate": self.failed_calls / max(self.total_calls, 1),
            "state_transitions": len(self.state_transitions),
            "consecutive_failures": self.consecutive_failures,
            "consecutive_successes": self.consecutive_successes,
        }


class CircuitBreaker:
    """
    Circuit breaker implementation.

    Prevents cascading failures by temporarily blocking calls to a failing service.
    """

    def __init__(self, name: str, config: CircuitBreakerConfig | None = None):
        """
        Initialize circuit breaker.

        Args:
            name: Circuit breaker name
            config: Circuit breaker configuration
        """
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._metrics = CircuitBreakerMetrics()
        self._lock = asyncio.Lock()
        self._half_open_counter = 0
        self._state_changed_at = datetime.now(UTC)

    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        return self._state

    async def _transition_to(self, new_state: CircuitState) -> None:
        """
        Transition to a new state.

        Args:
            new_state: New circuit state
        """
        if self._state != new_state:
            old_state = self._state
            self._state = new_state
            self._state_changed_at = datetime.now(UTC)
            self._metrics.state_transitions.append(
                (old_state, new_state, datetime.now(UTC))
            )

            if new_state == CircuitState.HALF_OPEN:
                self._half_open_counter = 0

            logger.info(
                "circuit_breaker_state_transitioned",
                circuit_breaker=self.name,
                old_state=old_state.value,
                new_state=new_state.value,
            )

    async def _should_attempt_reset(self) -> bool:
        """Check if circuit should attempt reset."""
        return (
            self._state == CircuitState.OPEN
            and (datetime.now(UTC) - self._state_changed_at).total_seconds()
            >= self.config.timeout
        )

    async def call(
        self, func: Callable[..., T | Awaitable[T]], *args: Any, **kwargs: Any
    ) -> T:
        """
        Execute function through circuit breaker.

        Args:
            func: Function to execute
            *args: Function arguments
            **kwargs: Function keyword arguments

        Returns:
            Function result

        Raises:
            Exception: If circuit is open or function fails
        """
        probe = False
        async with self._lock:
            if await self._should_attempt_reset():
                await self._transition_to(CircuitState.HALF_OPEN)

            # Reject if circuit is OPEN
            if self._state == CircuitState.OPEN:
                self._metrics.rejected_calls += 1
                raise Exception(f"Circuit breaker '{self.name}' is OPEN")

            # Limit requests in HALF_OPEN state
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_counter >= self.config.half_open_requests:
                    self._metrics.rejected_calls += 1
                    raise Exception(
                        f"Circuit breaker '{self.name}' is HALF_OPEN (limit reached)"
                    )
                self._half_open_counter += 1
                probe = True

        # Execute the function
        self._metrics.total_calls += 1

        try:
            # Handle both sync and async functions
            outcome = func(*args, **kwargs)
            result: T = await outcome if inspect.isawaitable(outcome) else outcome

            # Record success
            await self._on_success()
            return result

        except Exception as e:
            if not isinstance(e, self.config.exclude_exceptions):
                await self._on_failure()
            raise
        finally:
            # The probe slot is only held while the probe is in flight; release
            # it on success, failure and cancellation so HALF_OPEN cannot wedge.
            if probe:
                async with self._lock:
                    if self._state == CircuitState.HALF_OPEN:
                        self._half_open_counter = max(0, self._half_open_counter - 1)

    async def _on_success(self) -> None:
        """Handle successful call."""
        async with self._lock:
            self._metrics.successful_calls += 1
            self._metrics.consecutive_successes += 1
            self._metrics.consecutive_failures = 0

            if (
                self._state == CircuitState.HALF_OPEN
                and self._metrics.consecutive_successes >= self.config.success_threshold
            ):
                await self._transition_to(CircuitState.CLOSED)

    async def _on_failure(self) -> None:
        """Handle failed call."""
        async with self._lock:
            self._metrics.failed_calls += 1
            self._metrics.consecutive_failures += 1
            self._metrics.consecutive_successes = 0
            self._metrics.last_failure_time = datetime.now(UTC)

            if self._state == CircuitState.CLOSED:
                if self._metrics.consecutive_failures >= self.config.failure_threshold:
                    await self._transition_to(CircuitState.OPEN)
            elif self._state == CircuitState.HALF_OPEN:
                await self._transition_to(CircuitState.OPEN)

    def get_metrics(self) -> CircuitBreakerMetrics:
        """Get circuit breaker metrics."""
        return self._metrics

    async def reset(self) -> None:
        """Manually reset circuit breaker."""
        async with self._lock:
            await self._transition_to(CircuitState.CLOSED)
            self._metrics.consecutive_failures = 0
            self._metrics.consecutive_successes = 0
