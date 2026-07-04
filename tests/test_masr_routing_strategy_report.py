"""Regression tests for MASR routing-decision strategy reporting.

Guards against a silent-wrong bug where ``MASRRoutingService`` read
``decision.optimization_result.strategy`` (a non-existent attribute — the real
field is ``strategy_used``) behind a ``hasattr`` guard that was always False.
The guard silently fell back to ``RoutingStrategy.BALANCED``, so ``/masr/route``
reported ``balanced`` for *every* request regardless of the strategy chosen,
and feedback/metrics/reasoning were all attributed to the wrong strategy.

These tests drive the real service end to end (routing and complexity analysis
are local/statistical — no LLM or network calls) and assert that the reported
strategy reflects the requested one. On the pre-fix code every assertion here
resolves to ``balanced`` and fails.
"""

import pytest

from src.ai_brain.router.routing_types import RoutingStrategy
from src.api.services.masr_routing_service import MASRRoutingService
from src.models.masr_api_models import RoutingRequest


@pytest.fixture
def service() -> MASRRoutingService:
    return MASRRoutingService()


@pytest.mark.parametrize(
    "requested",
    [
        RoutingStrategy.QUALITY_FOCUSED,
        RoutingStrategy.COST_EFFICIENT,
        RoutingStrategy.SPEED_FIRST,
        RoutingStrategy.BALANCED,
    ],
)
async def test_response_echoes_requested_strategy(
    service: MASRRoutingService, requested: RoutingStrategy,
) -> None:
    """The routing response must report the strategy that was requested."""
    request = RoutingRequest(
        query="Perform a rigorous comparative analysis of transformer scaling laws.",
        strategy=requested,
    )

    response = await service.get_routing_decision(request)

    assert response.strategy == requested, (
        f"requested {requested} but response reported {response.strategy}"
    )


async def test_reasoning_reflects_requested_strategy(
    service: MASRRoutingService,
) -> None:
    """Human-readable reasoning should name the requested strategy, not a default."""
    request = RoutingRequest(
        query="Summarize the key findings quickly.",
        strategy=RoutingStrategy.SPEED_FIRST,
    )

    response = await service.get_routing_decision(request)

    assert "speed_first" in response.reasoning
    assert "balanced strategy" not in response.reasoning


async def test_alternatives_exclude_the_selected_strategy(
    service: MASRRoutingService,
) -> None:
    """The alternatives list should exclude whichever strategy was actually chosen."""
    request = RoutingRequest(
        query="Evaluate the cost tradeoffs of several model providers.",
        strategy=RoutingStrategy.COST_EFFICIENT,
    )

    response = await service.get_routing_decision(request)

    assert response.alternatives is not None
    alt_strategies = {alt["strategy"] for alt in response.alternatives}
    assert RoutingStrategy.COST_EFFICIENT.value not in alt_strategies
