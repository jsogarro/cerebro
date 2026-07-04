"""
Routing Metrics Collector for MASR

Tracks routing performance, strategy effectiveness, and enables adaptive
routing through historical analysis of routing decisions.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .routing_types import RoutingMetrics, RoutingStrategy

if TYPE_CHECKING:
    from .masr import RoutingDecision


class RoutingMetricsCollector:
    """
    Collects and analyzes routing metrics for performance tracking and
    adaptive routing strategies.

    Maintains historical routing decisions and provides analytics for
    continuous improvement.
    """

    def __init__(
        self,
        default_strategy: RoutingStrategy = RoutingStrategy.BALANCED,
        adaptation_window_hours: int = 24,
        min_history_for_adaptation: int = 100,
    ):
        """
        Initialize routing metrics collector.

        Args:
            default_strategy: Default routing strategy when no adaptation available
            adaptation_window_hours: Time window for adaptive strategy analysis
            min_history_for_adaptation: Minimum decisions needed for adaptation
        """
        self.default_strategy = default_strategy
        self.adaptation_window_hours = adaptation_window_hours
        self.min_history_for_adaptation = min_history_for_adaptation

        self.metrics = RoutingMetrics()
        self.routing_history: list[RoutingDecision] = []

    def update_metrics(self, decision: RoutingDecision) -> None:
        """
        Update routing metrics with new decision.

        Args:
            decision: The routing decision to record
        """
        self.metrics.total_requests += 1
        self.metrics.last_updated = datetime.now()
        # Additional metrics would be updated after execution feedback

    def get_metrics(self) -> RoutingMetrics:
        """
        Get current routing metrics.

        Returns:
            Current RoutingMetrics snapshot
        """
        return self.metrics

    def add_to_history(self, decision: RoutingDecision) -> None:
        """
        Add a routing decision to history for adaptive learning.

        Args:
            decision: The routing decision to store
        """
        self.routing_history.append(decision)

    def get_adaptive_strategy(self, complexity_analysis: Any) -> RoutingStrategy:
        """
        Get adaptive routing strategy based on historical performance.

        Analyzes recent routing decisions to select the strategy with
        the best average confidence score.

        Args:
            complexity_analysis: Current query complexity analysis

        Returns:
            Recommended RoutingStrategy based on historical performance
        """
        if len(self.routing_history) < self.min_history_for_adaptation:
            return self.default_strategy

        # Analyze recent performance by strategy
        recent_cutoff = datetime.now() - timedelta(hours=self.adaptation_window_hours)
        recent_decisions = [
            d for d in self.routing_history if d.timestamp > recent_cutoff
        ]

        if not recent_decisions:
            return self.default_strategy

        # Simple strategy selection based on average confidence
        strategy_performance: dict[str, list[float]] = {}
        for decision in recent_decisions:
            strategy = "balanced"  # Simplified for now
            if strategy not in strategy_performance:
                strategy_performance[strategy] = []
            strategy_performance[strategy].append(decision.confidence_score)

        # Select strategy with highest average confidence
        best_strategy = max(
            strategy_performance.items(), key=lambda x: sum(x[1]) / len(x[1])
        )[0]

        return RoutingStrategy(best_strategy)

    async def adapt_from_decision(self, decision: RoutingDecision) -> None:
        """
        Adapt routing parameters based on decision outcomes.

        This is a placeholder for future ML-based adaptation that would
        learn from execution feedback and continuously improve routing
        decisions.

        Args:
            decision: The routing decision to learn from
        """
        # This would implement learning from execution feedback
        # For now, it's a placeholder for future ML-based adaptation
        pass

    def get_history_size(self) -> int:
        """Get current routing history size."""
        return len(self.routing_history)

    def clear_history(self) -> None:
        """Clear routing history (use with caution)."""
        self.routing_history.clear()


__all__ = ["RoutingMetricsCollector"]
