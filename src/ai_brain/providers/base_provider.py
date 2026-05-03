"""
Base Provider Interface for Foundation Models

Defines the abstract interface that all foundation model providers must implement.
This ensures consistent behavior across different model providers while allowing
for provider-specific optimizations and capabilities.

Now supports dynamic model configuration loading from YAML files instead of
hard-coded specifications.
"""

import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

from structlog import get_logger

from src.core.observability import LLMCallMetrics, record_llm_call

if TYPE_CHECKING:
    from ..config.model_config_manager import ModelConfigManager
    from ..config.model_schemas import ModelSpecification, ProviderConfiguration

logger = get_logger(__name__)


class ModelCapability(Enum):
    """Supported model capabilities."""

    TEXT_GENERATION = "text_generation"
    TEXT_COMPLETION = "text_completion"
    CHAT = "chat"
    CODE_GENERATION = "code_generation"
    REASONING = "reasoning"
    ANALYSIS = "analysis"
    MULTIMODAL = "multimodal"
    STREAMING = "streaming"
    FUNCTION_CALLING = "function_calling"


class ResponseFormat(Enum):
    """Supported response formats."""

    TEXT = "text"
    JSON = "json"
    STRUCTURED = "structured"
    STREAMING = "streaming"


@dataclass
class ModelRequest:
    """Standardized request format for all model providers."""

    # Request identification
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.now)

    # Core request data
    prompt: str = ""
    messages: list[dict[str, str]] = field(default_factory=list)  # For chat models
    system_prompt: str | None = None

    # Generation parameters
    max_tokens: int = 1000
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int | None = None

    # Response configuration
    response_format: ResponseFormat = ResponseFormat.TEXT
    stream: bool = False

    # Context and constraints
    context_window_usage: float = 0.0  # Percentage of context window used
    quality_requirements: dict[str, Any] = field(default_factory=dict)

    # Performance constraints
    timeout_seconds: int = 30
    max_retries: int = 2
    priority: str = "normal"  # low, normal, high, critical

    # Metadata
    user_id: str | None = None
    session_id: str | None = None
    domain: str | None = None
    complexity_score: float = 0.5


@dataclass
class ModelResponse:
    """Standardized response format from all model providers."""

    # Response identification
    request_id: str
    response_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.now)

    # Core response data
    content: str = ""
    structured_content: dict[str, Any] | None = None

    # Generation metadata
    model_name: str = ""
    provider: str = ""
    completion_tokens: int = 0
    prompt_tokens: int = 0
    total_tokens: int = 0

    # Performance metrics
    latency_ms: int = 0
    processing_time_ms: int = 0
    queue_time_ms: int = 0

    # Quality metrics
    confidence_score: float = 0.0
    quality_indicators: dict[str, float] = field(default_factory=dict)

    # Status and error handling
    success: bool = True
    error_message: str | None = None
    error_type: str | None = None
    retry_count: int = 0

    # Cost information
    cost_estimate: float = 0.0
    cost_breakdown: dict[str, float] = field(default_factory=dict)

    # Additional metadata
    finish_reason: str = "completed"  # completed, length, stop, error
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderHealthStatus:
    """Health status information for a model provider."""

    provider_name: str
    healthy: bool = True
    last_check: datetime = field(default_factory=datetime.now)

    # Performance metrics
    avg_latency_ms: float = 0.0
    success_rate: float = 1.0
    requests_per_minute: int = 0

    # Capacity information
    current_load: float = 0.0  # 0.0 - 1.0
    queue_length: int = 0
    available_capacity: float = 1.0

    # Error information
    recent_errors: list[str] = field(default_factory=list)
    last_error: str | None = None
    error_rate: float = 0.0

    # Provider-specific status
    api_status: str = "operational"
    rate_limit_remaining: int | None = None
    rate_limit_reset: datetime | None = None


class BaseProvider(ABC):
    """
    Abstract base class for all foundation model providers.

    Defines the interface that must be implemented by all provider classes
    to ensure consistent behavior and enable the model router to work
    seamlessly with different providers.

    Now supports dynamic model configuration loading from the ModelConfigManager.
    """

    def __init__(
        self,
        config: dict[str, Any],
        model_config_manager: Optional["ModelConfigManager"] = None,
    ):
        """
        Initialize the provider with configuration.

        Args:
            config: Provider-specific configuration
            model_config_manager: Optional model configuration manager
        """
        self.config = config
        self.model_config_manager = model_config_manager
        self.provider_name = self._get_provider_name()

        # Dynamic configuration support
        self._provider_config: ProviderConfiguration | None = None
        self._model_specs: dict[str, ModelSpecification] = {}
        self._config_loaded = False

        # Legacy support - will be populated from configuration
        self.supported_capabilities: list[ModelCapability] = []
        self.supported_models: list[str] = []

        # Performance tracking
        self.request_count = 0
        self.error_count = 0
        self.total_latency_ms = 0

        # Health monitoring
        self.last_health_check = datetime.now()
        self.health_status = ProviderHealthStatus(provider_name=self.provider_name)

        logger.info("Initialized provider", provider_name=self.provider_name)

    @abstractmethod
    def _get_provider_name(self) -> str:
        """Return the name of this provider."""
        pass

    async def load_configuration(self) -> None:
        """Load model configurations for this provider."""

        if not self.model_config_manager:
            # Fallback to legacy hard-coded configuration
            self.supported_capabilities = self._get_supported_capabilities_legacy()
            self.supported_models = self._get_supported_models_legacy()
            logger.warning(
                "Provider using legacy configuration",
                provider_name=self.provider_name,
            )
            return

        try:
            # Load provider configuration
            self._provider_config = (
                await self.model_config_manager.get_provider_configuration(
                    self.provider_name
                )
            )

            if not self._provider_config or not self._provider_config.enabled:
                logger.warning(
                    f"Provider {self.provider_name} is disabled or not configured"
                )
                self.supported_models = []
                self.supported_capabilities = []
                self._config_loaded = True
                return

            # Load model specifications for this provider
            self._model_specs = await self.model_config_manager.get_models_for_provider(
                self.provider_name
            )

            # Update supported models and capabilities
            self.supported_models = list(self._model_specs.keys())

            # Collect all capabilities from models
            capabilities_set = set()
            for model_spec in self._model_specs.values():
                capabilities_set.update(model_spec.capabilities)

            # Convert to legacy format for compatibility
            from ..config.model_schemas import ModelCapability as ConfigCapability

            legacy_capability_mapping = {
                ConfigCapability.TEXT_GENERATION: ModelCapability.TEXT_GENERATION,
                ConfigCapability.CHAT: ModelCapability.CHAT,
                ConfigCapability.CODE_GENERATION: ModelCapability.CODE_GENERATION,
                ConfigCapability.REASONING: ModelCapability.REASONING,
                ConfigCapability.ANALYSIS: ModelCapability.ANALYSIS,
                ConfigCapability.MULTIMODAL: ModelCapability.MULTIMODAL,
                ConfigCapability.STREAMING: ModelCapability.STREAMING,
                ConfigCapability.FUNCTION_CALLING: ModelCapability.FUNCTION_CALLING,
            }

            self.supported_capabilities = []
            for config_cap in capabilities_set:
                if isinstance(config_cap, str):
                    try:
                        config_cap_enum = ConfigCapability(config_cap)
                        if config_cap_enum in legacy_capability_mapping:
                            self.supported_capabilities.append(
                                legacy_capability_mapping[config_cap_enum]
                            )
                    except ValueError:
                        logger.warning("Unknown capability", capability=config_cap)

            self._config_loaded = True
            logger.info(
                "Loaded provider configuration",
                provider_name=self.provider_name,
                model_count=len(self.supported_models),
                capability_count=len(self.supported_capabilities),
            )

        except Exception as e:
            logger.error(
                "Failed to load provider configuration",
                provider_name=self.provider_name,
                error=str(e),
                exc_info=True,
            )
            # Fallback to legacy configuration
            self.supported_capabilities = self._get_supported_capabilities_legacy()
            self.supported_models = self._get_supported_models_legacy()

    def _get_supported_capabilities_legacy(self) -> list[ModelCapability]:
        """
        Legacy method for hard-coded capabilities.
        Should be implemented by subclasses for backward compatibility.
        """
        return []

    def _get_supported_models_legacy(self) -> list[str]:
        """
        Legacy method for hard-coded models.
        Should be implemented by subclasses for backward compatibility.
        """
        return []

    @abstractmethod
    async def generate(
        self, request: ModelRequest, model_name: str | None = None
    ) -> ModelResponse:
        """
        Generate a response using the specified model.

        Args:
            request: The model request to process
            model_name: Specific model to use (optional)

        Returns:
            ModelResponse with generated content and metadata
        """
        pass

    async def stream(
        self, request: ModelRequest, model_name: str | None = None
    ) -> AsyncGenerator[str, None]:
        """
        Stream a response using the specified model.

        Args:
            request: The model request to process
            model_name: Specific model to use (optional)

        Yields:
            Partial response content as it's generated
        """
        # Default implementation falls back to non-streaming
        response = await self.generate(request, model_name)
        yield response.content

    async def health_check(self) -> ProviderHealthStatus:
        """
        Check the health status of this provider.

        Returns:
            ProviderHealthStatus with current health information
        """
        try:
            # Basic health check with a simple request
            test_request = ModelRequest(
                prompt="Hello", max_tokens=5, timeout_seconds=10
            )

            start_time = datetime.now()
            response = await self.generate(test_request)
            latency = (datetime.now() - start_time).total_seconds() * 1000

            # Update health status
            self.health_status.healthy = response.success
            self.health_status.last_check = datetime.now()
            self.health_status.avg_latency_ms = latency

            if not response.success:
                self.health_status.last_error = response.error_message
                self.health_status.recent_errors.append(
                    f"{datetime.now()}: {response.error_message}"
                )
                # Keep only recent errors (last 10)
                self.health_status.recent_errors = self.health_status.recent_errors[
                    -10:
                ]

        except Exception as e:
            logger.error(
                "Health check failed",
                provider_name=self.provider_name,
                error=str(e),
                exc_info=True,
            )
            self.health_status.healthy = False
            self.health_status.last_error = str(e)
            self.health_status.last_check = datetime.now()

        return self.health_status

    def supports_capability(self, capability: ModelCapability) -> bool:
        """Check if this provider supports a specific capability."""
        return capability in self.supported_capabilities

    def supports_model(self, model_name: str) -> bool:
        """Check if this provider supports a specific model."""
        return model_name in self.supported_models

    def get_model_info(self, model_name: str) -> dict[str, Any] | None:
        """Get information about a specific model."""
        if not self.supports_model(model_name):
            return None

        return {
            "name": model_name,
            "provider": self.provider_name,
            "capabilities": self.supported_capabilities,
            "context_window": self._get_model_context_window(model_name),
            "cost_per_1k_tokens": self._get_model_cost(model_name),
        }

    def _get_model_context_window(self, model_name: str) -> int:
        """Get context window size for a specific model."""

        # Try to get from dynamic configuration first
        if self._config_loaded and model_name in self._model_specs:
            return int(self._model_specs[model_name].context_window)

        # Fallback to legacy implementation
        return self._get_model_context_window_legacy(model_name)

    def _get_model_cost(self, model_name: str) -> float:
        """Get cost per 1K tokens for a specific model."""

        # Try to get from dynamic configuration first
        if self._config_loaded and model_name in self._model_specs:
            return float(self._model_specs[model_name].cost_per_1k_tokens)

        # Fallback to legacy implementation
        return self._get_model_cost_legacy(model_name)

    def _get_model_context_window_legacy(self, model_name: str) -> int:
        """Legacy method for model context window."""
        return 4000

    def _get_model_cost_legacy(self, model_name: str) -> float:
        """Legacy method for model cost."""
        return 0.001

    def get_model_specification(
        self, model_name: str
    ) -> Optional["ModelSpecification"]:
        """Get complete model specification from configuration."""

        if self._config_loaded and model_name in self._model_specs:
            return self._model_specs[model_name]

        return None

    def get_provider_configuration(self) -> Optional["ProviderConfiguration"]:
        """Get provider configuration."""
        return self._provider_config

    async def ensure_configuration_loaded(self) -> None:
        """Ensure configuration is loaded before operations."""

        if not self._config_loaded:
            await self.load_configuration()

    async def reload_configuration(self) -> None:
        """Reload configuration from the manager."""

        self._config_loaded = False
        await self.load_configuration()

    async def _preprocess_request(self, request: ModelRequest) -> ModelRequest:
        """Preprocess request before sending to model."""
        # Default implementation does no preprocessing
        return request

    async def _postprocess_response(
        self, response: ModelResponse, request: ModelRequest
    ) -> ModelResponse:
        """Postprocess response before returning."""
        # Update performance metrics
        self.request_count += 1
        if not response.success:
            self.error_count += 1
        self.total_latency_ms += response.latency_ms

        # Calculate success rate
        self.health_status.success_rate = (self.request_count - self.error_count) / max(
            self.request_count, 1
        )

        # Calculate average latency
        if self.request_count > 0:
            self.health_status.avg_latency_ms = (
                self.total_latency_ms / self.request_count
            )

        record_llm_call(
            LLMCallMetrics(
                provider=response.provider or self.provider_name,
                model=response.model_name,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                latency_ms=response.latency_ms,
                cost_usd=response.cost_estimate,
                request_id=response.request_id,
                success=response.success,
            )
        )

        return response

    def _calculate_cost(
        self, prompt_tokens: int, completion_tokens: int, model_name: str
    ) -> float:
        """Calculate cost for token usage."""
        total_tokens = prompt_tokens + completion_tokens
        cost_per_1k = self._get_model_cost(model_name)
        return (total_tokens / 1000) * cost_per_1k

    def _create_error_response(
        self, request: ModelRequest, error: Exception, error_type: str = "unknown"
    ) -> ModelResponse:
        """Create an error response."""
        return ModelResponse(
            request_id=request.request_id,
            model_name=getattr(request, "model_name", "unknown"),
            provider=self.provider_name,
            success=False,
            error_message=str(error),
            error_type=error_type,
            latency_ms=0,
            confidence_score=0.0,
        )

    async def validate_request(self, request: ModelRequest) -> bool:
        """Validate that the request can be processed by this provider."""
        # Basic validation
        if not request.prompt and not request.messages:
            return False

        if request.max_tokens <= 0:
            return False

        return not (request.temperature < 0 or request.temperature > 2)

    def get_metrics(self) -> dict[str, Any]:
        """Get provider performance metrics."""
        return {
            "provider": self.provider_name,
            "total_requests": self.request_count,
            "error_count": self.error_count,
            "success_rate": self.health_status.success_rate,
            "avg_latency_ms": self.health_status.avg_latency_ms,
            "supported_models": self.supported_models,
            "health_status": self.health_status.healthy,
        }


__all__ = [
    "BaseProvider",
    "ModelCapability",
    "ModelRequest",
    "ModelResponse",
    "ProviderHealthStatus",
    "ResponseFormat",
]
