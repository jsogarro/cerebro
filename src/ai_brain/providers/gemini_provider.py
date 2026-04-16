"""
Gemini Provider Implementation

Integrates with Google's Gemini models for multimodal capabilities and
reliable general-purpose text generation. Builds on the existing Gemini
service in the research platform.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any, AsyncGenerator
from datetime import datetime

from .base_provider import (
    BaseProvider,
    ModelRequest,
    ModelResponse,
    ModelCapability,
    ResponseFormat,
)

# Import existing Gemini service from research platform
try:
    from src.services.gemini_service import GeminiService
except ImportError:
    # Fallback if not available
    GeminiService = None

logger = logging.getLogger(__name__)


class GeminiProvider(BaseProvider):
    """
    Google Gemini model provider for multimodal and general-purpose tasks.

    Builds on the existing research platform's Gemini integration while
    extending it to support the AI Brain's routing and optimization features.

    Gemini excels at:
    - Multimodal understanding (text, images, documents)
    - General-purpose text generation
    - Reliable and consistent performance
    - Good balance of cost and quality
    """

    def __init__(self, config: Dict[str, Any]):
        """Initialize Gemini provider."""
        super().__init__(config)

        # Gemini-specific configuration
        self.api_key = config.get("api_key")
        self.default_model = config.get("default_model", "gemini-pro")

        if not self.api_key:
            raise ValueError("Gemini API key is required")

        # Initialize underlying Gemini service if available
        if GeminiService:
            gemini_config = {
                "api_key": self.api_key,
                "model": self.default_model,
                **config.get("gemini_service_config", {}),
            }
            self.gemini_service = GeminiService(gemini_config)
        else:
            logger.warning("GeminiService not available, using direct API calls")
            self.gemini_service = None

        # Model specifications
        self.model_specs = {
            "gemini-pro": {
                "context_window": 100000,
                "cost_per_1k_tokens": 0.001,
                "max_output_tokens": 8000,
                "strengths": ["general_purpose", "reliable", "balanced"],
            },
            "gemini-pro-vision": {
                "context_window": 100000,
                "cost_per_1k_tokens": 0.0015,
                "max_output_tokens": 8000,
                "strengths": ["multimodal", "vision", "image_analysis"],
            },
            "gemini-ultra": {
                "context_window": 100000,
                "cost_per_1k_tokens": 0.003,
                "max_output_tokens": 8000,
                "strengths": ["high_quality", "complex_reasoning", "premium"],
            },
        }

    def _get_provider_name(self) -> str:
        return "gemini"

    def _get_supported_capabilities(self) -> List[ModelCapability]:
        return [
            ModelCapability.TEXT_GENERATION,
            ModelCapability.CHAT,
            ModelCapability.REASONING,
            ModelCapability.ANALYSIS,
            ModelCapability.MULTIMODAL,
        ]

    def _get_supported_models(self) -> List[str]:
        return list(self.model_specs.keys())

    def _get_model_context_window(self, model_name: str) -> int:
        return self.model_specs.get(model_name, {}).get("context_window", 100000)

    def _get_model_cost(self, model_name: str) -> float:
        return self.model_specs.get(model_name, {}).get("cost_per_1k_tokens", 0.001)

    async def generate(
        self, request: ModelRequest, model_name: Optional[str] = None
    ) -> ModelResponse:
        """Generate response using Gemini models."""

        if not await self.validate_request(request):
            return self._create_error_response(
                request, ValueError("Invalid request"), "validation_error"
            )

        model_name = model_name or self.default_model

        if not self.supports_model(model_name):
            return self._create_error_response(
                request, ValueError(f"Unsupported model: {model_name}"), "model_error"
            )

        start_time = datetime.now()

        try:
            # Use existing Gemini service if available
            if self.gemini_service:
                response = await self._generate_via_service(
                    request, model_name, start_time
                )
            else:
                response = await self._generate_via_direct_api(
                    request, model_name, start_time
                )

            return await self._postprocess_response(response, request)

        except Exception as e:
            logger.error(f"Gemini generation failed: {e}")
            return self._create_error_response(request, e, "generation_error")

    async def _generate_via_service(
        self, request: ModelRequest, model_name: str, start_time: datetime
    ) -> ModelResponse:
        """Generate using existing GeminiService."""

        # Convert ModelRequest to format expected by GeminiService
        if request.messages:
            # Use chat format
            prompt = self._convert_messages_to_prompt(request.messages)
        else:
            prompt = request.prompt

        # Add system prompt if provided
        if request.system_prompt:
            prompt = f"{request.system_prompt}\n\n{prompt}"

        # Call existing service
        try:
            # This would use the existing GeminiService.generate method
            service_response = await self.gemini_service.generate(
                prompt=prompt,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            )

            # Convert service response to ModelResponse
            end_time = datetime.now()
            latency_ms = int((end_time - start_time).total_seconds() * 1000)

            # Estimate token usage (Gemini service might provide this)
            prompt_tokens = len(prompt.split()) * 1.3
            completion_tokens = len(service_response.split()) * 1.3
            total_tokens = prompt_tokens + completion_tokens

            cost = self._calculate_cost(prompt_tokens, completion_tokens, model_name)
            confidence = self._calculate_confidence_score(service_response, request)

            return ModelResponse(
                request_id=request.request_id,
                content=service_response,
                model_name=model_name,
                provider=self.provider_name,
                completion_tokens=int(completion_tokens),
                prompt_tokens=int(prompt_tokens),
                total_tokens=int(total_tokens),
                latency_ms=latency_ms,
                processing_time_ms=latency_ms,
                confidence_score=confidence,
                success=True,
                cost_estimate=cost,
                finish_reason="completed",
            )

        except Exception as e:
            logger.error(f"Gemini service call failed: {e}")
            raise

    async def _generate_via_direct_api(
        self, request: ModelRequest, model_name: str, start_time: datetime
    ) -> ModelResponse:
        """Generate using direct API calls (fallback)."""

        # This would implement direct Google AI API calls
        # For now, return a placeholder response
        end_time = datetime.now()
        latency_ms = int((end_time - start_time).total_seconds() * 1000)

        return ModelResponse(
            request_id=request.request_id,
            content="Direct API not implemented - please configure GeminiService",
            model_name=model_name,
            provider=self.provider_name,
            success=False,
            error_message="Direct API not implemented",
            error_type="not_implemented",
            latency_ms=latency_ms,
        )

    def _convert_messages_to_prompt(self, messages: List[Dict[str, str]]) -> str:
        """Convert chat messages to a single prompt for Gemini."""
        prompt_parts = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                prompt_parts.append(f"System: {content}")
            elif role == "user":
                prompt_parts.append(f"User: {content}")
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {content}")

        return "\n\n".join(prompt_parts)

    def _calculate_confidence_score(self, content: str, request: ModelRequest) -> float:
        """Calculate confidence score for Gemini response."""

        base_confidence = 0.85  # Base confidence for Gemini (reliable)

        # Adjust based on response quality indicators
        if len(content) < 10:
            base_confidence -= 0.2
        elif len(content) > 100:
            base_confidence += 0.05

        # Gemini is generally reliable across task types
        if request.complexity_score > 0.7:
            base_confidence -= 0.05  # Slight decrease for very complex tasks

        # Multimodal requests (if supported)
        if "vision" in request.domain or "image" in (request.prompt or "").lower():
            if "gemini-pro-vision" in request.metadata.get("model_name", ""):
                base_confidence += 0.1  # Gemini Vision is strong

        return min(max(base_confidence, 0.0), 1.0)

    async def stream(
        self, request: ModelRequest, model_name: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """Stream response using Gemini models."""

        # Gemini typically doesn't support streaming in the same way
        # Fall back to generating full response and yielding it
        model_name = model_name or self.default_model

        try:
            response = await self.generate(request, model_name)

            if response.success:
                # Simulate streaming by yielding chunks
                content = response.content
                chunk_size = 20  # words per chunk
                words = content.split()

                for i in range(0, len(words), chunk_size):
                    chunk_words = words[i : i + chunk_size]
                    chunk = " ".join(chunk_words)
                    yield chunk + " "

                    # Small delay to simulate streaming
                    await asyncio.sleep(0.1)
            else:
                yield f"Error: {response.error_message}"

        except Exception as e:
            logger.error(f"Gemini streaming failed: {e}")
            yield f"Error: {str(e)}"

    async def health_check(self) -> "ProviderHealthStatus":
        """Perform Gemini-specific health check."""

        try:
            # Test with a simple request
            test_request = ModelRequest(
                prompt="What is 1 + 1?", max_tokens=10, timeout_seconds=15
            )

            start_time = datetime.now()
            response = await self.generate(test_request)
            latency = (datetime.now() - start_time).total_seconds() * 1000

            # Update health status
            self.health_status.healthy = response.success
            self.health_status.last_check = datetime.now()
            self.health_status.avg_latency_ms = latency

            # Gemini-specific health indicators
            if response.success:
                # Check for reasonable response
                if "2" in response.content:
                    self.health_status.api_status = "operational"
                else:
                    self.health_status.api_status = "degraded"
            else:
                self.health_status.api_status = "error"
                self.health_status.last_error = response.error_message

        except Exception as e:
            logger.error(f"Gemini health check failed: {e}")
            self.health_status.healthy = False
            self.health_status.last_error = str(e)
            self.health_status.api_status = "error"

        return self.health_status

    async def validate_request(self, request: ModelRequest) -> bool:
        """Validate Gemini-specific request requirements."""

        if not await super().validate_request(request):
            return False

        # Gemini-specific validations
        if request.max_tokens > 8000:
            return False  # Gemini output limit

        # Check context window usage
        estimated_tokens = len((request.prompt or "").split()) * 1.3
        if estimated_tokens > 100000:  # Gemini context limit
            return False

        return True

    def get_model_info(self, model_name: str) -> Optional[Dict[str, Any]]:
        """Get Gemini model information."""

        if not self.supports_model(model_name):
            return None

        spec = self.model_specs.get(model_name, {})

        base_info = {
            "name": model_name,
            "provider": self.provider_name,
            "capabilities": self.supported_capabilities,
            "context_window": spec.get("context_window"),
            "cost_per_1k_tokens": spec.get("cost_per_1k_tokens"),
            "max_output_tokens": spec.get("max_output_tokens"),
            "strengths": spec.get("strengths", []),
            "limitations": [
                "No streaming support",
                "Rate limited API",
                "Less specialized than domain-specific models",
            ],
        }

        # Add model-specific information
        if model_name == "gemini-pro":
            base_info["best_for"] = [
                "General text generation",
                "Balanced cost/performance",
                "Reliable responses",
                "Research tasks",
            ]
        elif model_name == "gemini-pro-vision":
            base_info["best_for"] = [
                "Image analysis",
                "Multimodal tasks",
                "Document processing",
                "Visual reasoning",
            ]
            base_info["capabilities"].append(ModelCapability.MULTIMODAL)
        elif model_name == "gemini-ultra":
            base_info["best_for"] = [
                "Complex reasoning",
                "High-quality outputs",
                "Critical applications",
                "Premium tasks",
            ]

        return base_info

    def supports_multimodal(self, model_name: str) -> bool:
        """Check if a specific model supports multimodal inputs."""
        return "vision" in model_name or "ultra" in model_name

    def get_rate_limits(self) -> Dict[str, int]:
        """Get current rate limits for Gemini API."""
        return {
            "requests_per_minute": 60,
            "requests_per_day": 1500,
            "tokens_per_minute": 100000,
        }


__all__ = ["GeminiProvider"]
