"""
Llama Provider Implementation

Provides integration with Llama 3.3 70B model via Ollama for cost-effective
general-purpose language generation. Optimized for high-volume workloads
where cost efficiency is important.
"""

import json
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


class LlamaProvider(BaseProvider):
    """
    Llama 3.3 70B model provider via Ollama for cost-effective generation.

    Llama 3.3 70B excels at:
    - General-purpose text generation
    - Conversational AI
    - Content creation and editing
    - Simple reasoning tasks
    - High-volume processing with cost efficiency
    """

    def __init__(
        self,
        config: dict[str, Any],
        model_config_manager: "ModelConfigManager | None" = None,
    ) -> None:
        """Initialize Llama provider."""
        super().__init__(config, model_config_manager)

        # Initialize configuration - will be updated from dynamic config
        self.ollama_endpoint = config.get("endpoint", "http://localhost:11434")
        self.default_model = config.get("default_model", "llama3.3:70b")

        # HTTP client will be configured after loading provider config
        self.client: httpx.AsyncClient | None = None

        # Legacy model specifications (for backward compatibility)
        self._legacy_model_specs = {
            "llama3.3:70b": {
                "context_window": 128000,
                "cost_per_1k_tokens": 0.0008,  # Very cost-effective
                "max_output_tokens": 4000,
                "strengths": ["general_purpose", "conversation", "content", "speed"],
            },
            "llama3.3:8b": {
                "context_window": 128000,
                "cost_per_1k_tokens": 0.0002,  # Even more cost-effective
                "max_output_tokens": 4000,
                "strengths": ["speed", "basic_tasks", "content"],
            },
            "codellama:34b": {
                "context_window": 100000,
                "cost_per_1k_tokens": 0.0006,
                "max_output_tokens": 4000,
                "strengths": ["code", "programming", "scripts"],
            },
        }

        # Performance optimization settings
        self.enable_caching = config.get("enable_caching", True)
        self.keep_alive = config.get("keep_alive", "5m")  # Keep model loaded

    def _get_provider_name(self) -> str:
        return "llama"

    def _get_supported_capabilities_legacy(self) -> list[ModelCapability]:
        """Legacy hard-coded capabilities for backward compatibility."""
        return [
            ModelCapability.TEXT_GENERATION,
            ModelCapability.CHAT,
            ModelCapability.CODE_GENERATION,
            ModelCapability.STREAMING,
        ]

    def _get_supported_models_legacy(self) -> list[str]:
        """Legacy hard-coded models for backward compatibility."""
        return list(self._legacy_model_specs.keys())

    async def load_configuration(self) -> None:
        """Load Llama-specific configuration."""

        # Load base configuration
        await super().load_configuration()

        # Configure API settings from provider configuration
        if self._provider_config:
            self.ollama_endpoint = self._provider_config.api_endpoint

            # Configure HTTP client with provider settings
            timeout_ms = getattr(self._provider_config, "timeout_ms", 120000)
            pool_size = getattr(self._provider_config, "connection_pool_size", 5)

            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_ms / 1000.0),
                limits=httpx.Limits(
                    max_keepalive_connections=pool_size, max_connections=pool_size * 4
                ),
            )

            # Update Ollama-specific settings from provider config
            provider_settings = getattr(self._provider_config, "provider_settings", {})
            self.keep_alive = provider_settings.get("keep_alive", self.keep_alive)

        if not self.client:
            # Fallback client configuration
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0),  # Longer timeout for local models
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
            )

    def _get_model_context_window_legacy(self, model_name: str) -> int:
        """Legacy method using hard-coded specifications."""
        spec = self._legacy_model_specs.get(model_name, {})
        context_window: Any = spec.get("context_window", 128000)
        return int(context_window)

    def _get_model_cost_legacy(self, model_name: str) -> float:
        """Legacy method using hard-coded specifications."""
        spec = self._legacy_model_specs.get(model_name, {})
        cost: Any = spec.get("cost_per_1k_tokens", 0.0008)
        return float(cost)

    async def generate(
        self, request: ModelRequest, model_name: str | None = None
    ) -> ModelResponse:
        """Generate response using Llama models via Ollama."""

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
            # Check if model is available, pull if needed
            await self._ensure_model_available(model_name)

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
            logger.error("Llama generation failed", error=str(e), exc_info=True)
            return self._create_error_response(request, e, "generation_error")

    async def stream(
        self, request: ModelRequest, model_name: str | None = None
    ) -> AsyncGenerator[str, None]:
        """Stream response using Llama models via Ollama."""

        model_name = model_name or self.default_model

        if not self.supports_model(model_name):
            raise ValueError(f"Unsupported model: {model_name}")

        # Ensure model is available
        await self._ensure_model_available(model_name)

        # Prepare streaming request
        payload = self._build_request_payload(request, model_name)
        payload["stream"] = True

        try:
            if not self.client:
                raise RuntimeError("HTTP client not initialized")
            async with self.client.stream(
                "POST",
                f"{self.ollama_endpoint}/api/generate",
                headers=self._get_headers(),
                json=payload,
            ) as response:

                if response.status_code != 200:
                    error_text = await response.aread()
                    error_str = error_text.decode('utf-8') if isinstance(error_text, bytes) else str(error_text)
                    raise Exception(
                        f"Ollama error: {response.status_code} - {error_str}"
                    )

                async for line in response.aiter_lines():
                    if line.strip():
                        try:
                            chunk = json.loads(line)
                            content = chunk.get("response", "")

                            if content:
                                yield content

                            # Check if done
                            if chunk.get("done", False):
                                break

                        except json.JSONDecodeError:
                            continue  # Skip invalid JSON chunks

        except Exception as e:
            logger.error("Llama streaming failed", error=str(e), exc_info=True)
            yield f"Error: {e!s}"

    def _build_request_payload(self, request: ModelRequest, model_name: str) -> dict[str, Any]:
        """Build Ollama API request payload."""

        # Ollama uses a different format than OpenAI-style APIs
        if request.messages:
            # Convert chat messages to single prompt
            prompt_parts = []
            for msg in request.messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    prompt_parts.append(f"System: {content}")
                elif role == "user":
                    prompt_parts.append(f"User: {content}")
                elif role == "assistant":
                    prompt_parts.append(f"Assistant: {content}")

            prompt = "\n\n".join(prompt_parts)
            if not prompt.endswith("Assistant: "):
                prompt += "\n\nAssistant: "
        else:
            prompt = request.prompt

        # Add system prompt if provided
        if request.system_prompt and not request.messages:
            prompt = f"System: {request.system_prompt}\n\nUser: {prompt}\n\nAssistant: "

        # Build payload for Ollama
        max_output = 4000
        if hasattr(self, 'model_specs'):
            spec = getattr(self, 'model_specs', {}).get(model_name, {})
            max_output = spec.get("max_output_tokens", 4000)

        payload = {
            "model": model_name,
            "prompt": prompt,
            "options": {
                "num_predict": min(request.max_tokens, max_output),
                "temperature": request.temperature,
                "top_p": request.top_p,
            },
            "keep_alive": self.keep_alive,
            "stream": request.stream,
        }

        # Add top_k if specified
        if request.top_k:
            payload["options"]["top_k"] = request.top_k

        return payload

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers for Ollama requests."""
        return {
            "Content-Type": "application/json",
            "User-Agent": "Cerebro-AI-Brain/2.0",
        }

    async def _ensure_model_available(self, model_name: str) -> None:
        """Ensure the model is available in Ollama, pull if needed."""

        try:
            if not self.client:
                raise RuntimeError("HTTP client not initialized")
            # Check if model exists
            response = await self.client.get(f"{self.ollama_endpoint}/api/tags")

            if response.status_code == 200:
                models_data = response.json()
                available_models = [m["name"] for m in models_data.get("models", [])]

                if model_name not in available_models:
                    logger.info("Pulling model", model_name=model_name)
                    await self._pull_model(model_name)

        except Exception as e:
            logger.warning(
                "Failed to check model availability",
                model_name=model_name,
                error=str(e),
            )
            # Continue anyway, let the generation request handle the error

    async def _pull_model(self, model_name: str) -> None:
        """Pull a model from Ollama registry."""

        if not self.client:
            raise RuntimeError("HTTP client not initialized")

        pull_payload = {"name": model_name}

        async with self.client.stream(
            "POST", f"{self.ollama_endpoint}/api/pull", json=pull_payload
        ) as response:

            if response.status_code != 200:
                raise Exception(f"Failed to pull model {model_name}")

            # Stream the pull progress
            async for line in response.aiter_lines():
                if line.strip():
                    try:
                        progress = json.loads(line)
                        status = progress.get("status", "")
                        if "error" in progress:
                            raise Exception(f"Model pull error: {progress['error']}")

                        logger.debug(
                            "Model pull progress",
                            model_name=model_name,
                            status=status,
                        )

                    except json.JSONDecodeError:
                        continue

    async def _make_api_request(self, payload: dict[str, Any]) -> httpx.Response:
        """Make API request to Ollama."""

        if not self.client:
            raise RuntimeError("HTTP client not initialized")

        response = await self.client.post(
            f"{self.ollama_endpoint}/api/generate",
            headers=self._get_headers(),
            json=payload,
        )

        if response.status_code != 200:
            error_detail = await response.aread()
            error_str = error_detail.decode('utf-8') if isinstance(error_detail, bytes) else str(error_detail)
            raise Exception(
                f"Ollama API error: {response.status_code} - {error_str}"
            )

        return response

    async def _process_api_response(
        self,
        response: httpx.Response,
        request: ModelRequest,
        model_name: str,
        start_time: datetime,
    ) -> ModelResponse:
        """Process Ollama API response into ModelResponse."""

        response_data = response.json()

        # Extract response content
        content = response_data.get("response", "")
        done = response_data.get("done", False)

        if not done:
            # For non-streaming, this shouldn't happen
            logger.warning("Received incomplete response from Ollama")

        # Ollama provides some context about the response
        context = response_data.get("context", [])
        total_duration = response_data.get("total_duration", 0)
        load_duration = response_data.get("load_duration", 0)
        prompt_eval_duration = response_data.get("prompt_eval_duration", 0)
        eval_duration = response_data.get("eval_duration", 0)

        # Convert nanoseconds to milliseconds
        latency_ms = int(total_duration / 1_000_000) if total_duration else 0
        processing_time_ms = int(eval_duration / 1_000_000) if eval_duration else 0
        queue_time_ms = int(load_duration / 1_000_000) if load_duration else 0

        # Estimate token usage (Ollama doesn't always provide exact counts)
        prompt_eval_count = response_data.get("prompt_eval_count", 0)
        eval_count = response_data.get("eval_count", 0)

        if not prompt_eval_count:
            # Estimate based on prompt length
            prompt_eval_count = len((request.prompt or "").split()) * 1.3

        if not eval_count:
            # Estimate based on response length
            eval_count = len(content.split()) * 1.3

        prompt_tokens = int(prompt_eval_count)
        completion_tokens = int(eval_count)
        total_tokens = prompt_tokens + completion_tokens

        # Calculate cost
        cost = self._calculate_cost(prompt_tokens, completion_tokens, model_name)

        # Calculate confidence score
        confidence_score = self._calculate_confidence_score(
            content, done, request, response_data
        )

        return ModelResponse(
            request_id=request.request_id,
            content=content,
            model_name=model_name,
            provider=self.provider_name,
            completion_tokens=completion_tokens,
            prompt_tokens=prompt_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            processing_time_ms=processing_time_ms,
            queue_time_ms=queue_time_ms,
            confidence_score=confidence_score,
            success=True,
            cost_estimate=cost,
            finish_reason="completed" if done else "incomplete",
            metadata={
                "total_duration_ns": total_duration,
                "load_duration_ns": load_duration,
                "prompt_eval_duration_ns": prompt_eval_duration,
                "eval_duration_ns": eval_duration,
                "context_length": len(context) if context else 0,
            },
        )

    def _calculate_confidence_score(
        self, content: str, done: bool, request: ModelRequest, response_data: dict[str, Any]
    ) -> float:
        """Calculate confidence score for Llama response."""

        base_confidence = 0.75  # Base confidence for Llama

        # Adjust based on completion status
        if done:
            base_confidence += 0.1
        else:
            base_confidence -= 0.2  # Incomplete response

        # Adjust based on response length
        if len(content) < 5:
            base_confidence -= 0.3  # Very short response
        elif len(content) > 100:
            base_confidence += 0.1  # Good length response

        # Adjust based on generation speed (Llama's strength)
        eval_duration = response_data.get("eval_duration", 0)
        if eval_duration > 0:
            tokens_per_second = len(content.split()) / (eval_duration / 1_000_000_000)
            if tokens_per_second > 10:  # Fast generation
                base_confidence += 0.05

        # Adjust for simple tasks (Llama's strength)
        if request.complexity_score < 0.5:
            base_confidence += 0.1
        elif request.complexity_score > 0.8:
            base_confidence -= 0.1  # Complex tasks not Llama's strongest suit

        return min(max(base_confidence, 0.0), 1.0)

    async def health_check(self) -> ProviderHealthStatus:
        """Perform Llama/Ollama-specific health check."""

        try:
            if not self.client:
                raise RuntimeError("HTTP client not initialized")
            # Check if Ollama is running
            response = await self.client.get(f"{self.ollama_endpoint}/api/tags")

            if response.status_code != 200:
                raise Exception("Ollama service not available")

            # Test generation with a simple request
            test_request = ModelRequest(
                prompt="Hello! How are you?", max_tokens=20, timeout_seconds=30
            )

            start_time = datetime.now()
            model_response = await self.generate(test_request)
            latency = (datetime.now() - start_time).total_seconds() * 1000

            # Update health status
            self.health_status.healthy = model_response.success
            self.health_status.last_check = datetime.now()
            self.health_status.avg_latency_ms = latency

            # Ollama-specific health indicators
            if model_response.success and len(model_response.content) > 5:
                self.health_status.api_status = "operational"
            else:
                self.health_status.api_status = "degraded"

        except Exception as e:
            logger.error("Llama health check failed", error=str(e), exc_info=True)
            self.health_status.healthy = False
            self.health_status.last_error = str(e)
            self.health_status.api_status = "error"

        return self.health_status

    async def validate_request(self, request: ModelRequest) -> bool:
        """Validate Llama-specific request requirements."""

        if not await super().validate_request(request):
            return False

        # Llama-specific validations
        if request.max_tokens > 4000:
            return False  # Llama practical output limit

        # Check context window usage
        estimated_tokens = len((request.prompt or "").split()) * 1.3
        return estimated_tokens <= 128000  # Llama context limit

    def get_model_info(self, model_name: str) -> dict[str, Any] | None:
        """Get Llama model information."""

        if not self.supports_model(model_name):
            return None

        spec = getattr(self, 'model_specs', {}).get(model_name, {})

        return {
            "name": model_name,
            "provider": self.provider_name,
            "capabilities": self.supported_capabilities,
            "context_window": spec.get("context_window"),
            "cost_per_1k_tokens": spec.get("cost_per_1k_tokens"),
            "max_output_tokens": spec.get("max_output_tokens"),
            "strengths": spec.get("strengths", []),
            "best_for": [
                "General conversation",
                "Content creation",
                "Simple reasoning",
                "High-volume processing",
                "Cost-effective generation",
            ],
            "limitations": [
                "Not optimized for complex reasoning",
                "Limited mathematical capabilities",
                "Requires local deployment",
                "May need model pulling time",
            ],
            "deployment": "local_ollama",
        }

    async def close(self) -> None:
        """Clean up resources."""
        if self.client:
            await self.client.aclose()


__all__ = ["LlamaProvider"]
