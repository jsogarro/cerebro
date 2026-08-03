"""Bounded telemetry for plan-backed topology admission and provider policy.

Prometheus labels are deliberately limited to fixed enums and typed error
codes. Opaque plan/run identifiers and free-text rejection reasons belong
only in structured logs and never become metric labels — the same
convention as ``src.ai_brain.router.routing_observability``.
"""

from __future__ import annotations

from prometheus_client import Counter
from structlog import get_logger

from src.core.contracts import ExecutionPlan

logger = get_logger(__name__)

execution_plan_admissions_total = Counter(
    "cerebro_execution_plan_admissions_total",
    "Plan-backed topology admission outcomes by collaboration mode and result.",
    ("collaboration_mode", "result"),
)
execution_plan_rejections_total = Counter(
    "cerebro_execution_plan_rejections_total",
    "Plan-backed rejections by typed error code.",
    ("code",),
)


def record_topology_admitted(plan: ExecutionPlan) -> None:
    """Record a plan that passed topology admission, before any dispatch."""

    mode = plan.routing_decision.collaboration_mode.value
    execution_plan_admissions_total.labels(
        collaboration_mode=mode, result="admitted"
    ).inc()
    logger.info(
        "execution_plan_admitted",
        execution_plan_id=str(plan.execution_plan_id),
        plan_version=plan.plan_version,
        collaboration_mode=mode,
    )


def record_topology_rejected(plan: ExecutionPlan, code: str, reason: str) -> None:
    """Record a plan rejected by topology admission, before any dispatch."""

    mode = plan.routing_decision.collaboration_mode.value
    execution_plan_admissions_total.labels(
        collaboration_mode=mode, result="rejected"
    ).inc()
    execution_plan_rejections_total.labels(code=code).inc()
    logger.warning(
        "execution_plan_rejected",
        execution_plan_id=str(plan.execution_plan_id),
        plan_version=plan.plan_version,
        collaboration_mode=mode,
        code=code,
        reason=reason,
    )


def record_provider_unavailable(
    plan: ExecutionPlan, provider: str, reason: str
) -> None:
    """Record a plan-backed provider-policy failure (fail-closed, no substitution)."""

    execution_plan_rejections_total.labels(code="PLAN_PROVIDER_UNAVAILABLE").inc()
    logger.warning(
        "execution_plan_provider_unavailable",
        execution_plan_id=str(plan.execution_plan_id),
        plan_version=plan.plan_version,
        provider=provider,
        reason=reason,
    )


__all__ = [
    "execution_plan_admissions_total",
    "execution_plan_rejections_total",
    "record_provider_unavailable",
    "record_topology_admitted",
    "record_topology_rejected",
]
