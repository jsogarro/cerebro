"""
DeepSeek Provider Implementation

Provides integration with DeepSeek-V3 models for high-quality reasoning,
mathematical computation, and analytical tasks. DeepSeek-V3 is particularly
strong at complex reasoning and analysis tasks.
"""

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config.model_config_manager import ModelConfigManager
from collections.abc import AsyncGenerator
from datetime import datetime

import httpx
from structlog import get_logger

from .base_provider import (
    BaseProvider,
    ModelCapability,
    ModelRequest,
    ModelResponse,
    ProviderHealthStatus,
    ResponseFormat,
)

logger = get_logger(__name__)


class DeepSeekProvider(BaseProvider):
    """
    DeepSeek-V3 model provider for complex reasoning and analysis tasks.

    DeepSeek-V3 excels at:
    - Mathematical reasoning and computation
    - Code analysis and generation
    - Complex analytical tasks
    - Multi-step problem solving
    - Scientific and technical reasoning
    """

    def __init__(
        self,
        config: dict[str, Any],
        model_config_manager: "ModelConfigManager | None" = None,
    ) -> None:
        """Initialize DeepSeek provider."""
        super().__init__(config, model_config_manager)

        # Initialize API configuration - will be updated from dynamic config
        self.api_endpoint = config.get("endpoint", "https://api.deepseek.com/v1")
        self.api_key = config.get("api_key")
        self.default_model = config.get("default_model", "deepseek-v3")

        # HTTP client will be configured after loading provider config
        self.client: httpx.AsyncClient | None = None

        # Legacy model specifications (for backward compatibility)
        self._legacy_model_specs = {
            "deepseek-v3": {
                "context_window": 200000,
                "cost_per_1k_tokens": 0.002,
                "max_output_tokens": 8000,
                "strengths": ["reasoning", "math", "analysis", "code"],
            },
            "deepseek-coder": {
                "context_window": 200000,
                "cost_per_1k_tokens": 0.0015,
                "max_output_tokens": 8000,
                "strengths": ["code", "programming", "debugging"],
            },
            "deepseek-math": {
                "context_window": 100000,
                "cost_per_1k_tokens": 0.001,
                "max_output_tokens": 4000,
                "strengths": ["math", "calculations", "proofs"],
            },
        }

    def _get_provider_name(self) -> str:
        return "deepseek"

    def _get_supported_capabilities_legacy(self) -> list[ModelCapability]:
        """Legacy hard-coded capabilities for backward compatibility."""
        return [
            ModelCapability.TEXT_GENERATION,
            ModelCapability.CHAT,
            ModelCapability.CODE_GENERATION,
            ModelCapability.REASONING,
            ModelCapability.ANALYSIS,
            ModelCapability.STREAMING,
        ]

    def _get_supported_models_legacy(self) -> list[str]:
        """Legacy hard-coded models for backward compatibility."""
        return list(self._legacy_model_specs.keys())

    async def load_configuration(self) -> None:
        """Load DeepSeek-specific configuration."""

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
                "DeepSeek API key not configured - provider will not function"
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
        context_window: Any = spec.get("context_window", 200000)
        return int(context_window)

    def _get_model_cost_legacy(self, model_name: str) -> float:
        """Legacy method using hard-coded specifications."""
        spec = self._legacy_model_specs.get(model_name, {})
        cost: Any = spec.get("cost_per_1k_tokens", 0.002)
        return float(cost)

    async def generate(
        self, request: ModelRequest, model_name: str | None = None
    ) -> ModelResponse:
        """Generate response using DeepSeek models."""

        # Ensure configuration is loaded
        await self.ensure_configuration_loaded()

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
            # Prepare request payload
            payload = self._build_request_payload(request, model_name)

            # Make API request
            response = await self._make_api_request(payload)

            # Process response
            model_response = await self._process_api_response(
                response, request, model_name, start_time
            )

            return await self._postprocess_response(model_response, request)

        except Exception as e:
            logger.error("DeepSeek generation failed", error=str(e), exc_info=True)
            return self._create_error_response(request, e, "generation_error")

    async def stream(
        self, request: ModelRequest, model_name: str | None = None
    ) -> AsyncGenerator[str, None]:
        """Stream response using DeepSeek models."""

        model_name = model_name or self.default_model

        if not self.supports_model(model_name):
            raise ValueError(f"Unsupported model: {model_name}")

        # Prepare streaming request
        payload = self._build_request_payload(request, model_name)
        payload["stream"] = True

        if not self.client:
            raise ValueError("HTTP client not initialized")

        try:
            async with self.client.stream(
                "POST",
                f"{self.api_endpoint}/chat/completions",
                headers=self._get_headers(),
                json=payload,
            ) as response:

                if response.status_code != 200:
                    error_bytes = await response.aread()
                    error_text = error_bytes.decode("utf-8")
                    raise Exception(f"API error: {response.status_code} - {error_text}")

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]  # Remove "data: " prefix

                        if data == "[DONE]":
                            break

                        try:
                            chunk = json.loads(data)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")

                            if content:
                                yield content

                        except json.JSONDecodeError:
                            continue  # Skip invalid JSON chunks

        except Exception as e:
            logger.error("DeepSeek streaming failed", error=str(e), exc_info=True)
            yield f"Error: {e!s}"

    def _build_request_payload(self, request: ModelRequest, model_name: str) -> dict[str, Any]:
        """Build API request payload."""

        # Convert messages or prompt to chat format
        if request.messages:
            messages = request.messages.copy()
        else:
            messages = [{"role": "user", "content": request.prompt}]

        # Add system message if provided
        if request.system_prompt:
            messages.insert(0, {"role": "system", "content": request.system_prompt})

        # Get max tokens from configuration or legacy specs
        max_tokens = request.max_tokens
        if self._config_loaded and model_name in self._model_specs:
            configured_max: int = self._model_specs[model_name].max_output_tokens
            max_tokens = min(request.max_tokens, configured_max)
        else:
            # Fallback to legacy specs
            legacy_max_obj = self._legacy_model_specs.get(model_name, {}).get(
                "max_output_tokens", 8000
            )
            legacy_max = legacy_max_obj if isinstance(legacy_max_obj, int) else 8000
            max_tokens = min(request.max_tokens, legacy_max)

        # Build payload
        payload = {
            "model": model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": request.temperature,
            "top_p": request.top_p,
            "stream": request.stream,
        }

        # Add DeepSeek-specific parameters
        if request.response_format == ResponseFormat.JSON:
            payload["response_format"] = {"type": "json_object"}

        return payload

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers for API requests."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Cerebro-AI-Brain/2.0",
        }

    async def _make_api_request(self, payload: dict[str, Any]) -> httpx.Response:
        """Make API request to DeepSeek."""

        if not self.client:
            raise ValueError("HTTP client not initialized")

        response = await self.client.post(
            f"{self.api_endpoint}/chat/completions",
            headers=self._get_headers(),
            json=payload,
        )

        if response.status_code != 200:
            error_bytes = await response.aread()
            error_detail = error_bytes.decode("utf-8")
            raise Exception(
                f"DeepSeek API error: {response.status_code} - {error_detail}"
            )

        return response

    async def _process_api_response(
        self,
        response: httpx.Response,
        request: ModelRequest,
        model_name: str,
        start_time: datetime,
    ) -> ModelResponse:
        """Process API response into ModelResponse."""

        response_data = response.json()

        # Extract response content
        choices = response_data.get("choices", [])
        if not choices:
            raise Exception("No response choices returned")

        content = choices[0].get("message", {}).get("content", "")
        finish_reason = choices[0].get("finish_reason", "completed")

        # Extract usage information
        usage = response_data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

        # Calculate metrics
        end_time = datetime.now()
        latency_ms = int((end_time - start_time).total_seconds() * 1000)
        cost = self._calculate_cost(prompt_tokens, completion_tokens, model_name)

        # Calculate confidence score (heuristic based on response characteristics)
        confidence_score = self._calculate_confidence_score(
            content, finish_reason, request, response_data
        )

        # Parse structured content if JSON response
        structured_content = None
        if request.response_format == ResponseFormat.JSON:
            try:
                structured_content = json.loads(content)
            except json.JSONDecodeError:
                logger.warning("Failed to parse JSON response")

        return ModelResponse(
            request_id=request.request_id,
            content=content,
            structured_content=structured_content,
            model_name=model_name,
            provider=self.provider_name,
            completion_tokens=completion_tokens,
            prompt_tokens=prompt_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            processing_time_ms=latency_ms,  # Approximate for now
            confidence_score=confidence_score,
            success=True,
            cost_estimate=cost,
            finish_reason=finish_reason,
            metadata={
                "api_response_id": response_data.get("id"),
                "model_version": response_data.get("model"),
                "created": response_data.get("created"),
            },
        )

    def _calculate_confidence_score(
        self,
        content: str,
        finish_reason: str,
        request: ModelRequest,
        response_data: dict[str, Any],
    ) -> float:
        """Calculate confidence score for the response."""

        base_confidence = 0.8  # Base confidence for DeepSeek

        # Adjust based on finish reason
        if finish_reason == "stop":
            base_confidence += 0.1
        elif finish_reason == "length":
            base_confidence -= 0.1  # Truncated response

        # Adjust based on response length and request
        if len(content) < 10:
            base_confidence -= 0.2  # Very short response might be incomplete

        # Adjust based on complexity requirements
        if request.complexity_score > 0.8:
            # For complex queries, DeepSeek is very capable
            base_confidence += 0.1

        # Check for mathematical content (DeepSeek's strength)
        if any(
            keyword in content.lower()
            for keyword in ["equation", "formula", "calculate", "proof", "theorem"]
        ):
            base_confidence += 0.1

        return min(max(base_confidence, 0.0), 1.0)

    async def health_check(self) -> "ProviderHealthStatus":
        """Perform DeepSeek-specific health check."""

        try:
            # Test with a simple mathematical reasoning task
            test_request = ModelRequest(
                prompt="What is 2 + 2? Explain your reasoning.",
                max_tokens=50,
                timeout_seconds=15,
            )

            start_time = datetime.now()
            response = await self.generate(test_request)
            latency = (datetime.now() - start_time).total_seconds() * 1000

            # Update health status
            self.health_status.healthy = response.success
            self.health_status.last_check = datetime.now()
            self.health_status.avg_latency_ms = latency

            # DeepSeek-specific health indicators
            if response.success:
                # Check if response demonstrates reasoning capability
                if "4" in response.content and len(response.content) > 10:
                    self.health_status.api_status = "operational"
                else:
                    self.health_status.api_status = "degraded"

        except Exception as e:
            logger.error("DeepSeek health check failed", error=str(e), exc_info=True)
            self.health_status.healthy = False
            self.health_status.last_error = str(e)
            self.health_status.api_status = "error"

        return self.health_status

    async def validate_request(self, request: ModelRequest) -> bool:
        """Validate DeepSeek-specific request requirements."""

        if not await super().validate_request(request):
            return False

        # DeepSeek-specific validations
        if request.max_tokens > 8000:
            return False  # DeepSeek max output limit

        # Check context window usage
        estimated_tokens = len((request.prompt or "").split()) * 1.3
        return estimated_tokens <= 200000  # DeepSeek context limit

    def get_model_info(self, model_name: str) -> dict[str, Any] | None:
        """Get DeepSeek model information."""

        if not self.supports_model(model_name):
            return None

        spec_obj = self._model_specs.get(model_name) if self._config_loaded else None
        if spec_obj is None:
            spec_dict = self._legacy_model_specs.get(model_name, {})
        else:
            spec_dict = None

        return {
            "name": model_name,
            "provider": self.provider_name,
            "capabilities": self.supported_capabilities,
            "context_window": spec_dict.get("context_window") if spec_dict else None,
            "cost_per_1k_tokens": spec_dict.get("cost_per_1k_tokens") if spec_dict else None,
            "max_output_tokens": spec_dict.get("max_output_tokens") if spec_dict else None,
            "strengths": spec_dict.get("strengths", []) if spec_dict else [],
            "best_for": [
                "Mathematical reasoning",
                "Complex analysis",
                "Code generation",
                "Multi-step problem solving",
                "Scientific reasoning",
            ],
            "limitations": [
                "Higher cost than basic models",
                "Slower for simple tasks",
                "May be overkill for basic queries",
            ],
        }

    async def close(self) -> None:
        """Clean up resources."""
        if self.client:
            await self.client.aclose()


__all__ = ["DeepSeekProvider"]
