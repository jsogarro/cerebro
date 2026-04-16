"""
Rate limiting for Gemini API calls.

Implements token bucket algorithm for rate limiting following functional principles.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any


class RateLimiter:
    """
    Rate limiter using token bucket algorithm.

    This class manages rate limiting state but exposes functional interfaces.
    """

    def __init__(
        self,
        rate_limit: int = 10,
        rate_period: int = 60,
        max_concurrent: int = 5,
    ):
        """
        Initialize rate limiter.

        Args:
            rate_limit: Maximum requests per period
            rate_period: Period in seconds
            max_concurrent: Maximum concurrent requests
        """
        self.rate_limit = rate_limit
        self.rate_period = rate_period
        self.max_concurrent = max_concurrent

        # Token bucket state
        self._tokens: float = float(rate_limit)
        self._last_refill = time.time()
        self._lock = asyncio.Lock()

        # Semaphore for concurrent requests
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def _calculate_tokens_to_add(self, current_time: float) -> float:
        """
        Calculate tokens to add based on elapsed time.

        Pure function that calculates token refill.
        """
        time_elapsed = current_time - self._last_refill
        refill_rate = self.rate_limit / self.rate_period
        return time_elapsed * refill_rate

    async def _refill_tokens(self) -> None:
        """
        Refill tokens based on elapsed time.

        Side effect: Updates token state.
        """
        current_time = time.time()
        tokens_to_add = self._calculate_tokens_to_add(current_time)

        self._tokens = min(float(self.rate_limit), self._tokens + tokens_to_add)
        self._last_refill = current_time

    async def _acquire_token(self) -> None:
        """
        Acquire a token, waiting if necessary.

        Side effect: Updates token state and may wait.
        """
        async with self._lock:
            while True:
                await self._refill_tokens()

                if self._tokens >= 1:
                    self._tokens -= 1
                    return

                # Calculate wait time
                tokens_needed = 1 - self._tokens
                refill_rate = self.rate_limit / self.rate_period
                wait_time = tokens_needed / refill_rate

                # Wait for tokens
                await asyncio.sleep(wait_time)

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        """
        Context manager for rate-limited operations.

        Ensures both rate limiting and concurrency control.
        """
        # Acquire semaphore for concurrency control
        async with self._semaphore:
            # Acquire token for rate limiting
            await self._acquire_token()

            try:
                yield
            finally:
                # Token is already consumed, nothing to release
                pass

    def get_current_tokens(self) -> float:
        """
        Get current number of available tokens.

        Pure function that calculates current tokens.
        """
        current_time = time.time()
        tokens_to_add = self._calculate_tokens_to_add(current_time)
        return min(self.rate_limit, self._tokens + tokens_to_add)

    def get_wait_time(self) -> float:
        """
        Get estimated wait time for next available token.

        Pure function that calculates wait time.
        """
        current_tokens = self.get_current_tokens()

        if current_tokens >= 1:
            return 0.0

        tokens_needed = 1 - current_tokens
        refill_rate = self.rate_limit / self.rate_period
        return tokens_needed / refill_rate


class CircuitBreaker:
    """
    Circuit breaker for handling API failures.

    Implements circuit breaker pattern with three states:
    - Closed: Normal operation
    - Open: Failures exceeded threshold, blocking requests
    - Half-Open: Testing if service recovered
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type[BaseException] = Exception,
    ):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of failures before opening
            recovery_timeout: Seconds before attempting recovery
            expected_exception: Exception type to track
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        # State management
        self._failure_count = 0
        self._last_failure_time: float | None = None
        self._state = "closed"  # closed, open, half-open
        self._lock = asyncio.Lock()

    def _should_attempt_reset(self) -> bool:
        """
        Check if circuit breaker should attempt reset.

        Pure function that determines reset eligibility.
        """
        if self._state != "open":
            return False

        if self._last_failure_time is None:
            return False

        return (time.time() - self._last_failure_time) >= self.recovery_timeout

    async def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """
        Call function with circuit breaker protection.

        Args:
            func: Async function to call
            *args: Function arguments
            **kwargs: Function keyword arguments

        Returns:
            Function result

        Raises:
            Exception: If circuit is open or function fails
        """
        async with self._lock:
            # Check if circuit should attempt reset
            if self._should_attempt_reset():
                self._state = "half-open"

            # Check circuit state
            if self._state == "open":
                raise Exception("Circuit breaker is open")

        try:
            # Attempt the call
            result = await func(*args, **kwargs)

            # Success - reset on half-open
            async with self._lock:
                if self._state == "half-open":
                    self._state = "closed"
                    self._failure_count = 0

            return result

        except self.expected_exception as e:
            # Handle failure
            async with self._lock:
                self._failure_count += 1
                self._last_failure_time = time.time()

                if self._failure_count >= self.failure_threshold:
                    self._state = "open"

            raise e

    def get_state(self) -> str:
        """Get current circuit breaker state."""
        return self._state

    def reset(self) -> None:
        """
        Reset circuit breaker to closed state.

        Side effect: Resets internal state.
        """
        self._state = "closed"
        self._failure_count = 0
        self._last_failure_time = None
