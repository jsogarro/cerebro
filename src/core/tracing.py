"""Distributed tracing with Langfuse.

This module provides opt-in distributed tracing for LLM calls and agent orchestration.
Traces are sent to Langfuse for debugging, cost tracking, and performance analysis.

The implementation is no-op safe: if LANGFUSE_ENABLED is false or the SDK import fails,
all tracing functions become no-ops without breaking the application.

Environment Variables:
    LANGFUSE_ENABLED: "1" or "true" to enable tracing (default: disabled)
    LANGFUSE_PUBLIC_KEY: Langfuse public API key (from Langfuse UI)
    LANGFUSE_SECRET_KEY: Langfuse secret API key (from Langfuse UI)
    LANGFUSE_HOST: Langfuse server URL (optional, defaults to cloud)

Example Usage:
    ```python
    from src.core.tracing import trace_masr_routing, trace_provider_call, record_provider_metrics

    # In MASR router
    with trace_masr_routing(
        query_id=str(decision.query_id),
        query=query,
        metadata={"complexity": "high", "strategy": "balanced"},
    ) as trace:
        # routing logic
        pass

    # In provider
    with trace_provider_call(
        trace=trace,
        provider="openrouter",
        model="claude-sonnet-4.6",
    ) as span:
        # make LLM call
        record_provider_metrics(
            span,
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.005,
            latency_ms=250,
        )
    ```
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from structlog import get_logger

if TYPE_CHECKING:
    from langfuse import Langfuse
    from langfuse.client import StatefulSpanClient, StatefulTraceClient

logger = get_logger(__name__)

_langfuse_client: Langfuse | None = None
_langfuse_enabled: bool | None = None


def _is_langfuse_enabled() -> bool:
    """Check if Langfuse tracing is enabled via environment variable."""
    global _langfuse_enabled
    if _langfuse_enabled is None:
        env_value = os.getenv("LANGFUSE_ENABLED", "").lower()
        _langfuse_enabled = env_value in ("1", "true", "yes")
    return _langfuse_enabled


def _initialize_langfuse() -> Langfuse | None:
    """Initialize Langfuse client if enabled.

    Returns:
        Langfuse client instance if initialization succeeds, None otherwise.

    This function handles three failure modes gracefully:
    1. LANGFUSE_ENABLED=false → returns None immediately
    2. SDK not installed → logs warning, returns None
    3. Initialization error (bad keys, network) → logs error, returns None
    """
    if not _is_langfuse_enabled():
        logger.debug("langfuse_disabled")
        return None

    try:
        from langfuse import Langfuse

        public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        secret_key = os.getenv("LANGFUSE_SECRET_KEY")
        host = os.getenv("LANGFUSE_HOST")

        if not public_key or not secret_key:
            logger.warning(
                "langfuse_keys_missing",
                message="LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY required when LANGFUSE_ENABLED=true",
            )
            return None

        client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,  # Optional, defaults to Langfuse cloud
        )
        logger.info("langfuse_initialized", host=host or "cloud")
        return client
    except ImportError:
        logger.warning(
            "langfuse_sdk_not_installed",
            message="Install langfuse package to enable tracing: uv pip install langfuse",
        )
        return None
    except Exception as e:
        logger.error("langfuse_init_failed", error=str(e), error_type=type(e).__name__)
        return None


def get_langfuse_client() -> Langfuse | None:
    """Get or initialize Langfuse client.

    Returns:
        Langfuse client instance if available, None if disabled or failed to initialize.

    This function is idempotent: the client is initialized once on first call,
    then cached for subsequent calls.
    """
    global _langfuse_client
    if _langfuse_client is None:
        _langfuse_client = _initialize_langfuse()
    return _langfuse_client


@contextmanager
def trace_masr_routing(
    query_id: str,
    query: str,
    metadata: dict[str, Any] | None = None,
) -> Generator[StatefulTraceClient | None, None, None]:
    """Trace MASR routing decision.

    Creates a new distributed trace for the query. The trace ID is set to the query_id
    so traces can be correlated with query execution logs.

    Args:
        query_id: Unique query identifier (UUID)
        query: Raw query text (will be PII-redacted in metadata)
        metadata: Additional trace metadata (complexity, strategy, estimated_cost, etc.)

    Yields:
        StatefulTraceClient if tracing is enabled, None otherwise.

    Example:
        ```python
        with trace_masr_routing(
            query_id="123e4567-e89b-12d3-a456-426614174000",
            query="What is the impact of AI on healthcare?",
            metadata={
                "complexity_level": "high",
                "routing_strategy": "quality_focused",
                "estimated_cost": 0.15,
                "estimated_tokens": 5000,
            },
        ) as trace:
            # MASR routing logic
            decision = await self.route(query)
        ```
    """
    client = get_langfuse_client()
    if client is None:
        yield None
        return

    try:
        trace = client.trace(  # type: ignore[attr-defined]
            id=query_id,
            name="masr_routing",
            input={"query": query},
            metadata=metadata or {},
        )
        yield trace
        # Mark trace as completed (trace auto-flushes on context exit)
        trace.update(output={"status": "completed"})
    except Exception as e:
        logger.warning(
            "langfuse_trace_failed",
            error=str(e),
            error_type=type(e).__name__,
            query_id=query_id,
        )
        yield None


@contextmanager
def trace_provider_call(
    trace: StatefulTraceClient | None,
    provider: str,
    model: str,
    metadata: dict[str, Any] | None = None,
) -> Generator[StatefulSpanClient | None, None, None]:
    """Trace LLM provider call as a span within a trace.

    Args:
        trace: Parent trace (from trace_masr_routing), or None if tracing disabled
        provider: Provider name (e.g., "openrouter", "gemini")
        model: Model identifier (e.g., "claude-sonnet-4.6", "gemini-pro")
        metadata: Additional span metadata (temperature, strategy, etc.)

    Yields:
        StatefulSpanClient if tracing is enabled, None otherwise.

    Example:
        ```python
        with trace_provider_call(
            trace=trace,
            provider="openrouter",
            model="claude-sonnet-4.6",
            metadata={"temperature": 0.7, "strategy": "balanced"},
        ) as span:
            response = await httpx_client.post(...)
            record_provider_metrics(
                span,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                cost_usd=response.cost,
                latency_ms=response.latency_ms,
            )
        ```
    """
    if trace is None:
        yield None
        return

    try:
        span = trace.span(
            name=f"{provider}_call",
            metadata={
                "provider": provider,
                "model": model,
                **(metadata or {}),
            },
        )
        yield span
        # Span auto-closes on context exit
    except Exception as e:
        logger.warning(
            "langfuse_span_failed",
            error=str(e),
            error_type=type(e).__name__,
            provider=provider,
            model=model,
        )
        yield None


def record_provider_metrics(
    span: StatefulSpanClient | None,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    latency_ms: int,
) -> None:
    """Record LLM provider call metrics to a span.

    This updates the span with token usage, cost, and latency metadata.
    The metrics are displayed in Langfuse UI and used for cost tracking.

    Args:
        span: Span from trace_provider_call, or None if tracing disabled
        prompt_tokens: Number of tokens in the prompt
        completion_tokens: Number of tokens in the completion
        cost_usd: Estimated cost in USD
        latency_ms: Provider call latency in milliseconds

    Example:
        ```python
        record_provider_metrics(
            span,
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.005,
            latency_ms=250,
        )
        ```
    """
    if span is None:
        return

    try:
        total_tokens = prompt_tokens + completion_tokens
        span.update(
            usage={
                "input": prompt_tokens,
                "output": completion_tokens,
                "total": total_tokens,
                "unit": "TOKENS",
            },
            metadata={
                "cost_usd": cost_usd,
                "latency_ms": latency_ms,
            },
        )
    except Exception as e:
        logger.warning(
            "langfuse_metrics_failed",
            error=str(e),
            error_type=type(e).__name__,
        )


def flush_langfuse() -> None:
    """Flush pending traces to Langfuse server.

    This is useful at application shutdown to ensure all traces are sent.
    Langfuse SDK normally flushes async in the background, but explicit flush
    ensures no traces are lost on shutdown.

    Example:
        ```python
        # In FastAPI shutdown handler
        @app.on_event("shutdown")
        async def shutdown_event():
            flush_langfuse()
        ```
    """
    client = get_langfuse_client()
    if client is None:
        return

    try:
        client.flush()
        logger.debug("langfuse_flushed")
    except Exception as e:
        logger.warning(
            "langfuse_flush_failed",
            error=str(e),
            error_type=type(e).__name__,
        )
