"""
Configuration for Gemini service.

This module contains configuration settings for the Gemini API integration.
"""

import os
from dataclasses import dataclass
from typing import Any, TypeVar

from src.core.environment import load_environment

_Number = TypeVar("_Number", int, float)


@dataclass(frozen=True)
class GeminiConfig:
    """
    Immutable configuration for Gemini service.

    Following functional programming principles with immutable data structures.
    """

    api_key: str
    model_name: str = "gemini-2.5-flash"
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 40
    # gemini-2.5-flash spends output tokens on internal "thinking" (dynamic
    # budget up to ~24576). 8192 total intermittently left too little for the
    # structured-output JSON, truncating it and failing schema validation.
    # 32768 covers max thinking plus the JSON payload.
    max_output_tokens: int = 32768

    # Rate limiting settings
    rate_limit: int = 10  # requests per period
    rate_period: int = 60  # period in seconds
    max_concurrent: int = 2  # max concurrent requests

    # Retry settings
    max_retries: int = 3
    retry_delay: float = 1.0
    retry_backoff: float = 2.0

    # Cache settings
    cache_enabled: bool = True
    cache_ttl: int = 3600  # 1 hour

    # Safety settings
    block_none: bool = False
    block_few: bool = False
    block_some: bool = True
    block_most: bool = False

    @classmethod
    def from_env(cls, api_key: str | None = None) -> "GeminiConfig":
        """
        Create configuration from environment variables.

        This is a pure function that reads environment and returns config.
        """
        load_environment()
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable is required")

        return cls(
            api_key=api_key,
            model_name=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            temperature=_env_number("GEMINI_TEMPERATURE", cls.temperature),
            top_p=_env_number("GEMINI_TOP_P", cls.top_p),
            top_k=_env_number("GEMINI_TOP_K", cls.top_k),
            max_output_tokens=_env_number("GEMINI_MAX_TOKENS", cls.max_output_tokens),
            rate_limit=_env_number("GEMINI_RATE_LIMIT", cls.rate_limit),
            rate_period=_env_number("GEMINI_RATE_PERIOD", cls.rate_period),
            max_concurrent=_env_number("GEMINI_MAX_CONCURRENT", cls.max_concurrent),
            cache_enabled=os.getenv("GEMINI_CACHE_ENABLED", "true").lower() == "true",
            cache_ttl=_env_number("GEMINI_CACHE_TTL", cls.cache_ttl),
        )


def _env_number(name: str, default: _Number) -> _Number:
    """Read a numeric env var; the error names the variable, never its value."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return type(default)(raw)
    except ValueError:
        raise ValueError(f"{name} must be a valid {type(default).__name__}") from None


def get_generation_config(config: GeminiConfig) -> dict[str, Any]:
    """
    Get generation configuration for Gemini model.

    Pure function that transforms config to generation settings.
    """
    return {
        "temperature": config.temperature,
        "top_p": config.top_p,
        "top_k": config.top_k,
        "max_output_tokens": config.max_output_tokens,
    }
