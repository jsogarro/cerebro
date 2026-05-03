"""
Pydantic Schemas for Model Configuration

Defines validation schemas for model specifications, provider configurations,
and routing rules. Ensures type safety and validation for all configuration
loaded from YAML files.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


class ModelTier(StrEnum):
    """Model performance and cost tiers."""

    BASIC = "basic"
    STANDARD = "standard"
    PREMIUM = "premium"
    SPECIALIZED = "specialized"
    TESTING = "testing"


class ModelCapability(StrEnum):
    """Supported model capabilities."""

    TEXT_GENERATION = "text_generation"
    CHAT = "chat"
    CODE_GENERATION = "code_generation"
    REASONING = "reasoning"
    ANALYSIS = "analysis"
    MULTIMODAL = "multimodal"
    VISION = "vision"
    STREAMING = "streaming"
    FUNCTION_CALLING = "function_calling"
    DEBUGGING = "debugging"
    TESTING = "testing"


# Canonical RoutingStrategy lives in src.ai_brain.router.routing_types.
# Re-exported here to preserve the historical import path and avoid duplicate
# enum identities (which caused 10 mypy arg-type errors across MASR boundaries).
from src.ai_brain.router.routing_types import RoutingStrategy  # noqa: E402


class ModelSpecification(BaseModel):
    """Complete specification for a foundation model."""

    model_config = ConfigDict(extra="allow")

    # Basic identification
    provider: str = Field(..., description="Provider name (e.g., 'deepseek', 'llama')")
    tier: ModelTier = Field(..., description="Performance tier of the model")
    enabled: bool = Field(default=True, description="Whether this model is enabled")

    # Cost and performance metrics
    cost_per_1k_tokens: float = Field(
        ..., ge=0.0, description="Cost per 1K tokens in USD"
    )
    avg_latency_ms: int = Field(
        ..., ge=0, description="Average latency in milliseconds"
    )
    context_window: int = Field(..., ge=1000, description="Maximum context window size")
    max_output_tokens: int = Field(..., ge=1, description="Maximum output tokens")

    # Quality and reliability metrics
    quality_score: float = Field(..., ge=0.0, le=1.0, description="Quality score (0-1)")
    availability: float = Field(
        default=0.99, ge=0.0, le=1.0, description="SLA availability"
    )
    rate_limit: int = Field(default=1000, ge=1, description="Requests per minute limit")
    supports_streaming: bool = Field(
        default=False, description="Supports streaming responses"
    )

    # Capabilities and characteristics
    capabilities: list[ModelCapability] = Field(
        default_factory=list, description="Model capabilities"
    )
    strengths: list[str] = Field(default_factory=list, description="Model strengths")
    weaknesses: list[str] = Field(default_factory=list, description="Model weaknesses")

    # Optimization criteria
    optimal_for: dict[str, Any] | None = Field(
        default=None, description="Conditions when this model is optimal"
    )

    # Documentation and metadata
    metadata: dict[str, Any] | None = Field(
        default_factory=dict, description="Additional model metadata"
    )

    @field_validator("cost_per_1k_tokens")
    @classmethod
    def validate_reasonable_cost(cls, v: float) -> float:
        if v > 1.0:  # More than $1 per 1K tokens seems unreasonable
            raise ValueError("Cost per 1K tokens seems too high (> $1.00)")
        return v

    @field_validator("quality_score")
    @classmethod
    def validate_quality_score(cls, v: float) -> float:
        if v < 0.1:  # Quality score too low to be useful
            raise ValueError("Quality score must be at least 0.1")
        return v


class ProviderConfiguration(BaseModel):
    """Configuration for a model provider."""

    model_config = ConfigDict(extra="allow")

    # Basic information
    name: str = Field(..., description="Human-readable provider name")
    enabled: bool = Field(default=True, description="Whether provider is enabled")

    # API configuration
    api_endpoint: str = Field(..., description="API endpoint URL")
    api_key_env: str | None = Field(
        default=None, description="Environment variable name for API key"
    )
    health_check_endpoint: str | None = Field(
        default=None, description="Health check endpoint path"
    )

    # Connection configuration
    timeout_ms: int = Field(default=30000, ge=1000, description="Request timeout")
    max_retries: int = Field(
        default=3, ge=0, le=10, description="Maximum retry attempts"
    )
    connection_pool_size: int = Field(
        default=10, ge=1, description="Connection pool size"
    )

    # Provider-specific settings
    provider_settings: dict[str, Any] = Field(
        default_factory=dict, description="Provider-specific configuration"
    )

    @field_validator("api_endpoint")
    @classmethod
    def validate_endpoint_format(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("API endpoint must be a valid HTTP/HTTPS URL")
        return v


class CostOptimizationConfig(BaseModel):
    """Cost optimization configuration."""

    enabled: bool = True
    max_cost_per_request: float = Field(default=0.05, ge=0.0)
    daily_budget_limit: float = Field(default=100.0, ge=0.0)
    cost_alert_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    enable_budget_alerts: bool = True


class PerformanceOptimizationConfig(BaseModel):
    """Performance optimization configuration."""

    target_latency_ms: int = Field(default=1000, ge=10)
    quality_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    enable_caching: bool = True
    cache_ttl_seconds: int = Field(default=3600, ge=60)


class SelectionRule(BaseModel):
    """Model selection rule for specific conditions."""

    preferred_models: list[str] = Field(default_factory=list)
    avoid_models: list[str] = Field(default_factory=list)
    max_cost: float | None = Field(default=None, ge=0.0)
    target_latency_ms: int | None = Field(default=None, ge=0)
    quality_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    require_fallback: bool = False
    required_capabilities: list[ModelCapability] = Field(default_factory=list)


class DomainPreference(BaseModel):
    """Domain-specific model preferences."""

    preferred_models: list[str] = Field(default_factory=list)
    quality_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    cost_threshold: float | None = Field(default=None, ge=0.0)
    max_latency_ms: int | None = Field(default=None, ge=0)
    required_capabilities: list[ModelCapability] = Field(default_factory=list)
    require_validation: bool = False


class RoutingConfiguration(BaseModel):
    """Complete routing configuration."""

    default_strategy: RoutingStrategy = RoutingStrategy.BALANCED
    cost_optimization: CostOptimizationConfig = Field(
        default_factory=CostOptimizationConfig
    )
    performance_optimization: PerformanceOptimizationConfig = Field(
        default_factory=PerformanceOptimizationConfig
    )

    # Selection rules by complexity
    simple_queries: SelectionRule = Field(default_factory=SelectionRule)
    moderate_queries: SelectionRule = Field(default_factory=SelectionRule)
    complex_queries: SelectionRule = Field(default_factory=SelectionRule)

    # Domain-specific preferences
    domain_preferences: dict[str, DomainPreference] = Field(default_factory=dict)


class GlobalSettings(BaseModel):
    """Global model system settings."""

    default_timeout_ms: int = Field(default=30000, ge=1000)
    default_retry_attempts: int = Field(default=3, ge=0, le=10)
    enable_streaming: bool = True
    enable_fallback: bool = True
    quality_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    cost_threshold: float = Field(default=0.10, ge=0.0)
    latency_threshold_ms: int = Field(default=5000, ge=100)

    # Development and debug settings
    enable_debug_logging: bool = False
    enable_mock_providers: bool = False
    strict_validation: bool = True


class ConfigurationMetadata(BaseModel):
    """Metadata about the configuration."""

    schema_version: str = "1.0"
    config_name: str = "unknown"
    description: str = ""
    environment: str | None = None
    extends: str | None = None

    # Maintenance information
    maintainer: str = "Cerebro Development Team"
    last_updated: str = Field(default_factory=lambda: datetime.now().isoformat())
    last_validated: str | None = None
    validation_required: bool = True

    # Deployment information
    deployment_notes: list[str] = Field(default_factory=list)
    sla_requirements: dict[str, Any] = Field(default_factory=dict)


class ModelConfiguration(BaseModel):
    """Complete model configuration structure."""

    model_config = ConfigDict(extra="allow")

    # Configuration metadata
    version: str = "2.0.0"
    metadata: ConfigurationMetadata = Field(default_factory=ConfigurationMetadata)

    # Global settings
    global_settings: GlobalSettings = Field(default_factory=GlobalSettings)

    # Model and provider specifications
    models: dict[str, ModelSpecification] = Field(default_factory=dict)
    providers: dict[str, ProviderConfiguration] = Field(default_factory=dict)

    # Routing and optimization
    routing_config: RoutingConfiguration = Field(default_factory=RoutingConfiguration)

    # Additional configuration sections
    testing_config: dict[str, Any] | None = None
    monitoring: dict[str, Any] | None = None
    security: dict[str, Any] | None = None
    disaster_recovery: dict[str, Any] | None = None

    @field_validator("models")
    @classmethod
    def validate_models_have_providers(cls, v: dict[str, ModelSpecification], info: ValidationInfo) -> dict[str, ModelSpecification]:
        """Ensure all models reference valid providers."""
        # Skip validation if providers haven't been processed yet
        providers = info.data.get("providers")
        if not providers:
            return v

        for model_name, model_spec in v.items():
            if model_spec.provider not in providers:
                raise ValueError(
                    f"Model '{model_name}' references unknown provider '{model_spec.provider}'"
                )

        return v

    def get_enabled_models(self) -> dict[str, ModelSpecification]:
        """Get only enabled models."""
        return {name: spec for name, spec in self.models.items() if spec.enabled}

    def get_enabled_providers(self) -> dict[str, ProviderConfiguration]:
        """Get only enabled providers."""
        return {
            name: config for name, config in self.providers.items() if config.enabled
        }

    def get_models_for_provider(
        self, provider_name: str
    ) -> dict[str, ModelSpecification]:
        """Get all models for a specific provider."""
        return {
            name: spec
            for name, spec in self.models.items()
            if spec.provider == provider_name and spec.enabled
        }

    def get_models_by_capability(
        self, capability: ModelCapability
    ) -> dict[str, ModelSpecification]:
        """Get all models that support a specific capability."""
        return {
            name: spec
            for name, spec in self.models.items()
            if capability in spec.capabilities and spec.enabled
        }

    def get_models_by_tier(self, tier: ModelTier) -> dict[str, ModelSpecification]:
        """Get all models in a specific tier."""
        return {
            name: spec
            for name, spec in self.models.items()
            if spec.tier == tier and spec.enabled
        }


__all__ = [
    "ConfigurationMetadata",
    "DomainPreference",
    "GlobalSettings",
    "ModelCapability",
    "ModelConfiguration",
    "ModelSpecification",
    "ModelTier",
    "ProviderConfiguration",
    "RoutingConfiguration",
    "RoutingStrategy",
    "SelectionRule",
]
