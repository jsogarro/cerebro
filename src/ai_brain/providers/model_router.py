"""
Model Router Implementation

Intelligent routing system that selects the optimal model provider based on
MASR decisions, handles fallback strategies, and manages provider health
monitoring. This is the execution layer that implements the routing decisions
from the MASR system.
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from structlog import get_logger

from .base_provider import (
    BaseProvider,
    ModelRequest,
    ModelResponse,
    ProviderHealthStatus,
)
from .deepseek_provider import DeepSeekProvider
from .gemini_provider import GeminiProvider
from .llama_provider import LlamaProvider

logger = get_logger(__name__)


@dataclass
class ProviderRegistry:
    """Registry of available model providers."""

    providers: dict[str, BaseProvider] = field(default_factory=dict)
    provider_configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    health_status: dict[str, ProviderHealthStatus] = field(default_factory=dict)
    last_health_check: dict[str, datetime] = field(default_factory=dict)


@dataclass
class RoutingDecision:
    """Routing decision with provider and model selection."""

    provider_name: str
    model_name: str
    confidence: float
    fallback_providers: list[str] = field(default_factory=list)
    routing_reason: str = ""


class ModelRouter:
    """
    Intelligent model router that executes MASR routing decisions.

    Manages multiple model providers, handles fallback strategies,
    monitors provider health, and provides unified access to all
    foundation models in the Cerebro system.
    """

    def __init__(self, config: dict[str, Any]):
        """Initialize model router with configuration."""
        self.config = config

        # Provider registry
        self.registry = ProviderRegistry()

        # Routing configuration
        self.health_check_interval = config.get(
            "health_check_interval", 300
        )  # 5 minutes
        self.max_retries = config.get("max_retries", 3)
        self.fallback_enabled = config.get("enable_fallback", True)

        # Provider class mapping
        self.provider_classes = {
            "deepseek": DeepSeekProvider,
            "llama": LlamaProvider,
            "gemini": GeminiProvider,
        }

        # Performance tracking
        self.request_count = 0
        self.success_count = 0
        self.fallback_count = 0

        # Initialize providers
        self._initialize_providers()

    def _initialize_providers(self) -> None:
        """Initialize all configured providers."""

        provider_configs = self.config.get("providers", {})

        for provider_name, provider_config in provider_configs.items():
            if not provider_config.get("enabled", True):
                logger.info("Skipping disabled provider", provider_name=provider_name)
                continue

            try:
                provider_class = self.provider_classes.get(provider_name)
                if not provider_class:
                    logger.warning("Unknown provider type", provider_name=provider_name)
                    continue

                # Initialize provider
                provider = provider_class(provider_config)

                self.registry.providers[provider_name] = provider
                self.registry.provider_configs[provider_name] = provider_config

                logger.info("Initialized provider", provider_name=provider_name)

            except Exception as e:
                logger.error(
                    "Failed to initialize provider",
                    provider_name=provider_name,
                    error=str(e),
                    exc_info=True,
                )

    async def route_and_generate(
        self, request: ModelRequest, routing_decision: dict[str, Any] | None = None
    ) -> ModelResponse:
        """
        Route request and generate response using optimal provider.

        Args:
            request: The model request to process
            routing_decision: Optional routing decision from MASR

        Returns:
            ModelResponse from the selected provider
        """

        self.request_count += 1

        try:
            # Determine routing if not provided
            if not routing_decision:
                routing_decision = await self._make_routing_decision(request)

            # Extract routing information
            primary_provider = routing_decision.get("primary_model", {}).get("provider")
            primary_model = routing_decision.get("primary_model", {}).get("name")
            fallback_models = routing_decision.get("fallback_models", [])

            # Attempt generation with primary provider
            response = await self._try_provider(
                request, primary_provider, primary_model
            )

            if response.success:
                self.success_count += 1
                return response

            # Try fallback providers if enabled
            if self.fallback_enabled and fallback_models:
                logger.info(
                    "Primary provider failed, trying fallbacks",
                    provider_name=primary_provider,
                )

                for fallback_model in fallback_models:
                    fallback_provider = fallback_model.provider
                    fallback_model_name = fallback_model.name

                    try:
                        response = await self._try_provider(
                            request, fallback_provider, fallback_model_name
                        )

                        if response.success:
                            self.fallback_count += 1
                            self.success_count += 1
                            return response

                    except Exception as e:
                        logger.warning(
                            "Fallback provider failed",
                            provider_name=fallback_provider,
                            error=str(e),
                        )
                        continue

            # All providers failed
            logger.error("All providers failed for request")
            return self._create_failure_response(request, "All providers failed")

        except Exception as e:
            logger.error("Routing failed", error=str(e), exc_info=True)
            return self._create_failure_response(request, str(e))

    async def _try_provider(
        self, request: ModelRequest, provider_name: str, model_name: str
    ) -> ModelResponse:
        """Try to generate response using specific provider."""

        provider = self.registry.providers.get(provider_name)
        if not provider:
            raise ValueError(f"Provider not available: {provider_name}")

        # Check provider health
        health_status = await self._get_provider_health(provider_name)
        if not health_status.healthy:
            raise Exception(f"Provider {provider_name} is unhealthy")

        # Generate response
        response = await provider.generate(request, model_name)

        # Update health status based on response
        if not response.success:
            health_status.recent_errors.append(
                f"{datetime.now()}: {response.error_message}"
            )

        return response

    async def _make_routing_decision(self, request: ModelRequest) -> dict[str, Any]:
        """Make routing decision when not provided by MASR."""

        # Simple fallback routing logic
        # In production, this would integrate with MASR

        available_providers = [
            name
            for name, provider in self.registry.providers.items()
            if isinstance(self.registry.health_status.get(name), ProviderHealthStatus)
            and self.registry.health_status.get(name, ProviderHealthStatus(provider_name=name, healthy=False, avg_latency_ms=0, last_check=datetime.now(), error_rate=0.0)).healthy
        ]

        if not available_providers:
            raise Exception("No healthy providers available")

        # Simple heuristic-based selection
        if request.complexity_score and request.complexity_score > 0.7:
            # Complex query - prefer DeepSeek if available
            if "deepseek" in available_providers:
                primary_provider = "deepseek"
                primary_model = "deepseek-v3"
            else:
                primary_provider = available_providers[0]
                primary_model = None

        elif request.domain == "multimodal":
            # Multimodal - prefer Gemini if available
            if "gemini" in available_providers:
                primary_provider = "gemini"
                primary_model = "gemini-pro-vision"
            else:
                primary_provider = available_providers[0]
                primary_model = None

        else:
            # General query - prefer Llama for cost efficiency
            if "llama" in available_providers:
                primary_provider = "llama"
                primary_model = "llama3.3:70b"
            else:
                primary_provider = available_providers[0]
                primary_model = None

        # Create fallback list
        fallback_providers = [p for p in available_providers if p != primary_provider]

        return {
            "primary_model": {"provider": primary_provider, "name": primary_model},
            "fallback_models": [
                {"provider": fp, "name": None} for fp in fallback_providers[:2]
            ],
        }

    async def _get_provider_health(self, provider_name: str) -> ProviderHealthStatus:
        """Get provider health status with caching."""

        now = datetime.now()
        last_check = self.registry.last_health_check.get(provider_name)

        # Check if we need to refresh health status
        if not last_check or now - last_check > timedelta(
            seconds=self.health_check_interval
        ):

            provider = self.registry.providers.get(provider_name)
            if provider:
                try:
                    health_status = await provider.health_check()
                    self.registry.health_status[provider_name] = health_status
                    self.registry.last_health_check[provider_name] = now
                except Exception as e:
                    logger.error(
                        "Health check failed",
                        provider_name=provider_name,
                        error=str(e),
                        exc_info=True,
                    )
                    # Create error status
                    health_status = ProviderHealthStatus(
                        provider_name=provider_name, healthy=False, last_error=str(e)
                    )
                    self.registry.health_status[provider_name] = health_status

        return self.registry.health_status.get(
            provider_name,
            ProviderHealthStatus(provider_name=provider_name, healthy=False),
        )

    def _create_failure_response(
        self, request: ModelRequest, error_message: str
    ) -> ModelResponse:
        """Create a failure response."""
        return ModelResponse(
            request_id=request.request_id,
            provider="router",
            success=False,
            error_message=error_message,
            error_type="routing_failure",
            confidence_score=0.0,
        )

    async def get_available_models(self) -> dict[str, list[str]]:
        """Get all available models from all providers."""

        available_models = {}

        for provider_name, provider in self.registry.providers.items():
            health_status = await self._get_provider_health(provider_name)

            if health_status.healthy:
                available_models[provider_name] = provider.supported_models

        return available_models

    async def get_model_info(
        self, provider_name: str, model_name: str
    ) -> dict[str, Any] | None:
        """Get detailed information about a specific model."""

        provider = self.registry.providers.get(provider_name)
        if not provider:
            return None

        return provider.get_model_info(model_name)

    async def get_provider_metrics(self) -> dict[str, Any]:
        """Get metrics for all providers."""

        providers_dict: dict[str, Any] = {}
        metrics: dict[str, Any] = {
            "router": {
                "total_requests": self.request_count,
                "successful_requests": self.success_count,
                "fallback_requests": self.fallback_count,
                "success_rate": self.success_count / max(self.request_count, 1),
            },
            "providers": providers_dict,
        }

        for provider_name, provider in self.registry.providers.items():
            provider_metrics: dict[str, Any] = provider.get_metrics()
            health_status = self.registry.health_status.get(provider_name)

            if health_status:
                provider_metrics["health"] = {
                    "healthy": health_status.healthy,
                    "last_check": health_status.last_check,
                    "api_status": health_status.api_status,
                    "error_rate": health_status.error_rate,
                }

            metrics["providers"][provider_name] = provider_metrics

        return metrics

    async def health_check_all_providers(self) -> dict[str, ProviderHealthStatus]:
        """Perform health check on all providers."""

        health_results = {}

        for provider_name in self.registry.providers:
            health_results[provider_name] = await self._get_provider_health(
                provider_name
            )

        return health_results

    async def stream_response(
        self, request: ModelRequest, routing_decision: dict[str, Any] | None = None
    ) -> AsyncGenerator[str, None]:
        """Stream response using optimal provider."""

        try:
            # Determine routing if not provided
            if not routing_decision:
                routing_decision = await self._make_routing_decision(request)

            # Extract routing information
            primary_provider = routing_decision.get("primary_model", {}).get("provider")
            primary_model = routing_decision.get("primary_model", {}).get("name")

            provider = self.registry.providers.get(primary_provider)
            if not provider:
                yield f"Error: Provider {primary_provider} not available"
                return

            # Check provider health
            health_status = await self._get_provider_health(primary_provider)
            if not health_status.healthy:
                yield f"Error: Provider {primary_provider} is unhealthy"
                return

            # Stream response
            async for chunk in provider.stream(request, primary_model):
                yield chunk

        except Exception as e:
            logger.error("Streaming failed", error=str(e), exc_info=True)
            yield f"Error: {e!s}"

    def add_provider(self, provider_name: str, provider_config: dict[str, Any]) -> None:
        """Dynamically add a new provider."""

        try:
            provider_class = self.provider_classes.get(provider_name)
            if not provider_class:
                raise ValueError(f"Unknown provider type: {provider_name}")

            provider = provider_class(provider_config)

            self.registry.providers[provider_name] = provider
            self.registry.provider_configs[provider_name] = provider_config

            logger.info("Added provider", provider_name=provider_name)

        except Exception as e:
            logger.error(
                "Failed to add provider",
                provider_name=provider_name,
                error=str(e),
                exc_info=True,
            )
            raise

    def remove_provider(self, provider_name: str) -> None:
        """Remove a provider from the registry."""

        if provider_name in self.registry.providers:
            del self.registry.providers[provider_name]
            del self.registry.provider_configs[provider_name]

            if provider_name in self.registry.health_status:
                del self.registry.health_status[provider_name]

            if provider_name in self.registry.last_health_check:
                del self.registry.last_health_check[provider_name]

            logger.info("Removed provider", provider_name=provider_name)

    async def close(self) -> None:
        """Clean up all provider resources."""

        for provider in self.registry.providers.values():
            if hasattr(provider, "close"):
                try:
                    await provider.close()
                except Exception as e:
                    logger.warning("Error closing provider", error=str(e))


__all__ = ["ModelRouter", "ProviderRegistry", "RoutingDecision"]
