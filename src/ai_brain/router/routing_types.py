"""
Routing Types for MASR

Shared dataclasses and enums used across the routing system.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class CollaborationMode(StrEnum):
    """Agent collaboration modes for different query types."""

    DIRECT = "direct"  # Single agent handles everything
    PARALLEL = "parallel"  # Multiple agents work simultaneously
    HIERARCHICAL = "hierarchical"  # Supervisor coordinates workers
    DEBATE = "debate"  # Agents discuss and refine responses
    ENSEMBLE = "ensemble"  # Multiple models/agents vote on result


class RoutingStrategy(StrEnum):
    """High-level routing strategies. Canonical definition; re-exported from
    src.ai_brain.config.model_schemas for backward compatibility."""

    SPEED_FIRST = "speed_first"  # Minimize latency
    COST_EFFICIENT = "cost_efficient"  # Minimize cost
    QUALITY_FOCUSED = "quality_focused"  # Maximize quality
    BALANCED = "balanced"  # Balance all factors
    ADAPTIVE = "adaptive"  # Learn from usage patterns


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
    "AgentAllocation",
    "CollaborationMode",
    "RoutingMetrics",
    "RoutingStrategy",
]
