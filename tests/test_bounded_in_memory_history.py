"""Long-lived singletons must not grow with traffic."""

from collections import deque
from datetime import datetime

from src.ai_brain.router.routing_metrics import (
    MAX_ROUTING_HISTORY,
    RoutingMetricsCollector,
)
from src.api.services.agent_execution_service import AgentExecutionService


class _Decision:
    def __init__(self) -> None:
        self.timestamp = datetime.now()
        self.confidence_score = 0.5


def test_routing_history_is_bounded() -> None:
    collector = RoutingMetricsCollector()
    for _ in range(MAX_ROUTING_HISTORY + 1):
        collector.add_to_history(_Decision())  # type: ignore[arg-type]
    assert collector.get_history_size() == MAX_ROUTING_HISTORY


def test_routing_history_evicts_oldest() -> None:
    collector = RoutingMetricsCollector()
    first = _Decision()
    collector.add_to_history(first)  # type: ignore[arg-type]
    for _ in range(MAX_ROUTING_HISTORY):
        collector.add_to_history(_Decision())  # type: ignore[arg-type]
    assert first not in collector.routing_history
    assert isinstance(collector.routing_history, deque)


def test_adaptive_strategy_still_works_on_bounded_history() -> None:
    collector = RoutingMetricsCollector(min_history_for_adaptation=2)
    for _ in range(3):
        collector.add_to_history(_Decision())  # type: ignore[arg-type]
    assert collector.get_adaptive_strategy(None) is not None


def test_execution_service_keeps_no_unbounded_history() -> None:
    assert not hasattr(AgentExecutionService(), "execution_history")
