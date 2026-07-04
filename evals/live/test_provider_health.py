"""Provider health checks: slug validation, auth, tier-map sanity."""

import os

import pytest

from evals.live.conftest import LiveEvalReport
from src.ai_brain.config.model_config_manager import ModelConfigManager
from src.ai_brain.providers.openrouter_provider import OpenRouterProvider


@pytest.mark.live_eval
async def test_openrouter_auth_and_connectivity(
    live_eval_cost_meter: LiveEvalReport,
) -> None:
    """Verify OpenRouter API key works and endpoint is reachable."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    assert api_key, "OPENROUTER_API_KEY must be set"

    config = {
        "api_key": api_key,
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "tier_mapping": {
            "simple": "deepseek/deepseek-chat",
            "balanced": "anthropic/claude-sonnet-4.6",
            "complex": "anthropic/claude-sonnet-4.6",
        },
        "validate_slugs_on_startup": False,  # We'll do this manually
    }

    manager = ModelConfigManager()
    provider = OpenRouterProvider(config, manager)

    # Initialize HTTP client
    await provider.initialize()

    try:
        # Health check should pass with valid credentials
        health = await provider.check_health()
        assert health.available, f"Provider unavailable: {health.message}"
        assert health.message == "OK", f"Unexpected message: {health.message}"

        live_eval_cost_meter.add_check_result(
            "openrouter_auth_connectivity",
            "passed",
            {
                "available": health.available,
                "message": health.message,
                "latency_ms": health.latency_ms,
            },
        )
    finally:
        await provider.shutdown()


@pytest.mark.live_eval
async def test_openrouter_slug_validation(
    live_eval_cost_meter: LiveEvalReport,
) -> None:
    """Validate all tier_mapping slugs against live OpenRouter catalog."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    assert api_key, "OPENROUTER_API_KEY must be set"

    config = {
        "api_key": api_key,
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "tier_mapping": {
            "simple": "deepseek/deepseek-chat",
            "balanced": "anthropic/claude-sonnet-4.6",
            "complex": "anthropic/claude-sonnet-4.6",
        },
        "validate_slugs_on_startup": False,
    }

    manager = ModelConfigManager()
    provider = OpenRouterProvider(config, manager)

    await provider.initialize()

    try:
        result = await provider.validate_model_slugs()

        # All slugs must be valid - this is a FAIL-LOUDLY check
        assert not result.invalid_slugs, (
            f"Invalid slugs detected in tier_mapping: {result.invalid_slugs}. "
            "This indicates stale/wrong slugs in config - update tier_mapping immediately."
        )
        assert result.validation_error is None, (
            f"Slug validation error: {result.validation_error}"
        )
        assert len(result.valid_slugs) == len(config["tier_mapping"]), (
            f"Expected {len(config['tier_mapping'])} valid slugs, "
            f"got {len(result.valid_slugs)}"
        )

        live_eval_cost_meter.add_check_result(
            "openrouter_slug_validation",
            "passed",
            {
                "valid_slugs": list(result.valid_slugs.values()),
                "invalid_slugs": result.invalid_slugs,
                "tier_count": len(result.valid_slugs),
            },
        )
    finally:
        await provider.shutdown()


@pytest.mark.live_eval
async def test_tier_mapping_coverage(
    live_eval_cost_meter: LiveEvalReport,
) -> None:
    """Ensure tier_mapping covers all required tiers."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    assert api_key, "OPENROUTER_API_KEY must be set"

    config = {
        "api_key": api_key,
        "tier_mapping": {
            "simple": "deepseek/deepseek-chat",
            "balanced": "anthropic/claude-sonnet-4.6",
            "complex": "anthropic/claude-sonnet-4.6",
        },
    }

    required_tiers = {"simple", "balanced", "complex"}
    actual_tiers = set(config["tier_mapping"].keys())

    missing = required_tiers - actual_tiers
    assert not missing, f"Missing required tiers in tier_mapping: {missing}"

    live_eval_cost_meter.add_check_result(
        "tier_mapping_coverage",
        "passed",
        {
            "required_tiers": sorted(required_tiers),
            "actual_tiers": sorted(actual_tiers),
            "coverage_complete": True,
        },
    )
