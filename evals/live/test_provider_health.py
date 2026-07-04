"""Live provider-health checks: auth, catalog reachability, tier-map slug validity.

Motivated by a real incident: a retired model slug in the tier map made the
provider health probe 404, silently disabling multi-provider routing while
every mocked test stayed green.
"""

import pytest

from src.ai_brain.providers.openrouter_provider import OpenRouterProvider
from src.core.config import settings

pytestmark = pytest.mark.live_eval


def _provider() -> OpenRouterProvider:
    return OpenRouterProvider(
        {
            "enabled": True,
            "api_key": settings.OPENROUTER_API_KEY,
            "endpoint": settings.OPENROUTER_ENDPOINT,
            "tier_mapping": settings.OPENROUTER_TIER_MAPPING,
        }
    )


@pytest.mark.asyncio
async def test_openrouter_auth_and_slug_validation(live_eval_report) -> None:
    """The configured tier map must validate cleanly against the live catalog.

    A stale slug or auth failure here is a hard FAILURE, not a warning.
    """
    provider = _provider()
    await provider.load_configuration()

    result = await provider.validate_model_slugs()

    details = {
        "valid_slugs": dict(result.valid_slugs),
        "invalid_slugs": dict(result.invalid_slugs),
        "validation_error": result.validation_error,
    }
    ok = not result.invalid_slugs and not result.validation_error
    live_eval_report.add_check_result(
        "provider_health.slug_validation", "passed" if ok else "failed", details
    )

    assert result.validation_error is None, (
        f"catalog fetch/auth failed: {result.validation_error}"
    )
    assert not result.invalid_slugs, f"STALE tier-map slugs: {result.invalid_slugs}"
    assert set(result.valid_slugs) == set(settings.OPENROUTER_TIER_MAPPING)


@pytest.mark.asyncio
async def test_no_stale_state_recorded(live_eval_report) -> None:
    """After a clean validation, the provider must not carry stale-slug state."""
    provider = _provider()
    await provider.load_configuration()
    await provider.validate_model_slugs()

    stale = getattr(provider, "stale_slugs", {}) or {}
    live_eval_report.add_check_result(
        "provider_health.no_stale_state",
        "passed" if not stale else "failed",
        {"stale_slugs": dict(stale)},
    )
    assert not stale
