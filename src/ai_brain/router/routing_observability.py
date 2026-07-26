"""Bounded adaptive-routing telemetry.

Prometheus labels are deliberately limited to fixed enums. Opaque correlation
identifiers belong only in structured logs and never become metric labels.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge

from .routing_outcome import OutcomeApplicationResult, RoutingOutcome

masr_outcomes_total = Counter(
    "cerebro_masr_outcomes_total",
    "Operational routing outcomes observed by source and application result.",
    ("source", "application_status", "eligible"),
)
masr_outcome_metric_availability_total = Counter(
    "cerebro_masr_outcome_metric_availability_total",
    "Routing outcome metric availability without substituting estimates.",
    ("metric", "availability"),
)
masr_adaptive_effective_state = Gauge(
    "cerebro_masr_adaptive_effective_state",
    "One-hot effective adaptive routing state.",
    ("state",),
)

_EFFECTIVE_STATES = ("disabled", "fixture_off", "cold", "degraded", "active")


def observe_outcome(
    outcome: RoutingOutcome,
    result: OutcomeApplicationResult,
) -> None:
    """Record one final delivery result with bounded labels."""

    masr_outcomes_total.labels(
        source=outcome.source.value,
        application_status=result.status.value,
        eligible=str(result.outcome.eligibility.eligible).lower(),
    ).inc()
    masr_outcome_metric_availability_total.labels(
        metric="cost",
        availability=outcome.cost_availability.value,
    ).inc()
    masr_outcome_metric_availability_total.labels(
        metric="quality",
        availability=outcome.quality_availability.value,
    ).inc()


def observe_effective_state(state: str) -> None:
    """Publish one of the fixed effective states as a one-hot gauge."""

    if state not in _EFFECTIVE_STATES:
        state = "degraded"
    for candidate in _EFFECTIVE_STATES:
        masr_adaptive_effective_state.labels(state=candidate).set(
            1 if candidate == state else 0
        )


__all__ = [
    "masr_adaptive_effective_state",
    "masr_outcome_metric_availability_total",
    "masr_outcomes_total",
    "observe_effective_state",
    "observe_outcome",
]
