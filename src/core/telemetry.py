"""
Shared context-compaction telemetry helpers.

Provides a single location for tiktoken availability checks, encoder caching,
and the telemetry-enabled guard so the four hotspot modules do not each
duplicate those concerns.

Usage:
    from src.core.telemetry import count_tokens, telemetry_enabled

    if telemetry_enabled():
        n = count_tokens(some_text)
        logger.info("my_event", token_count=n)
"""

from typing import Any

from structlog import get_logger

logger = get_logger()

# ---------------------------------------------------------------------------
# Tiktoken availability guard
# ---------------------------------------------------------------------------

try:
    import tiktoken as _tiktoken

    TIKTOKEN_AVAILABLE = True
except ImportError:
    _tiktoken = None  # type: ignore[assignment]
    TIKTOKEN_AVAILABLE = False
    logger.debug("tiktoken not installed; context-compaction token counts will be 0")

# Encoding name used across all hotspots (cl100k_base — GPT-3.5 / GPT-4 family)
_ENCODING_NAME = "cl100k_base"

# Module-level cache so get_encoding() is called at most once per process.
_encoder: Any | None = None

# ---------------------------------------------------------------------------
# Event-loop safety: cap on text fed to tiktoken in measurement calls.
#
# Serializing + encoding an unbounded payload (e.g. large worker_results blobs
# or multi-domain outputs) on the async event loop can stall unrelated
# requests.  Token measurement is telemetry — an approximate count from a
# capped prefix is preferable to monopolising the loop.
#
# Callers that need the exact count on already-bounded strings may pass the
# text directly to count_tokens(); this cap applies only to the capped helper.
# ---------------------------------------------------------------------------
_MAX_MEASURE_CHARS: int = 100_000


def _get_encoder() -> Any | None:
    """Return a cached tiktoken encoder, or None if tiktoken is unavailable."""
    global _encoder
    if not TIKTOKEN_AVAILABLE:
        return None
    if _encoder is None:
        _encoder = _tiktoken.get_encoding(_ENCODING_NAME)  # type: ignore[union-attr]
    return _encoder


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def telemetry_enabled() -> bool:
    """Return True when context-compaction telemetry is active.

    Reads ``ENABLE_CONTEXT_COMPACTION_TELEMETRY`` from application settings.
    Any exception during settings access (e.g. circular import at module load,
    misconfigured environment) safely returns False so callers never crash.
    """
    try:
        from src.core.config import get_settings

        return bool(get_settings().ENABLE_CONTEXT_COMPACTION_TELEMETRY)
    except Exception:
        return False


def count_tokens(text: str) -> int:
    """Count tokens in *text* using the cl100k_base encoder.

    Returns 0 when tiktoken is unavailable.  Does NOT check
    ``telemetry_enabled()`` — callers are responsible for that guard so the
    function remains a pure utility without side-effects.

    Args:
        text: The string to encode and count.

    Returns:
        Integer token count, or 0 if tiktoken is unavailable or encoding fails.
    """
    if not TIKTOKEN_AVAILABLE:
        return 0
    try:
        enc = _get_encoder()
        if enc is None:
            return 0
        return len(enc.encode(text))
    except Exception as exc:
        logger.debug("count_tokens failed", error=str(exc))
        return 0


def count_tokens_capped(text: str) -> tuple[int, bool]:
    """Count tokens in *text*, silently capping at ``_MAX_MEASURE_CHARS``.

    Intended for telemetry measurement call sites where the input may be an
    arbitrarily large serialized blob.  Truncating before encoding keeps
    tiktoken off hot async-loop paths when the flag is enabled.

    The token count is approximate when truncated — sufficient for sizing
    telemetry; never use this for billing or hard limits.

    Args:
        text: The string to encode and count.

    Returns:
        ``(token_count, truncated)`` where ``truncated`` is True when the input
        exceeded ``_MAX_MEASURE_CHARS`` and was trimmed before encoding.
        token_count is 0 if tiktoken is unavailable or encoding fails.
    """
    truncated = len(text) > _MAX_MEASURE_CHARS
    capped = text[:_MAX_MEASURE_CHARS] if truncated else text
    return count_tokens(capped), truncated


__all__ = [
    "TIKTOKEN_AVAILABLE",
    "_MAX_MEASURE_CHARS",
    "count_tokens",
    "count_tokens_capped",
    "telemetry_enabled",
]
