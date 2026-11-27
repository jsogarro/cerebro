"""
Routing Types for MASR

Shared dataclasses and enums used across the routing system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from src.core.contracts.execution_plan import CollaborationMode


class RoutingStrategy(StrEnum):
    """High-level routing strategies. Canonical definition; re-exported from
    src.ai_brain.config.model_schemas for backward compatibility."""

    SPEED_FIRST = "speed_first"  # Minimize latency
    COST_EFFICIENT = "cost_efficient"  # Minimize cost
    QUALITY_FOCUSED = "quality_focused"  # Maximize quality
    BALANCED = "balanced"  # Balance all factors
    ADAPTIVE = "adaptive"  # Learn from usage patterns


class AdaptiveRoutingStatus(StrEnum):
    """State of the optional Thompson-sampling allocation path."""

    DISABLED = "disabled"
    FIXTURE_OFF = "fixture_off"
    COLD = "cold"
    CONTROL = "control"
    DEGRADED = "degraded"
    ACTIVE = "active"


@dataclass(frozen=True)
class RoutingExecutionPolicy:
    """Request-scoped routing controls owned by the application.

    Fixture isolation must be explicit at the execution boundary rather than
    inferred from ambient environment variables.  The fixture policy disables
    both stateful routing inputs before selection and forbids provider-backed
    execution.
    """

    fixture_mode: bool = False
    adaptive_routing_allowed: bool = True
    memory_routing_allowed: bool = True
    provider_execution_allowed: bool = True

    @classmethod
    def fixture(cls) -> RoutingExecutionPolicy:
        """Return the deterministic, credential-free fixture policy."""

        return cls(
            fixture_mode=True,
            adaptive_routing_allowed=False,
            memory_routing_allowed=False,
            provider_execution_allowed=False,
        )


@dataclass(frozen=True)
class AdaptiveAllocationProposal:
    """Literal bandit proposal before routing budgets and system clamps."""

    experiment_id: str
    analytic_baseline_count: int
    memory_baseline_count: int
    proposed_arm: int
    proposed_worker_count: int
    applied_arm: int
    applied_worker_count: int
    allocation_probability: float | None
    ready: bool
    safety_check_passed: bool
    control_reason: str | None = None
    state_revision: int = 0


@dataclass(frozen=True)
class AdaptiveDecisionMetadata:
    """Serializable attribution for an adaptive allocation decision."""

    schema_version: str
    policy_version: str
    state_revision: int
    status: AdaptiveRoutingStatus
    enabled: bool
    ready: bool
    analytic_baseline_count: int
    memory_baseline_count: int
    proposed_arm: int
    proposed_worker_count: int
    proposal_probability: float | None
    safety_clamped: bool
    budget_clamped: bool
    system_clamped: bool
    final_worker_count: int
    applied_arm: int
    control_reason: str | None = None

    def to_dict(self) -> dict[str, str | int | float | bool | None]:
        """Return additive JSON-compatible routing metadata."""

        return {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "state_revision": self.state_revision,
            "status": self.status.value,
            "enabled": self.enabled,
            "ready": self.ready,
            "analytic_baseline_count": self.analytic_baseline_count,
            "memory_baseline_count": self.memory_baseline_count,
            "proposed_arm": self.proposed_arm,
            "proposed_worker_count": self.proposed_worker_count,
            "proposal_probability": self.proposal_probability,
            "safety_clamped": self.safety_clamped,
            "budget_clamped": self.budget_clamped,
            "system_clamped": self.system_clamped,
            "final_worker_count": self.final_worker_count,
            "applied_arm": self.applied_arm,
            "control_reason": self.control_reason,
        }


@dataclass
class AgentAllocation:
    """Specification for agent allocation."""

    supervisor_type: str
    worker_count: int = 1
    worker_types: list[str] = field(default_factory=list)
    max_parallel: int = 5
    timeout_seconds: int = 300
    retry_attempts: int = 2


@dataclass
class RoutingMetrics:
    """Metrics for tracking routing performance."""

    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    avg_response_time_ms: float = 0.0
    avg_cost_per_request: float = 0.0
    avg_quality_score: float = 0.0
    fallback_usage_rate: float = 0.0

    # Strategy effectiveness
    strategy_performance: dict[str, float] = field(default_factory=dict)
    model_performance: dict[str, float] = field(default_factory=dict)

    # Time-based metrics
    last_updated: datetime = field(default_factory=datetime.now)


__all__ = [
    "AdaptiveAllocationProposal",
    "AdaptiveDecisionMetadata",
    "AdaptiveRoutingStatus",
    "AgentAllocation",
    "CollaborationMode",
    "RoutingExecutionPolicy",
    "RoutingMetrics",
    "RoutingStrategy",
]
