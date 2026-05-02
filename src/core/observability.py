"""Shared observability primitives."""

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


def record_llm_call(metrics: LLMCallMetrics) -> None:
    """Record a single LLM call to structured logs and Prometheus."""
    model = metrics.model or "unknown"
    provider = metrics.provider or "unknown"

    llm_call_duration_seconds.labels(model=model, provider=provider).observe(
        metrics.latency_seconds
    )
    llm_tokens_total.labels(model=model, provider=provider, type="prompt").inc(
        max(metrics.prompt_tokens, 0)
    )
    llm_tokens_total.labels(model=model, provider=provider, type="completion").inc(
        max(metrics.completion_tokens, 0)
    )
    llm_cost_usd_total.labels(model=model, provider=provider).inc(
        max(metrics.cost_usd, 0.0)
    )

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

