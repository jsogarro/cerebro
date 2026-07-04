"""Regression test for MASR routing-decision model-tier reporting.

The routing response read ``decision.optimization_result.model_tier`` behind a
``hasattr`` guard, but ``OptimizationResult`` has no ``model_tier`` field (the
chosen tier lives on ``primary_model.tier``). The guard was always False, so
``selected_models`` defaulted to the STANDARD tier for every request regardless
of the optimizer's actual choice.

This test builds a real routing decision, forces its primary model to the
PREMIUM tier, and asserts the response surfaces premium models. It fails on the
pre-fix code (which always returns STANDARD models).
"""

from unittest.mock import AsyncMock, patch

from src.ai_brain.router.cost_optimizer import ModelTier as OptimizerModelTier
from src.ai_brain.router.routing_types import RoutingStrategy
from src.api.services.masr_routing_service import MASRRoutingService
from src.models.masr_api_models import RoutingRequest


async def test_selected_models_reflect_optimizer_tier() -> None:
    service = MASRRoutingService()

    # Build a genuine decision via the real router, then force the primary
    # model's tier so the response builder has an unambiguous non-STANDARD tier
    # to surface.
    decision = await service.router.route(
        "Analyze the tradeoffs in distributed consensus algorithms",
        context={},
        strategy=RoutingStrategy.QUALITY_FOCUSED,
    )
    decision.optimization_result.primary_model.tier = OptimizerModelTier.PREMIUM

    with patch.object(service.router, "route", new=AsyncMock(return_value=decision)):
        response = await service.get_routing_decision(
            RoutingRequest(query="anything", strategy=RoutingStrategy.QUALITY_FOCUSED),
        )

    assert response.selected_models, "no models were surfaced for the decision"
    tiers = {str(getattr(m.tier, "value", m.tier)) for m in response.selected_models}
    assert tiers == {"premium"}, f"expected only premium models, got tiers={tiers}"
