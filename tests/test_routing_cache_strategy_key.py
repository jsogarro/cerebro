"""Regression tests for the MASR routing-decision cache key.

`RoutingCacheManager` keyed cached decisions on query + user_id + domain only,
ignoring the requested `strategy` and `constraints`. Two requests with the same
query but different strategies (or cost/quality constraints) therefore collided
and the second caller received the first caller's decision. This was masked
while every response reported `balanced`; once the reported strategy became
accurate the collision produced visibly wrong routing.

These tests pin the cache identity to include strategy and constraints, at the
cache layer and end to end through the real router.
"""

from src.ai_brain.router.routing_cache import RoutingCacheManager
from src.ai_brain.router.routing_types import RoutingStrategy
from src.api.services.masr_routing_service import MASRRoutingService
from src.models.masr_api_models import RoutingRequest


def test_cache_key_differs_by_strategy() -> None:
    cache = RoutingCacheManager()
    q, ctx = "compare two model providers", {"user_id": "u1"}

    k_quality = cache._generate_cache_key(q, ctx, RoutingStrategy.QUALITY_FOCUSED)
    k_cost = cache._generate_cache_key(q, ctx, RoutingStrategy.COST_EFFICIENT)

    assert k_quality != k_cost


def test_cache_key_differs_by_constraints() -> None:
    cache = RoutingCacheManager()
    q, ctx = "compare two model providers", {"user_id": "u1"}

    k_a = cache._generate_cache_key(q, ctx, None, {"max_cost": 1.0})
    k_b = cache._generate_cache_key(q, ctx, None, {"max_cost": 9.0})

    assert k_a != k_b


def test_cache_key_stable_for_enum_and_str_strategy() -> None:
    """RoutingRequest uses use_enum_values=True, so strategy can arrive as str."""
    cache = RoutingCacheManager()
    q, ctx = "compare two model providers", {"user_id": "u1"}

    k_enum = cache._generate_cache_key(q, ctx, RoutingStrategy.QUALITY_FOCUSED)
    k_str = cache._generate_cache_key(q, ctx, "quality_focused")

    assert k_enum == k_str


async def test_same_query_different_strategy_not_served_from_cache() -> None:
    """End to end: the second strategy must not receive the first's cached decision."""
    service = MASRRoutingService()
    query = "Analyze the tradeoffs in distributed consensus algorithms"

    first = await service.get_routing_decision(
        RoutingRequest(query=query, strategy=RoutingStrategy.QUALITY_FOCUSED),
    )
    second = await service.get_routing_decision(
        RoutingRequest(query=query, strategy=RoutingStrategy.COST_EFFICIENT),
    )

    assert first.strategy == RoutingStrategy.QUALITY_FOCUSED
    assert second.strategy == RoutingStrategy.COST_EFFICIENT
