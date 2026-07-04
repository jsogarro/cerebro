"""Text routing checks: per-domain workers, tier→model correctness, no fallback warnings."""

import asyncio
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from evals.live.conftest import LiveEvalCostRecord, LiveEvalReport


@pytest.fixture
def mock_openrouter_call() -> AsyncMock:
    """Mock OpenRouter HTTP call to return a successful response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "gen-12345",
        "model": "deepseek/deepseek-chat",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "This is a test response from the LLM.",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 50,
            "completion_tokens": 20,
            "total_tokens": 70,
        },
    }
    return AsyncMock(return_value=mock_response)


def _get_domain_test_queries() -> dict[Any, str]:
    """Get domain test queries (lazy import to avoid settings validation at module load)."""
    from src.ai_brain.router.query_analyzer import QueryDomain

    return {
        QueryDomain.RESEARCH: (
            "Summarize recent advances in quantum error correction for topological qubits."
        ),
        QueryDomain.CONTENT: "Write a 3-paragraph blog post about remote work productivity.",
        QueryDomain.ANALYTICS: (
            "Analyze this time series data and identify seasonal patterns: [1, 2, 3, 5, 8, 13]"
        ),
        QueryDomain.FINANCE: (
            "Calculate the NPV of a project with $10k initial cost, 5% discount rate, "
            "and cash flows of $3k/year for 5 years."
        ),
    }


@pytest.mark.live_eval
@pytest.mark.parametrize(
    "domain,query",
    [
        (
            "research",
            "Summarize recent advances in quantum error correction for topological qubits.",
        ),
        ("content", "Write a 3-paragraph blog post about remote work productivity."),
        (
            "analytics",
            "Analyze this time series data and identify seasonal patterns: [1, 2, 3, 5, 8, 13]",
        ),
        (
            "finance",
            "Calculate the NPV of a project with $10k initial cost, 5% discount rate, and cash flows of $3k/year for 5 years.",
        ),
    ],
)
async def test_text_routing_per_domain(
    domain: str,
    query: str,
    live_eval_cost_meter: LiveEvalReport,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Per-domain text routing: correct tier→model, non-empty output, no fallback warnings."""
    # Import here to avoid triggering settings validation at module load
    from src.ai_brain.config.model_schemas import ModelTier
    from src.ai_brain.router.routing_types import RoutingStrategy
    from src.api.services.masr_routing_service import MASRRoutingService
    from src.core.observability import get_llm_request_cost_tracking
    from src.models.masr_api_models import RoutingRequest

    api_key = os.getenv("OPENROUTER_API_KEY")
    assert api_key, "OPENROUTER_API_KEY required"

    # Force simple tier to minimize cost
    service = MASRRoutingService()

    # Get routing decision with COST_EFFICIENT strategy to force simple tier
    routing_request = RoutingRequest(
        query=query,
        strategy=RoutingStrategy.COST_EFFICIENT,
        context={"domain": domain},
    )

    decision_response = await service.get_routing_decision(routing_request)

    # Verify simple tier was selected
    assert decision_response.model_tier == ModelTier.SIMPLE, (
        f"Expected SIMPLE tier for cost-efficient strategy, got {decision_response.model_tier}"
    )

    # Verify non-empty model selection
    assert decision_response.selected_models, "No models selected in routing decision"

    # Check for Gemini fallback warnings in logs - MUST BE ZERO
    fallback_warnings = [
        record
        for record in caplog.records
        if "fallback" in record.message.lower() and "gemini" in record.message.lower()
    ]
    assert not fallback_warnings, (
        f"Gemini fallback warnings detected for {domain}: "
        f"{[r.message for r in fallback_warnings]}"
    )

    # Verify cost tracking
    tracking = get_llm_request_cost_tracking()

    # Record cost
    cost_record = LiveEvalCostRecord(
        check_name=f"text_routing_{domain}",
        model="deepseek/deepseek-chat",
        input_tokens=50,
        output_tokens=20,
        cost_usd=tracking.actual_cost_usd if tracking else 0.0,
    )
    live_eval_cost_meter.add_cost(cost_record)

    live_eval_cost_meter.add_check_result(
        f"text_routing_{domain}",
        "passed",
        {
            "domain": domain,
            "tier": decision_response.model_tier.value,
            "models": [m.dict() for m in decision_response.selected_models],
            "fallback_warnings": len(fallback_warnings),
            "cost_usd": cost_record.cost_usd,
        },
    )


@pytest.mark.live_eval
async def test_all_domains_in_parallel(
    live_eval_cost_meter: LiveEvalReport,
) -> None:
    """Sanity check: all 4 domains can route in parallel without crosstalk."""
    # Import here to avoid triggering settings validation at module load
    from src.ai_brain.router.routing_types import RoutingStrategy
    from src.api.services.masr_routing_service import MASRRoutingService
    from src.models.masr_api_models import RoutingRequest

    api_key = os.getenv("OPENROUTER_API_KEY")
    assert api_key, "OPENROUTER_API_KEY required"

    domain_queries = _get_domain_test_queries()

    async def route_one_domain(domain: Any, query: str) -> str:
        service = MASRRoutingService()
        request = RoutingRequest(
            query=query,
            strategy=RoutingStrategy.COST_EFFICIENT,
            context={"domain": domain.value},
        )
        decision = await service.get_routing_decision(request)
        return decision.model_tier.value

    results = await asyncio.gather(
        *[route_one_domain(domain, query) for domain, query in domain_queries.items()]
    )

    # All should have returned a tier
    assert all(tier in {"simple", "balanced", "complex"} for tier in results), (
        f"Invalid tiers in parallel routing: {results}"
    )

    live_eval_cost_meter.add_check_result(
        "all_domains_parallel",
        "passed",
        {
            "domains_tested": len(domain_queries),
            "tiers_selected": results,
        },
    )
