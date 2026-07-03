"""
OpenRouter Provider Implementation

Provides unified multi-provider routing through OpenRouter's OpenAI-compatible API.
Enables access to Claude, Llama, DeepSeek, Gemini, and other models through a single
integration point with graceful fallback to GeminiService when unavailable.
"""

from collections.abc import AsyncGenerator
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
from structlog import get_logger

from .base_provider import (
    BaseProvider,
    ModelCapability,
    ModelRequest,
    ModelResponse,
    ProviderHealthStatus,
)

if TYPE_CHECKING:
    from ..config.model_config_manager import ModelConfigManager

logger = get_logger(__name__)


class OpenRouterProvider(BaseProvider):
    """
    OpenRouter multi-provider integration using OpenAI-compatible API.

    Provides single-key access to Claude, Llama, DeepSeek, Gemini, and other models
    through OpenRouter's unified endpoint. Supports configurable tier mapping for
    intelligent model selection based on MASR routing decisions.

    Key capabilities:
    - Cost-optimized model selection via tier mapping
    - Graceful fallback to GeminiService when unavailable
    - OpenAI-compatible HTTP interface
    - Dynamic model configuration support
    """

    def __init__(
        self,
        config: dict[str, Any],
        model_config_manager: "ModelConfigManager | None" = None,
    ) -> None:
        """Initialize OpenRouter provider."""
        super().__init__(config, model_config_manager)

        # API configuration
        self.api_endpoint = config.get(
            "endpoint", "https://openrouter.ai/api/v1/chat/completions"
        )
        self.api_key = config.get("api_key")

        # HTTP client for OpenRouter API
        self.client: httpx.AsyncClient | None = None

        # Tier mapping: maps cost optimization tier -> OpenRouter model ID
        # Default mapping provides sensible model choices per tier
        self.tier_mapping = config.get(
            "tier_mapping",
            {
                "simple": "deepseek/deepseek-chat",  # Cost-minimized
                "balanced": "anthropic/claude-sonnet-4.6",  # Mid-tier
                "complex": "anthropic/claude-sonnet-4.6",  # Quality-focused
            },
        )

        # Legacy model specifications (backward compatibility)
        self._legacy_model_specs = {
            "deepseek/deepseek-chat": {
                "context_window": 200000,
                "cost_per_1k_tokens": 0.002,
                "max_output_tokens": 8000,
                "strengths": ["cost_efficient", "fast", "general"],
            },
            "anthropic/claude-sonnet-4.6": {
                "context_window": 200000,
                "cost_per_1k_tokens": 0.015,
                "max_output_tokens": 8000,
                "strengths": ["quality", "reasoning", "complex"],
            },
            "google/gemini-pro-1.5": {
                "context_window": 100000,
                "cost_per_1k_tokens": 0.001,
                "max_output_tokens": 8000,
                "strengths": ["multimodal", "balanced", "reliable"],
            },
        }

    def _get_provider_name(self) -> str:
        return "openrouter"

    def _get_supported_capabilities_legacy(self) -> list[ModelCapability]:
        """Legacy hard-coded capabilities for backward compatibility."""
        return [
            ModelCapability.TEXT_GENERATION,
            ModelCapability.CHAT,
            ModelCapability.CODE_GENERATION,
            ModelCapability.REASONING,
            ModelCapability.ANALYSIS,
            ModelCapability.MULTIMODAL,
        ]

    def _get_supported_models_legacy(self) -> list[str]:
        """Legacy hard-coded models for backward compatibility."""
        return list(self._legacy_model_specs.keys())

    async def load_configuration(self) -> None:
        """Load OpenRouter-specific configuration."""
        # Load base configuration
        await super().load_configuration()

        # Configure API settings from provider configuration
        if self._provider_config:
            self.api_endpoint = self._provider_config.api_endpoint

            # Get API key from environment if specified
            if self._provider_config.api_key_env:
                import os

                self.api_key = os.getenv(self._provider_config.api_key_env)

            # Configure HTTP client with provider settings
            timeout_ms = getattr(self._provider_config, "timeout_ms", 60000)
            pool_size = getattr(self._provider_config, "connection_pool_size", 10)

            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_ms / 1000.0),
                limits=httpx.Limits(
                    max_keepalive_connections=pool_size, max_connections=pool_size * 2
                ),
            )

        if not self.api_key:
            logger.warning(
                "OpenRouter API key not configured - provider will not function"
            )

        if not self.client:
            # Fallback client configuration
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0),
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=100),
            )

    def _get_model_context_window_legacy(self, model_name: str) -> int:
        """Legacy method using hard-coded specifications."""
        spec = self._legacy_model_specs.get(model_name, {})
        context_window: Any = spec.get("context_window", 100000)
        return int(context_window)

    def _get_model_cost_legacy(self, model_name: str) -> float:
        """Legacy method using hard-coded specifications."""
        spec = self._legacy_model_specs.get(model_name, {})
        cost: Any = spec.get("cost_per_1k_tokens", 0.002)
        return float(cost)

    def _select_model_from_tier(self, tier: str | None) -> str:
        """Select OpenRouter model ID based on routing tier.

        Maps cost optimization tiers (simple/balanced/complex) to specific
        OpenRouter model IDs via configurable tier_mapping.

        Args:
            tier: Routing tier from MASR optimization (simple/balanced/complex)

        Returns:
            OpenRouter model ID string
        """
        if tier and tier in self.tier_mapping:
            return str(self.tier_mapping[tier])
        # Default to balanced tier
        default_model: str = str(
            self.tier_mapping.get("balanced", "anthropic/claude-sonnet-4.6")
        )
        return default_model

    async def generate(
        self, request: ModelRequest, model_name: str | None = None
    ) -> ModelResponse:
        """Generate response using OpenRouter API.

        Args:
            request: ModelRequest with query and parameters
            model_name: Optional explicit model override

        Returns:
            ModelResponse with generated content and metadata
        """
        # Ensure configuration is loaded
        await self.ensure_configuration_loaded()

        if not await self.validate_request(request):
            return self._create_error_response(
                request, ValueError("Invalid request"), "validation_error"
            )

        # Determine model to use
        if model_name:
            # Explicit model override
            selected_model = model_name
        else:
            # Map from optimization tier if available
            tier = (
                request.metadata.get("tier") if hasattr(request, "metadata") else None
            )
            selected_model = self._select_model_from_tier(tier)

        start_time = datetime.now()

        try:
            # Build OpenAI-compatible request payload
            payload = self._build_request_payload(request, selected_model)

            # Make API request
            response_data = await self._make_api_request(payload)

            # Process response
            model_response = self._process_api_response(
                response_data, request, selected_model, start_time
            )

            return await self._postprocess_response(model_response, request)

        except Exception as e:
            logger.error("OpenRouter generation failed", error=str(e), exc_info=True)
            return self._create_error_response(request, e, "generation_error")

    def _build_request_payload(
        self, request: ModelRequest, model_name: str
    ) -> dict[str, Any]:
        """Build OpenAI-compatible request payload for OpenRouter API."""
        # Convert to chat messages format
        messages = []

        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})

        if request.messages:
            # Already in messages format
            messages.extend(request.messages)
        elif request.prompt:
            # Convert prompt to message
            messages.append({"role": "user", "content": request.prompt})

        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "top_p": request.top_p,
        }

        # Add top_k if specified and supported
        if request.top_k is not None:
            payload["top_k"] = request.top_k

        # Add response_format if specified (structured output support)
        # Prefer metadata["response_format"] for OpenAI-compatible dict format
        if hasattr(request, "metadata") and request.metadata.get("response_format"):
            payload["response_format"] = request.metadata["response_format"]

        return payload

    async def _make_api_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Make HTTP request to OpenRouter API.

        Args:
            payload: OpenAI-compatible request payload

        Returns:
            Parsed JSON response

        Raises:
            Exception: On API errors or network failures
        """
        if not self.client:
            raise RuntimeError("HTTP client not initialized")

        if not self.api_key:
            raise ValueError("OpenRouter API key not configured")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://cerebro-ai.internal",
            "X-Title": "Cerebro AI Brain",
        }

        response = await self.client.post(
            self.api_endpoint, headers=headers, json=payload
        )

        response.raise_for_status()
        json_response: dict[str, Any] = response.json()
        return json_response

    def _process_api_response(
        self,
        response_data: dict[str, Any],
        request: ModelRequest,
        model_name: str,
        start_time: datetime,
    ) -> ModelResponse:
        """Process OpenRouter API response into ModelResponse.

        Args:
            response_data: Parsed JSON response from OpenRouter
            request: Original ModelRequest
            model_name: Model ID used for generation
            start_time: Request start timestamp

        Returns:
            ModelResponse with parsed content and metadata
        """
        end_time = datetime.now()
        latency_ms = int((end_time - start_time).total_seconds() * 1000)

        # Extract content from OpenAI-compatible response
        choices = response_data.get("choices", [])
        if not choices:
            return self._create_error_response(
                request, ValueError("No choices in response"), "empty_response"
            )

        message = choices[0].get("message", {})
        content = message.get("content", "")

        # Extract token usage
        usage = response_data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

        # Calculate cost
        cost = self._calculate_cost(prompt_tokens, completion_tokens, model_name)

        # Estimate confidence based on response characteristics
        confidence = self._estimate_confidence(content, request)

        # Extract finish reason
        finish_reason = choices[0].get("finish_reason", "completed")

        return ModelResponse(
            request_id=request.request_id,
            content=content,
            model_name=model_name,
            provider=self.provider_name,
            completion_tokens=completion_tokens,
            prompt_tokens=prompt_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            processing_time_ms=latency_ms,
            confidence_score=confidence,
            success=True,
            cost_estimate=cost,
            finish_reason=finish_reason,
        )

    def _estimate_confidence(self, content: str, request: ModelRequest) -> float:
        """Estimate confidence score for the response.

        Simple heuristic based on response characteristics.
        More sophisticated scoring could incorporate model-specific signals.

        Args:
            content: Generated content
            request: Original request

        Returns:
            Confidence score between 0.0 and 1.0
        """
        base_confidence = 0.85

        # Adjust based on response length
        if len(content) < 10:
            base_confidence -= 0.2
        elif len(content) > 100:
            base_confidence += 0.05

        # Adjust based on complexity
        if request.complexity_score > 0.7:
            base_confidence -= 0.05

        return min(max(base_confidence, 0.0), 1.0)

    async def stream(
        self, request: ModelRequest, model_name: str | None = None
    ) -> AsyncGenerator[str, None]:
        """Stream response - not implemented for OpenRouter.

        Falls back to non-streaming generation and yields the full result.

        Args:
            request: ModelRequest with query and parameters
            model_name: Optional explicit model override

        Yields:
            Response content as a single chunk
        """
        response = await self.generate(request, model_name)

        if response.success:
            yield response.content
        else:
            yield f"Error: {response.error_message}"

    async def health_check(self) -> ProviderHealthStatus:
        """Perform OpenRouter-specific health check.

        Tests API connectivity with a simple generation request.

        Returns:
            ProviderHealthStatus with current health information
        """
        try:
            # Test with a simple request
            test_request = ModelRequest(
                prompt="What is 2+2?",
                max_tokens=10,
                timeout_seconds=15,
                metadata={"tier": "simple"},  # health probe uses the cheap tier
            )

            start_time = datetime.now()
            response = await self.generate(test_request)
            latency = (datetime.now() - start_time).total_seconds() * 1000

            # Update health status
            self.health_status.healthy = response.success
            self.health_status.last_check = datetime.now()
            self.health_status.avg_latency_ms = latency

            if response.success:
                # Check for reasonable response
                if "4" in response.content:
                    self.health_status.api_status = "operational"
                else:
                    self.health_status.api_status = "degraded"
            else:
                self.health_status.api_status = "error"
                self.health_status.last_error = response.error_message

        except Exception as e:
            logger.error("OpenRouter health check failed", error=str(e), exc_info=True)
            self.health_status.healthy = False
            self.health_status.last_error = str(e)
            self.health_status.api_status = "error"

        return self.health_status

    async def validate_request(self, request: ModelRequest) -> bool:
        """Validate OpenRouter-specific request requirements.

        Args:
            request: ModelRequest to validate

        Returns:
            True if request is valid, False otherwise
        """
        if not await super().validate_request(request):
            return False

        # OpenRouter-specific validations
        if request.max_tokens > 8000:
            return False  # General output limit for most models

        # Check context window usage (conservative estimate)
        estimated_tokens = len((request.prompt or "").split()) * 1.3
        return estimated_tokens <= 100000

    async def close(self) -> None:
        """Clean up HTTP client resources."""
        if self.client:
            await self.client.aclose()
            self.client = None


__all__ = ["OpenRouterProvider"]
