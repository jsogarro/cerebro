"""
Configuration for Gemini service.

This module contains configuration settings for the Gemini API integration.
"""

import os
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

# Load environment variables
load_dotenv()


@dataclass(frozen=True)
class GeminiConfig:
    """
    Immutable configuration for Gemini service.

    Following functional programming principles with immutable data structures.
    """

    api_key: str
    model_name: str = "gemini-2.0-flash"
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 40
    max_output_tokens: int = 4096

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
    def from_env(cls) -> "GeminiConfig":
        """
        Create configuration from environment variables.

        This is a pure function that reads environment and returns config.
        """
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable is required")

        return cls(
            api_key=api_key,
            model_name=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
            temperature=float(os.getenv("GEMINI_TEMPERATURE", "0.7")),
            top_p=float(os.getenv("GEMINI_TOP_P", "0.9")),
            top_k=int(os.getenv("GEMINI_TOP_K", "40")),
            max_output_tokens=int(os.getenv("GEMINI_MAX_TOKENS", "4096")),
            rate_limit=int(os.getenv("GEMINI_RATE_LIMIT", "10")),
            rate_period=int(os.getenv("GEMINI_RATE_PERIOD", "60")),
            max_concurrent=int(os.getenv("GEMINI_MAX_CONCURRENT", "5")),
            cache_enabled=os.getenv("GEMINI_CACHE_ENABLED", "true").lower() == "true",
            cache_ttl=int(os.getenv("GEMINI_CACHE_TTL", "3600")),
        )


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


def get_safety_settings(config: GeminiConfig) -> list[Any]:
    """
    Get safety settings for Gemini model.

    Pure function that transforms config to safety settings.
    """
    from google.generativeai.types import HarmBlockThreshold, HarmCategory

    settings = []

    # Map configuration to safety thresholds
    threshold = HarmBlockThreshold.BLOCK_NONE
    if config.block_few:
        threshold = HarmBlockThreshold.BLOCK_LOW_AND_ABOVE
    elif config.block_some:
        threshold = HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE
    elif config.block_most:
        threshold = HarmBlockThreshold.BLOCK_ONLY_HIGH

    # Apply threshold to all harm categories
    for category in [
        HarmCategory.HARM_CATEGORY_HARASSMENT,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
    ]:
        settings.append(
            {
                "category": category,
                "threshold": threshold,
            }
        )

    return settings
