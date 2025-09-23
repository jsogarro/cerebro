"""
Metrics collection for Gemini service.

This module provides Prometheus metrics and monitoring capabilities
for the Gemini API integration.
"""

import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from prometheus_client import Counter, Gauge, Histogram, Summary

# Metrics definitions
gemini_requests_total = Counter(
    "gemini_requests_total", "Total number of Gemini API requests", ["method", "status"]
)

gemini_request_duration = Histogram(
    "gemini_request_duration_seconds",
    "Gemini API request duration in seconds",
    ["method"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

gemini_tokens_used = Counter(
    "gemini_tokens_used_total",
    "Total tokens used in Gemini API calls",
    ["type"],  # input, output
)

gemini_cache_operations = Counter(
    "gemini_cache_operations_total",
    "Cache operations for Gemini responses",
    ["operation", "status"],  # get/set, hit/miss/error
)

gemini_cache_hit_rate = Gauge("gemini_cache_hit_rate", "Cache hit rate percentage")

gemini_rate_limit_remaining = Gauge(
    "gemini_rate_limit_remaining", "Remaining rate limit quota"
)

gemini_concurrent_requests = Gauge(
    "gemini_concurrent_requests", "Number of concurrent Gemini requests"
)

gemini_errors_total = Counter(
    "gemini_errors_total", "Total number of Gemini API errors", ["error_type"]
)

gemini_response_size = Summary(
    "gemini_response_size_bytes", "Size of Gemini API responses in bytes"
)


class GeminiMetrics:
    """
    Metrics collector for Gemini service.

    Provides methods for tracking API usage, performance, and errors.
    """

    def __init__(self):
        """Initialize metrics collector."""
        self.start_times: dict[str, float] = {}

    def record_request_start(self, request_id: str, method: str) -> None:
        """
        Record the start of a request.

        Args:
            request_id: Unique request identifier
            method: API method being called
        """
        self.start_times[request_id] = time.time()
        gemini_concurrent_requests.inc()

    def record_request_end(
        self,
        request_id: str,
        method: str,
        status: str = "success",
        response_size: int | None = None,
    ) -> None:
        """
        Record the end of a request.

        Args:
            request_id: Unique request identifier
            method: API method that was called
            status: Request status (success/error)
            response_size: Size of response in bytes
        """
        # Record request count
        gemini_requests_total.labels(method=method, status=status).inc()

        # Record duration
        if request_id in self.start_times:
            duration = time.time() - self.start_times[request_id]
            gemini_request_duration.labels(method=method).observe(duration)
            del self.start_times[request_id]

        # Record response size
        if response_size:
            gemini_response_size.observe(response_size)

        # Decrement concurrent requests
        gemini_concurrent_requests.dec()

    def record_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """
        Record token usage.

        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
        """
        gemini_tokens_used.labels(type="input").inc(input_tokens)
        gemini_tokens_used.labels(type="output").inc(output_tokens)

    def record_cache_operation(
        self,
        operation: str,
        status: str,
    ) -> None:
        """
        Record cache operation.

        Args:
            operation: Type of operation (get/set/delete)
            status: Operation status (hit/miss/error)
        """
        gemini_cache_operations.labels(operation=operation, status=status).inc()

    def update_cache_hit_rate(self, hit_rate: float) -> None:
        """
        Update cache hit rate gauge.

        Args:
            hit_rate: Hit rate percentage (0-100)
        """
        gemini_cache_hit_rate.set(hit_rate)

    def record_error(self, error_type: str) -> None:
        """
        Record an error.

        Args:
            error_type: Type of error that occurred
        """
        gemini_errors_total.labels(error_type=error_type).inc()

    def update_rate_limit(self, remaining: int) -> None:
        """
        Update remaining rate limit.

        Args:
            remaining: Number of remaining requests
        """
        gemini_rate_limit_remaining.set(remaining)


# Global metrics instance
metrics = GeminiMetrics()


def track_gemini_call(method: str):
    """
    Decorator to track Gemini API calls.

    Args:
        method: Name of the API method

    Returns:
        Decorated function
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            import uuid

            request_id = str(uuid.uuid4())

            # Record request start
            metrics.record_request_start(request_id, method)

            try:
                # Execute function
                result = await func(*args, **kwargs)

                # Record success
                response_size = len(str(result)) if result else 0
                metrics.record_request_end(request_id, method, "success", response_size)

                return result

            except Exception as e:
                # Record error
                metrics.record_request_end(request_id, method, "error")
                metrics.record_error(type(e).__name__)
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            import uuid

            request_id = str(uuid.uuid4())

            # Record request start
            metrics.record_request_start(request_id, method)

            try:
                # Execute function
                result = func(*args, **kwargs)

                # Record success
                response_size = len(str(result)) if result else 0
                metrics.record_request_end(request_id, method, "success", response_size)

                return result

            except Exception as e:
                # Record error
                metrics.record_request_end(request_id, method, "error")
                metrics.record_error(type(e).__name__)
                raise

        # Return appropriate wrapper based on function type
        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


class CircuitBreaker:
    """
    Circuit breaker pattern for Gemini API calls.

    Prevents cascading failures by temporarily blocking calls
    when error rate exceeds threshold.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type = Exception,
    ):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before attempting recovery
            expected_exception: Exception type to catch
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self.failure_count = 0
        self.last_failure_time: float | None = None
        self.state = "closed"  # closed, open, half-open

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Call function with circuit breaker protection.

        Args:
            func: Function to call
            *args: Function arguments
            **kwargs: Function keyword arguments

        Returns:
            Function result

        Raises:
            Exception: If circuit is open or function fails
        """
        if self.state == "open":
            if self._should_attempt_reset():
                self.state = "half-open"
            else:
                raise Exception("Circuit breaker is open")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception:
            self._on_failure()
            raise

    async def async_call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Async version of call.

        Args:
            func: Async function to call
            *args: Function arguments
            **kwargs: Function keyword arguments

        Returns:
            Function result
        """
        if self.state == "open":
            if self._should_attempt_reset():
                self.state = "half-open"
            else:
                raise Exception("Circuit breaker is open")

        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        """Handle successful call."""
        self.failure_count = 0
        self.state = "closed"

    def _on_failure(self) -> None:
        """Handle failed call."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            metrics.record_error("circuit_breaker_open")

    def _should_attempt_reset(self) -> bool:
        """Check if we should attempt to reset the circuit."""
        return (
            self.last_failure_time
            and time.time() - self.last_failure_time >= self.recovery_timeout
        )

    def get_state(self) -> str:
        """Get current circuit breaker state."""
        return self.state

    def reset(self) -> None:
        """Manually reset the circuit breaker."""
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "closed"
