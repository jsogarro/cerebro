"""Which model providers are configured, without contacting any of them."""

from typing import Any

from src.core.config import settings


def configured_providers() -> dict[str, bool]:
    """Keyed by the dispatchable adapter names in ``PROVIDER_KEYS``."""
    return {
        "deepseek": bool(settings.DEEPSEEK_ENABLED and settings.DEEPSEEK_API_KEY),
        "gemini": bool(settings.GEMINI_ENABLED and settings.GEMINI_API_KEY),
        "llama": bool(settings.LLAMA_ENABLED),
        "openrouter": bool(settings.OPENROUTER_ENABLED and settings.OPENROUTER_API_KEY),
    }


def provider_readiness() -> dict[str, bool | None]:
    """``False`` if unconfigured, else ``None``: nothing probes, so it is unknown."""
    return {
        name: None if configured else False
        for name, configured in configured_providers().items()
    }


def observed_provider_state(gemini_service: Any | None) -> str:
    """Gemini's circuit state, the only breaker a provider keeps, else ``unknown``.

    ``unconfigured`` when no provider is enabled. Other configured providers have
    no observed state, so they never move this value.
    """
    configured = configured_providers()
    if not any(configured.values()):
        return "unconfigured"
    if gemini_service is None or not configured["gemini"]:
        return "unknown"
    return str(gemini_service.circuit.state)
