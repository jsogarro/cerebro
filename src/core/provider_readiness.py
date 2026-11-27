"""Which model providers are configured, without contacting any of them."""

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
