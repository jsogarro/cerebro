"""
Foundation Model Providers Package

Provides a unified interface for interacting with different foundation models
including local models (Llama, DeepSeek) and cloud APIs (Gemini, OpenAI, Claude).

Key Components:
- BaseProvider: Abstract interface for all model providers
- ModelRouter: Routes requests to optimal models based on MASR decisions
- Provider implementations for each supported model
- Fallback and retry mechanisms
"""

from .base_provider import BaseProvider, ModelRequest, ModelResponse
from .deepseek_provider import DeepSeekProvider
from .gemini_provider import GeminiProvider
from .llama_provider import LlamaProvider
from .model_router import ModelRouter

__all__ = [
    "BaseProvider",
    "DeepSeekProvider",
    "GeminiProvider",
    "LlamaProvider",
    "ModelRequest",
    "ModelResponse",
    "ModelRouter",
]
