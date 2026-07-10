"""Distributed tracing with Langfuse (v4 SDK).

This module provides opt-in distributed tracing for MASR routing decisions and
LLM provider calls. Traces are sent to Langfuse for debugging, cost tracking,
and performance analysis.

The implementation is no-op safe: if tracing is disabled (via ``Settings``) or
the SDK import/initialization fails, all tracing functions become no-ops that
never raise. Every individual interaction with the Langfuse SDK (client init,
observation creation, ``update``, ``end``) is guarded, so a tracing error can
never escape into — or replace — the caller's control flow.

Configuration (see ``src.core.config.Settings``):
    LANGFUSE_ENABLED: enable tracing (default: False)
    LANGFUSE_PUBLIC_KEY: Langfuse public API key (required when enabled)
    LANGFUSE_SECRET_KEY: Langfuse secret API key (required when enabled)
    LANGFUSE_HOST: Langfuse server URL (optional; defaults to Langfuse cloud)

This targets the Langfuse v4 API. Routing decisions are modelled as ``span``
observations; provider calls as ``generation`` observations (first-class
model/usage/cost fields). Observation handles are threaded from the router to
the provider through call-site state rather than mutating caller data.

Example:
    ```python
    from src.core.tracing import (
        trace_masr_routing,
        trace_provider_call,
        record_provider_metrics,
    )

    with trace_masr_routing(query_id, query, metadata={...}) as routing_obs:
        # routing logic ...
        with trace_provider_call(
            routing_obs, provider="openrouter", model="claude-sonnet-4.6"
        ) as gen_obs:
            # make LLM call ...
            record_provider_metrics(
                gen_obs,
                prompt_tokens=100,
                completion_tokens=50,
                cost_usd=0.005,
                latency_ms=250,
            )
    ```
"""

from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from structlog import get_logger

from src.core.pii_redactor import redact_pii

# NOTE: src.core.config is imported lazily inside the functions below. Importing
# it at module level would instantiate the Settings() singleton at import time,
# and because this module is pulled in via the provider import chain
# (ai_brain/__init__ -> providers -> openrouter_provider -> tracing), that would
# force Settings validation during early startup — before the service env is
# available — crashing containers that set SECRET_KEY only at runtime.

if TYPE_CHECKING:
    from langfuse import Langfuse
    from langfuse._client.span import LangfuseGeneration, LangfuseSpan

logger = get_logger(__name__)

# Cap on the redacted query text stored as trace input. Traces are for
# debugging, not full-fidelity capture; a bounded prefix keeps payloads small.
_MAX_TRACE_INPUT_CHARS = 500

# Lazy singleton client + guard. The lock prevents a double-init race when
# concurrent tasks call get_langfuse_client() before the singleton is set.
_langfuse_client: Langfuse | None = None
_langfuse_lock = threading.Lock()
_langfuse_initialized = False


def _tracing_enabled() -> bool:
    """Return True if Langfuse tracing is enabled via Settings.

    This is the single source of truth for the enabled check; callers (and the
    verify script) MUST route through here rather than parsing env vars.
    """
    try:
        from src.core.config import get_settings

        return bool(get_settings().LANGFUSE_ENABLED)
    except Exception:
        return False


def _initialize_langfuse() -> Langfuse | None:
    """Initialize the Langfuse v4 client if enabled and configured.

    Handles every failure mode gracefully:
    1. tracing disabled -> None
    2. SDK not installed -> logs warning, returns None
    3. keys missing / init error -> logs, returns None
    """
    from src.core.config import get_settings

    settings = get_settings()
    if not settings.LANGFUSE_ENABLED:
        logger.debug("langfuse_disabled")
        return None

    public_key = settings.LANGFUSE_PUBLIC_KEY
    secret_key = settings.LANGFUSE_SECRET_KEY
    host = settings.LANGFUSE_HOST

    if not public_key or not secret_key:
        logger.warning(
            "langfuse_keys_missing",
            message=(
                "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are required "
                "when LANGFUSE_ENABLED is true"
            ),
        )
        return None

    try:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,  # None -> Langfuse cloud default
        )
        logger.info("langfuse_initialized", host=host or "cloud")
        return client
    except ImportError:
        logger.warning(
            "langfuse_sdk_not_installed",
            message="Install langfuse (>=4.13,<5) to enable tracing.",
        )
        return None
    except Exception as e:
        logger.error("langfuse_init_failed", error=str(e), error_type=type(e).__name__)
        return None


def get_langfuse_client() -> Langfuse | None:
    """Get or lazily initialize the Langfuse client (thread-safe singleton).

    Returns None if tracing is disabled or initialization failed. Initialization
    is attempted at most once; the (possibly None) result is cached.
    """
    global _langfuse_client, _langfuse_initialized

    # Fast path: if disabled, never touch the SDK or the lock.
    if not _tracing_enabled():
        return None

    if _langfuse_initialized:
        return _langfuse_client

    with _langfuse_lock:
        if not _langfuse_initialized:
            _langfuse_client = _initialize_langfuse()
            _langfuse_initialized = True
    return _langfuse_client


def reset_langfuse_state() -> None:
    """Reset the cached client + init flag. Intended for tests only."""
    global _langfuse_client, _langfuse_initialized
    with _langfuse_lock:
        _langfuse_client = None
        _langfuse_initialized = False


@contextmanager
def trace_masr_routing(
    query_id: str,
    query: str,
    metadata: dict[str, Any] | None = None,
) -> Generator[LangfuseSpan | None, None, None]:
    """Trace a MASR routing decision as a root ``span`` observation.

    The observation's ``trace_id`` is derived deterministically from ``query_id``
    so traces correlate with query-execution logs. The raw query is NEVER sent:
    a PII-redacted, length-capped prefix is used as the observation input.

    Contract:
    - Yields exactly once. Body exceptions propagate UNCHANGED to the caller;
      tracing never replaces or swallows a real error.
    - Every Langfuse interaction is individually guarded, so an SDK failure can
      never escape. On any tracing failure the block yields None and runs
      normally.
    - The observation is always ended in ``finally``.

    Args:
        query_id: Unique query identifier (UUID string).
        query: Raw query text (redacted + truncated before being sent).
        metadata: Additional observation metadata.

    Yields:
        A LangfuseSpan handle when tracing is active, else None.
    """
    client = get_langfuse_client()
    if client is None:
        yield None
        return

    observation: LangfuseSpan | None = None
    try:
        trace_id = client.create_trace_id(seed=query_id)
        redacted_input = redact_pii(query)[:_MAX_TRACE_INPUT_CHARS]
        observation = client.start_observation(  # type: ignore[assignment]
            trace_context={"trace_id": trace_id},
            name="masr_routing",
            as_type="span",
            input={"query": redacted_input},
            metadata=metadata or {},
        )
    except Exception as e:
        logger.warning(
            "langfuse_trace_create_failed",
            error=str(e),
            error_type=type(e).__name__,
            query_id=query_id,
        )
        observation = None

    try:
        # Body runs regardless of whether the observation was created. Body
        # exceptions are NOT caught here — they propagate to the caller.
        yield observation
    finally:
        if observation is not None:
            try:
                observation.end()
            except Exception as e:
                logger.warning(
                    "langfuse_trace_end_failed",
                    error=str(e),
                    error_type=type(e).__name__,
                    query_id=query_id,
                )


@contextmanager
def trace_provider_call(
    parent: LangfuseSpan | None,
    provider: str,
    model: str,
    metadata: dict[str, Any] | None = None,
) -> Generator[LangfuseGeneration | None, None, None]:
    """Trace an LLM provider call as a nested ``generation`` observation.

    Contract mirrors :func:`trace_masr_routing`: yields exactly once, body
    exceptions propagate unchanged, all SDK interactions are guarded, and the
    observation is ended in ``finally``.

    Args:
        parent: Parent routing observation (from :func:`trace_masr_routing`), or
            None when tracing is disabled.
        provider: Provider name (e.g. "openrouter").
        model: Model identifier (e.g. "claude-sonnet-4.6").
        metadata: Additional observation metadata.

    Yields:
        A LangfuseGeneration handle when tracing is active, else None.
    """
    if parent is None:
        yield None
        return

    observation: LangfuseGeneration | None = None
    try:
        observation = parent.start_observation(  # type: ignore[assignment]
            name=f"{provider}_call",
            as_type="generation",
            model=model,
            metadata={"provider": provider, **(metadata or {})},
        )
    except Exception as e:
        logger.warning(
            "langfuse_generation_create_failed",
            error=str(e),
            error_type=type(e).__name__,
            provider=provider,
            model=model,
        )
        observation = None

    try:
        yield observation
    finally:
        if observation is not None:
            try:
                observation.end()
            except Exception as e:
                logger.warning(
                    "langfuse_generation_end_failed",
                    error=str(e),
                    error_type=type(e).__name__,
                    provider=provider,
                    model=model,
                )


def record_provider_metrics(
    observation: LangfuseGeneration | None,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    latency_ms: int,
) -> None:
    """Record token usage, cost, and latency onto a generation observation.

    Uses the v4 first-class ``usage_details`` and ``cost_details`` fields so the
    values render natively in the Langfuse UI. No-op (and never raises) when the
    observation is None.

    Args:
        observation: Generation handle from :func:`trace_provider_call`, or None.
        prompt_tokens: Tokens in the prompt.
        completion_tokens: Tokens in the completion.
        cost_usd: Estimated cost in USD.
        latency_ms: Provider call latency in milliseconds.
    """
    if observation is None:
        return

    try:
        total_tokens = prompt_tokens + completion_tokens
        observation.update(
            usage_details={
                "input": prompt_tokens,
                "output": completion_tokens,
                "total": total_tokens,
            },
            cost_details={"total": cost_usd},
            metadata={"latency_ms": latency_ms},
        )
    except Exception as e:
        logger.warning(
            "langfuse_metrics_failed",
            error=str(e),
            error_type=type(e).__name__,
        )


def flush_langfuse() -> None:
    """Flush pending traces to the Langfuse server (guarded, no-op if disabled).

    Inspects the cached client directly WITHOUT calling get_langfuse_client(),
    so this function never triggers lazy initialization at shutdown time.
    """
    # Read the cached client under the lock to avoid a race with concurrent init.
    with _langfuse_lock:
        client = _langfuse_client

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


def shutdown_langfuse() -> None:
    """Shut the Langfuse client down cleanly.

    Calls ``client.shutdown()``, which flushes pending traces AND stops the
    SDK's background export threads (a bare ``flush()`` leaves them running).

    Inspects the cached client directly WITHOUT calling get_langfuse_client(),
    so this function never triggers lazy initialization at shutdown time (e.g.
    when tracing was enabled but no request was ever routed).  Guarded and a
    no-op when tracing is disabled or the client was never constructed.
    """
    # Read the cached client under the lock to avoid a race with concurrent init.
    with _langfuse_lock:
        client = _langfuse_client

    if client is None:
        return

    try:
        client.shutdown()
        logger.debug("langfuse_shutdown")
    except Exception as e:
        logger.warning(
            "langfuse_shutdown_failed",
            error=str(e),
            error_type=type(e).__name__,
        )
