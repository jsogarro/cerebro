"""
Legacy Configuration Adapter

Provides backward compatibility for components that expect hard-coded
model specifications. This adapter translates between the old hard-coded
format and the new configuration-based approach.

Key Functions:
- Migrate hard-coded specifications to configuration format
- Provide compatibility layer for legacy code
- Support gradual migration without breaking existing functionality
- Generate configuration files from existing hard-coded data
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..providers.base_provider import BaseProvider
    from .model_config_manager import ModelConfigManager

from ..providers.base_provider import ModelCapability as LegacyModelCapability
from .model_schemas import (
    ConfigurationMetadata,
    GlobalSettings,
    ModelConfiguration,
    ModelSpecification,
    ModelTier,
    ProviderConfiguration,
    RoutingConfiguration,
)
from .model_schemas import (
    ModelCapability as ConfigModelCapability,
)

logger = logging.getLogger(__name__)


class LegacyConfigurationAdapter:
    """
    Adapter for translating between legacy hard-coded configurations
    and new dynamic configuration system.
    """

    def __init__(self) -> None:
        """Initialize legacy adapter."""

        # Capability mapping between legacy and new enums
        self.capability_mapping = {
            LegacyModelCapability.TEXT_GENERATION: ConfigModelCapability.TEXT_GENERATION,
            LegacyModelCapability.CHAT: ConfigModelCapability.CHAT,
            LegacyModelCapability.CODE_GENERATION: ConfigModelCapability.CODE_GENERATION,
            LegacyModelCapability.REASONING: ConfigModelCapability.REASONING,
            LegacyModelCapability.ANALYSIS: ConfigModelCapability.ANALYSIS,
            LegacyModelCapability.MULTIMODAL: ConfigModelCapability.MULTIMODAL,
            LegacyModelCapability.STREAMING: ConfigModelCapability.STREAMING,
            LegacyModelCapability.FUNCTION_CALLING: ConfigModelCapability.FUNCTION_CALLING,
        }

        # Reverse mapping for backward compatibility
        self.reverse_capability_mapping = {
            v: k for k, v in self.capability_mapping.items()
        }

    def migrate_provider_specs_to_config(
        self,
        provider_name: str,
        legacy_model_specs: dict[str, dict[str, Any]],
        legacy_capabilities: list[LegacyModelCapability],
        provider_config: dict[str, Any],
    ) -> dict[str, ModelSpecification]:
        """
        Migrate legacy provider specifications to new configuration format.

        Args:
            provider_name: Name of the provider
            legacy_model_specs: Hard-coded model specifications
            legacy_capabilities: Hard-coded capabilities
            provider_config: Provider-specific configuration

        Returns:
            Dictionary of ModelSpecification objects
        """

        migrated_models = {}

        for model_name, legacy_spec in legacy_model_specs.items():
            # Map legacy specification to new format
            model_spec = ModelSpecification(
                provider=provider_name,
                tier=self._infer_model_tier(legacy_spec),
                enabled=True,
                # Cost and performance
                cost_per_1k_tokens=legacy_spec.get("cost_per_1k_tokens", 0.001),
                avg_latency_ms=legacy_spec.get("avg_latency_ms", 100),
                context_window=legacy_spec.get("context_window", 4000),
                max_output_tokens=legacy_spec.get("max_output_tokens", 1000),
                # Quality metrics
                quality_score=legacy_spec.get("quality_score", 0.75),
                availability=legacy_spec.get("availability", 0.99),
                rate_limit=legacy_spec.get("rate_limit", 1000),
                supports_streaming=legacy_spec.get("supports_streaming", False),
                # Capabilities
                capabilities=self._convert_legacy_capabilities(legacy_capabilities),
                strengths=legacy_spec.get("strengths", []),
                weaknesses=legacy_spec.get("weaknesses", []),
                # Metadata
                metadata={
                    "migrated_from": "legacy_hard_coded",
                    "migration_date": datetime.now().isoformat(),
                    "original_spec": legacy_spec,
                },
            )

            migrated_models[model_name] = model_spec

        logger.info(
            f"Migrated {len(migrated_models)} models for provider {provider_name}"
        )
        return migrated_models

    def create_provider_configuration(
        self, provider_name: str, provider_config: dict[str, Any]
    ) -> ProviderConfiguration:
        """Create provider configuration from legacy config."""

        return ProviderConfiguration(
            name=provider_config.get("name", provider_name.title()),
            enabled=provider_config.get("enabled", True),
            api_endpoint=provider_config.get(
                "endpoint", provider_config.get("api_endpoint", "")
            ),
            api_key_env=provider_config.get("api_key_env"),
            health_check_endpoint=provider_config.get("health_check_endpoint"),
            timeout_ms=provider_config.get("timeout_ms", 30000),
            max_retries=provider_config.get("max_retries", 3),
            connection_pool_size=provider_config.get("connection_pool_size", 10),
            provider_settings=provider_config.get("provider_settings", {}),
        )

    def generate_configuration_from_legacy(
        self, legacy_providers: dict[str, dict[str, Any]], environment: str = "migrated"
    ) -> ModelConfiguration:
        """
        Generate complete ModelConfiguration from legacy provider data.

        Args:
            legacy_providers: Dictionary of legacy provider configurations
            environment: Target environment name

        Returns:
            Complete ModelConfiguration object
        """

        models = {}
        providers = {}

        # Process each legacy provider
        for provider_name, legacy_config in legacy_providers.items():
            # Extract legacy specs
            legacy_model_specs = legacy_config.get("model_specs", {})
            legacy_capabilities = legacy_config.get("capabilities", [])

            # Migrate models
            migrated_models = self.migrate_provider_specs_to_config(
                provider_name, legacy_model_specs, legacy_capabilities, legacy_config
            )
            models.update(migrated_models)

            # Create provider configuration
            provider_config = self.create_provider_configuration(
                provider_name, legacy_config
            )
            providers[provider_name] = provider_config

        # Create complete configuration
        config = ModelConfiguration(
            version="2.0.0",
            metadata=ConfigurationMetadata(
                config_name=environment,
                description=f"Migrated configuration for {environment} environment",
                environment=environment,
                maintainer="Legacy Migration Tool",
            ),
            global_settings=GlobalSettings(),
            models=models,
            providers=providers,
            routing_config=RoutingConfiguration(),
        )

        return config

    def _infer_model_tier(self, legacy_spec: dict[str, Any]) -> ModelTier:
        """Infer model tier from legacy specification."""

        cost = legacy_spec.get("cost_per_1k_tokens", 0.001)
        quality = legacy_spec.get("quality_score", 0.75)

        # Simple heuristic-based classification
        if cost < 0.0005 and quality < 0.7:
            return ModelTier.BASIC
        elif cost < 0.001 and quality < 0.85:
            return ModelTier.STANDARD
        elif cost > 0.0015 or quality > 0.9:
            return ModelTier.PREMIUM
        else:
            return ModelTier.STANDARD

    def _convert_legacy_capabilities(
        self, legacy_capabilities: list[LegacyModelCapability]
    ) -> list[ConfigModelCapability]:
        """Convert legacy capabilities to new format."""

        converted = []

        for legacy_cap in legacy_capabilities:
            if legacy_cap in self.capability_mapping:
                converted.append(self.capability_mapping[legacy_cap])

        return converted

    def convert_config_capabilities_to_legacy(
        self, config_capabilities: list[ConfigModelCapability]
    ) -> list[LegacyModelCapability]:
        """Convert configuration capabilities to legacy format."""

        converted = []

        for config_cap in config_capabilities:
            if config_cap in self.reverse_capability_mapping:
                converted.append(self.reverse_capability_mapping[config_cap])

        return converted


class LegacyProviderWrapper:
    """
    Wrapper that makes configuration-based providers look like legacy providers.

    This allows existing code that expects hard-coded model_specs to continue
    working while the system gradually migrates to configuration-based approach.
    """

    def __init__(
        self, provider: "BaseProvider", model_config_manager: "ModelConfigManager"
    ):
        """Initialize legacy wrapper."""
        self.provider = provider
        self.model_config_manager = model_config_manager
        self.adapter = LegacyConfigurationAdapter()

    async def get_legacy_model_specs(self) -> dict[str, dict[str, Any]]:
        """Get model specifications in legacy format."""

        # Get current model specifications from configuration
        model_specs = await self.model_config_manager.get_models_for_provider(
            self.provider.provider_name
        )

        # Convert to legacy format
        legacy_specs = {}

        for model_name, model_spec in model_specs.items():
            legacy_specs[model_name] = {
                "context_window": model_spec.context_window,
                "cost_per_1k_tokens": model_spec.cost_per_1k_tokens,
                "max_output_tokens": model_spec.max_output_tokens,
                "avg_latency_ms": model_spec.avg_latency_ms,
                "quality_score": model_spec.quality_score,
                "availability": model_spec.availability,
                "rate_limit": model_spec.rate_limit,
                "supports_streaming": model_spec.supports_streaming,
                "strengths": model_spec.strengths,
                "weaknesses": model_spec.weaknesses,
                "capabilities": [cap.value for cap in model_spec.capabilities],
                "tier": model_spec.tier.value,
            }

        return legacy_specs

    async def get_legacy_capabilities(self) -> list[LegacyModelCapability]:
        """Get capabilities in legacy format."""

        # Get all capabilities from configured models
        model_specs = await self.model_config_manager.get_models_for_provider(
            self.provider.provider_name
        )

        all_capabilities = set()
        for model_spec in model_specs.values():
            all_capabilities.update(model_spec.capabilities)

        # Convert to legacy format
        return self.adapter.convert_config_capabilities_to_legacy(
            list(all_capabilities)
        )


__all__ = ["LegacyConfigurationAdapter", "LegacyProviderWrapper"]
