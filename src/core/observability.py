"""Shared observability primitives."""

from contextvars import ContextVar, Token
from dataclasses import dataclass

from prometheus_client import Counter, Histogram
from structlog import get_logger

logger = get_logger(__name__)

llm_call_duration_seconds = Histogram(
    "llm_call_duration_seconds",
    "LLM provider call duration in seconds",
    ["model", "provider"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

llm_tokens_total = Counter(
    "llm_tokens_total",
    "Total LLM tokens used",
    ["model", "provider", "type"],
)

llm_cost_usd_total = Counter(
    "llm_cost_usd_total",
    "Total estimated LLM call cost in USD",
    ["model", "provider"],
)

llm_request_cost_drift_ratio = Histogram(
    "llm_request_cost_drift_ratio",
    "Absolute ratio between MASR estimated request cost and actual LLM call cost",
    ["method", "route"],
    buckets=(0.01, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0),
)

llm_cost_drift_events_total = Counter(
    "llm_cost_drift_events_total",
    "Count of request-level LLM cost drift events beyond the alert threshold",
    ["method", "route", "direction"],
)


@dataclass(frozen=True)
class LLMCallMetrics:
    """Per-call LLM metrics suitable for logs and Prometheus."""

    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    cost_usd: float
    request_id: str | None = None
    success: bool = True

    @property
    def latency_seconds(self) -> float:
        """Return latency in seconds for Prometheus histograms."""
        return max(self.latency_ms, 0) / 1000


@dataclass
class LLMRequestCostTracking:
    """Request-scoped cost state for MASR estimate vs actual provider cost."""

    estimated_cost_usd: float | None = None
    actual_cost_usd: float = 0.0

    @property
    def has_comparable_costs(self) -> bool:
        """Return whether drift can be calculated safely."""
        return (
            self.estimated_cost_usd is not None
            and self.estimated_cost_usd > 0
            and self.actual_cost_usd > 0
        )

    @property
    def drift_ratio(self) -> float | None:
        """Return absolute drift ratio against the MASR estimate."""
        if not self.has_comparable_costs or self.estimated_cost_usd is None:
            return None
        return abs(self.actual_cost_usd - self.estimated_cost_usd) / (
            self.estimated_cost_usd
        )


_llm_request_cost_tracking: ContextVar[LLMRequestCostTracking | None] = ContextVar(
    "llm_request_cost_tracking",
    default=None,
)


def start_llm_request_cost_tracking() -> Token[LLMRequestCostTracking | None]:
    """Start request-scoped LLM cost tracking and return a reset token."""
    return _llm_request_cost_tracking.set(LLMRequestCostTracking())


def reset_llm_request_cost_tracking(
    token: Token[LLMRequestCostTracking | None],
) -> None:
    """Reset request-scoped LLM cost tracking."""
    _llm_request_cost_tracking.reset(token)


def set_llm_request_estimated_cost(cost_usd: float | None) -> None:
    """Record MASR's request-level cost estimate for the active request."""
    tracking = _llm_request_cost_tracking.get()
    if tracking is None or cost_usd is None:
        return
    tracking.estimated_cost_usd = max(float(cost_usd), 0.0)


def get_llm_request_cost_tracking() -> LLMRequestCostTracking | None:
    """Return active request cost tracking state, if any."""
    return _llm_request_cost_tracking.get()


def record_llm_request_cost_drift(
    *,
    method: str,
    route: str,
    threshold_ratio: float = 0.2,
) -> LLMRequestCostTracking | None:
    """Record request-level cost drift metrics and alert logs when applicable."""
    tracking = get_llm_request_cost_tracking()
    if tracking is None:
        return None

    drift_ratio = tracking.drift_ratio
    if drift_ratio is None or tracking.estimated_cost_usd is None:
        return tracking

    llm_request_cost_drift_ratio.labels(method=method, route=route).observe(
        drift_ratio
    )
    if drift_ratio > threshold_ratio:
        direction = (
            "over"
            if tracking.actual_cost_usd > tracking.estimated_cost_usd
            else "under"
        )
        llm_cost_drift_events_total.labels(
            method=method,
            route=route,
            direction=direction,
        ).inc()
        logger.warning(
            "llm_request_cost_drift_detected",
            method=method,
            route=route,
            estimated_cost_usd=tracking.estimated_cost_usd,
            actual_cost_usd=tracking.actual_cost_usd,
            drift_ratio=drift_ratio,
            threshold_ratio=threshold_ratio,
            direction=direction,
        )

    return tracking


def record_llm_call(metrics: LLMCallMetrics) -> None:
    """Record a single LLM call to structured logs and Prometheus."""
    model = metrics.model or "unknown"
    provider = metrics.provider or "unknown"
    safe_cost_usd = max(metrics.cost_usd, 0.0)

    llm_call_duration_seconds.labels(model=model, provider=provider).observe(
        metrics.latency_seconds
    )
    llm_tokens_total.labels(model=model, provider=provider, type="prompt").inc(
        max(metrics.prompt_tokens, 0)
    )
    llm_tokens_total.labels(model=model, provider=provider, type="completion").inc(
        max(metrics.completion_tokens, 0)
    )
    llm_cost_usd_total.labels(model=model, provider=provider).inc(safe_cost_usd)

    tracking = _llm_request_cost_tracking.get()
    if tracking is not None:
        tracking.actual_cost_usd += safe_cost_usd

    logger.info(
        "llm_call_completed",
        provider=provider,
        model=model,
        prompt_tokens=metrics.prompt_tokens,
        completion_tokens=metrics.completion_tokens,
        latency_ms=metrics.latency_ms,
        cost_usd=metrics.cost_usd,
        request_id=metrics.request_id,
        success=metrics.success,
    )
