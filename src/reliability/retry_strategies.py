"""
Retry strategies and circuit breaker patterns.

This module provides advanced retry mechanisms, circuit breakers,
and bulkhead patterns for fault-tolerant operation execution.
"""

import asyncio
import inspect
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from functools import wraps
from typing import Any, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


class RetryDecision(Enum):
    """Retry decision enumeration."""

    RETRY = "retry"
    FAIL = "fail"
    SUCCESS = "success"


@dataclass
class RetryPolicy:
    """
    Retry policy configuration.

    Attributes:
        max_attempts: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        exponential_base: Base for exponential backoff
        jitter: Whether to add random jitter
        retryable_exceptions: Exceptions that trigger retry
        non_retryable_exceptions: Exceptions that don't trigger retry
    """

    max_attempts: int = 3
    initial_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,)
    non_retryable_exceptions: tuple[type[Exception], ...] = ()
    retry_budget: Optional["RetryBudget"] = None

    def should_retry(self, exception: Exception, attempt: int) -> bool:
        """
        Determine if operation should be retried.

        Args:
            exception: The exception that occurred
            attempt: Current attempt number

        Returns:
            True if should retry, False otherwise
        """
        # Check retry budget if configured
        if self.retry_budget and not self.retry_budget.can_retry():
            logger.warning("Retry budget exhausted")
            return False

        # Check max attempts
        if attempt >= self.max_attempts:
            return False

        # Check non-retryable exceptions first
        if isinstance(exception, self.non_retryable_exceptions):
            return False

        # Check retryable exceptions
        return isinstance(exception, self.retryable_exceptions)

    def get_delay(self, attempt: int) -> float:
        """
        Calculate delay for the given attempt.

        Args:
            attempt: Attempt number (0-indexed)

        Returns:
            Delay in seconds
        """
        # Exponential backoff
        delay = min(
            self.initial_delay * (self.exponential_base**attempt), self.max_delay
        )

        # Add jitter if enabled
        if self.jitter:
            delay = delay * (0.5 + random.random())

        return delay


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
    """Circuit breaker metrics."""

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
        self._state_changed_at = datetime.utcnow()

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
            self._state_changed_at = datetime.utcnow()
            self._metrics.state_transitions.append(
                (old_state, new_state, datetime.utcnow())
            )

            if new_state == CircuitState.HALF_OPEN:
                self._half_open_counter = 0

            logger.info(
                f"Circuit breaker '{self.name}' transitioned from {old_state} to {new_state}"
            )

    async def _should_attempt_reset(self) -> bool:
        """Check if circuit should attempt reset."""
        return (
            self._state == CircuitState.OPEN
            and (datetime.utcnow() - self._state_changed_at).total_seconds()
            >= self.config.timeout
        )

    async def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
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
        async with self._lock:
            # Check if we should transition from OPEN to HALF_OPEN
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

        # Execute the function
        self._metrics.total_calls += 1

        try:
            # Handle both sync and async functions
            result: T
            if inspect.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)

            # Record success
            await self._on_success()
            return result

        except Exception as e:
            # Check if exception should be counted as failure
            if not isinstance(e, self.config.exclude_exceptions):
                await self._on_failure()
            raise

    async def _on_success(self) -> None:
        """Handle successful call."""
        async with self._lock:
            self._metrics.successful_calls += 1
            self._metrics.consecutive_successes += 1
            self._metrics.consecutive_failures = 0

            if self._state == CircuitState.HALF_OPEN:
                if self._metrics.consecutive_successes >= self.config.success_threshold:
                    await self._transition_to(CircuitState.CLOSED)

    async def _on_failure(self) -> None:
        """Handle failed call."""
        async with self._lock:
            self._metrics.failed_calls += 1
            self._metrics.consecutive_failures += 1
            self._metrics.consecutive_successes = 0
            self._metrics.last_failure_time = datetime.utcnow()

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


class ExponentialBackoff:
    """
    Exponential backoff retry strategy.

    Implements exponential backoff with jitter for retry delays.
    """

    def __init__(self, policy: RetryPolicy | None = None):
        """
        Initialize exponential backoff.

        Args:
            policy: Retry policy configuration
        """
        self.policy = policy or RetryPolicy()

    async def execute(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Execute function with exponential backoff retry.

        Args:
            func: Function to execute
            *args: Function arguments
            **kwargs: Function keyword arguments

        Returns:
            Function result

        Raises:
            Exception: If all retries fail
        """
        last_exception = None

        for attempt in range(self.policy.max_attempts):
            try:
                # Execute function
                result: T
                if inspect.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)
                return result

            except Exception as e:
                last_exception = e

                # Check if should retry
                if not self.policy.should_retry(e, attempt + 1):
                    logger.error(f"Non-retryable exception: {e}")
                    raise

                # Calculate delay
                if attempt < self.policy.max_attempts - 1:
                    delay = self.policy.get_delay(attempt)
                    logger.warning(
                        f"Attempt {attempt + 1} failed, retrying in {delay:.2f}s: {e}"
                    )
                    await asyncio.sleep(delay)

                    # Consume retry budget if configured
                    if self.policy.retry_budget:
                        await self.policy.retry_budget.consume()

        # All retries exhausted
        logger.error(f"All {self.policy.max_attempts} attempts failed")
        if last_exception:
            raise last_exception
        raise Exception("All retry attempts failed")


@dataclass
class BulkheadConfig:
    """Bulkhead configuration."""

    max_concurrent: int = 10
    max_queue_size: int = 100
    timeout: float = 30.0


class BulkheadExecutor:
    """
    Bulkhead pattern implementation.

    Isolates resources to prevent failure propagation.
    """

    def __init__(self, name: str, config: BulkheadConfig | None = None):
        """
        Initialize bulkhead executor.

        Args:
            name: Bulkhead name
            config: Bulkhead configuration
        """
        self.name = name
        self.config = config or BulkheadConfig()
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)
        self._queue: asyncio.Queue[bool] = asyncio.Queue(maxsize=self.config.max_queue_size)
        self._active_tasks = 0
        self._total_executed = 0
        self._total_rejected = 0
        self._total_timeout = 0

    async def execute(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Execute function with bulkhead isolation.

        Args:
            func: Function to execute
            *args: Function arguments
            **kwargs: Function keyword arguments

        Returns:
            Function result

        Raises:
            Exception: If queue is full or execution times out
        """
        # Check queue size
        if self._queue.full():
            self._total_rejected += 1
            raise Exception(f"Bulkhead '{self.name}' queue is full")

        # Add to queue
        await self._queue.put(True)

        try:
            # Acquire semaphore
            async with self._semaphore:
                self._active_tasks += 1
                self._total_executed += 1

                try:
                    # Execute with timeout
                    result: T
                    if inspect.iscoroutinefunction(func):
                        result = await asyncio.wait_for(
                            func(*args, **kwargs), timeout=self.config.timeout
                        )
                    else:
                        result = await asyncio.wait_for(
                            asyncio.get_event_loop().run_in_executor(
                                None, func, *args, **kwargs
                            ),
                            timeout=self.config.timeout,
                        )

                    return result

                except TimeoutError:
                    self._total_timeout += 1
                    raise Exception(f"Bulkhead '{self.name}' execution timeout")
                finally:
                    self._active_tasks -= 1
        finally:
            # Remove from queue
            await self._queue.get()

    def get_metrics(self) -> dict[str, Any]:
        """Get bulkhead metrics."""
        return {
            "name": self.name,
            "active_tasks": self._active_tasks,
            "queue_size": self._queue.qsize(),
            "total_executed": self._total_executed,
            "total_rejected": self._total_rejected,
            "total_timeout": self._total_timeout,
        }


class RetryBudget:
    """
    Retry budget to limit total retry attempts.

    Prevents retry storms by limiting the number of retries across all operations.
    """

    def __init__(self, budget_size: int = 100, refill_rate: float = 10.0):
        """
        Initialize retry budget.

        Args:
            budget_size: Maximum budget size
            refill_rate: Tokens refilled per second
        """
        self.budget_size = budget_size
        self.refill_rate = refill_rate
        self._tokens: float = float(budget_size)
        self._last_refill: float = time.time()
        self._lock = asyncio.Lock()

    async def can_retry(self) -> bool:
        """Check if retry is allowed."""
        async with self._lock:
            await self._refill()
            return self._tokens > 0

    async def consume(self, tokens: int = 1) -> bool:
        """
        Consume tokens from budget.

        Args:
            tokens: Number of tokens to consume

        Returns:
            True if tokens were consumed, False if insufficient budget
        """
        async with self._lock:
            await self._refill()

            if self._tokens >= tokens:
                self._tokens -= tokens
                return True

            return False

    async def _refill(self) -> None:
        """Refill tokens based on time elapsed."""
        current_time = time.time()
        time_elapsed = current_time - self._last_refill

        tokens_to_add = time_elapsed * self.refill_rate
        self._tokens = min(self._tokens + tokens_to_add, self.budget_size)
        self._last_refill = current_time

    def get_remaining(self) -> float:
        """Get remaining tokens."""
        return self._tokens


def with_retry(policy: RetryPolicy | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator for adding retry logic to functions.

    Args:
        policy: Retry policy configuration

    Returns:
        Decorated function
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        backoff = ExponentialBackoff(policy)

        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return await backoff.execute(func, *args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(backoff.execute(func, *args, **kwargs))

        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper

    return decorator


def with_circuit_breaker(name: str, config: CircuitBreakerConfig | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator for adding circuit breaker to functions.

    Args:
        name: Circuit breaker name
        config: Circuit breaker configuration

    Returns:
        Decorated function
    """
    circuit_breaker = CircuitBreaker(name, config)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return await circuit_breaker.call(func, *args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(circuit_breaker.call(func, *args, **kwargs))

        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper

    return decorator


def with_bulkhead(name: str, config: BulkheadConfig | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator for adding bulkhead isolation to functions.

    Args:
        name: Bulkhead name
        config: Bulkhead configuration

    Returns:
        Decorated function
    """
    bulkhead = BulkheadExecutor(name, config)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return await bulkhead.execute(func, *args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(bulkhead.execute(func, *args, **kwargs))

        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper

    return decorator


# Global instances for common use cases
default_retry_policy = RetryPolicy()
default_circuit_breaker = CircuitBreaker("default")
default_bulkhead = BulkheadExecutor("default")
global_retry_budget = RetryBudget(budget_size=1000, refill_rate=100)


__all__ = [
    "BulkheadConfig",
    "BulkheadExecutor",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitBreakerMetrics",
    "CircuitState",
    "ExponentialBackoff",
    "RetryBudget",
    "RetryPolicy",
    "default_bulkhead",
    "default_circuit_breaker",
    "default_retry_policy",
    "global_retry_budget",
    "with_bulkhead",
    "with_circuit_breaker",
    "with_retry",
]
