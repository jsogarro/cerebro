"""
Model Configuration Manager

Central manager for loading, validating, and managing model configurations.
Supports environment-specific overrides, and runtime
configuration updates without system restart.

Features:
- YAML configuration loading with inheritance
- Environment-specific overrides
- Hot-reloading with file system watching
- Configuration validation and error handling
- Thread-safe configuration updates
- Configuration change notifications
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from structlog import get_logger

logger = get_logger()

from .model_schemas import (  # noqa: E402
    ModelCapability,
    ModelConfiguration,
    ModelSpecification,
    ModelTier,
    ProviderConfiguration,
)


class ConfigurationChangeEvent:
    """Event for configuration changes."""

    def __init__(
        self,
        change_type: str,
        config_path: str,
        old_config: ModelConfiguration | None = None,
        new_config: ModelConfiguration | None = None,
    ):
        self.change_type = change_type  # created, modified, deleted
        self.config_path = config_path
        self.old_config = old_config
        self.new_config = new_config
        self.timestamp = datetime.now()


class ModelConfigManager:
    """
    Central manager for model configurations.

    Provides thread-safe access to model configurations with support for
    validation, and environment-specific overrides.
    """

    def __init__(self, config: dict[str, Any]):
        """Initialize model configuration manager."""
        self.config = config

        # Configuration paths
        self.config_dir = Path(config.get("config_dir", "configs/models"))
        self.base_config_path = self.config_dir / "base.yaml"
        self.environment = config.get("environment", "development")
        self.env_config_path = self.config_dir / f"{self.environment}.yaml"

        # Current configuration
        self._lock = threading.RLock()
        self._current_config: ModelConfiguration | None = None
        self._config_timestamp: datetime | None = None

        # Change notifications
        self._change_listeners: list[Callable[[ConfigurationChangeEvent], None]] = []

        # Performance tracking
        self.load_count = 0
        self.error_count = 0

    async def initialize(self) -> None:
        """Initialize the configuration manager."""

        logger.info("Initializing model configuration manager...")

        # Load initial configuration
        await self.load_configuration()

        if self._current_config is not None:
            logger.info(
                f"Model configuration manager initialized with {len(self._current_config.models)} models"
            )

    async def load_configuration(self) -> ModelConfiguration:
        """Load and validate configuration from files."""

        try:
            with self._lock:
                # Load base configuration
                base_config = await self._load_yaml_file(self.base_config_path)

                # Load environment-specific overrides
                env_config = {}
                if self.env_config_path.exists():
                    env_config = await self._load_yaml_file(self.env_config_path)

                # Merge configurations
                merged_config = self._merge_configurations(base_config, env_config)

                # Validate configuration
                validated_config = ModelConfiguration(**merged_config)

                # Update current configuration
                old_config = self._current_config
                self._current_config = validated_config
                self._config_timestamp = datetime.now()

                # Notify listeners of change
                if old_config != self._current_config:
                    await self._notify_change_listeners(
                        "modified", old_config, self._current_config
                    )

                self.load_count += 1
                logger.info("Model configuration loaded successfully")

                return self._current_config

        except Exception as e:
            self.error_count += 1
            logger.error(f"Failed to load configuration: {e}")

            # Return current config if available, otherwise raise
            if self._current_config:
                logger.warning("Using previous configuration due to load failure")
                return self._current_config

            raise

    async def get_configuration(self) -> ModelConfiguration:
        """Get current model configuration."""

        if not self._current_config:
            await self.load_configuration()

        if self._current_config is None:
            raise RuntimeError("Failed to load configuration")

        return self._current_config

    async def get_model_specification(
        self, model_name: str
    ) -> ModelSpecification | None:
        """Get specification for a specific model."""

        config = await self.get_configuration()
        return config.models.get(model_name)

    async def get_provider_configuration(
        self, provider_name: str
    ) -> ProviderConfiguration | None:
        """Get configuration for a specific provider."""

        config = await self.get_configuration()
        return config.providers.get(provider_name)

    async def get_models_for_provider(
        self, provider_name: str
    ) -> dict[str, ModelSpecification]:
        """Get all models for a specific provider."""

        config = await self.get_configuration()
        return config.get_models_for_provider(provider_name)

    async def get_models_by_capability(
        self, capability: ModelCapability
    ) -> dict[str, ModelSpecification]:
        """Get all models that support a capability."""

        config = await self.get_configuration()
        return config.get_models_by_capability(capability)

    async def get_models_by_tier(
        self, tier: ModelTier
    ) -> dict[str, ModelSpecification]:
        """Get all models in a specific tier."""

        config = await self.get_configuration()
        return config.get_models_by_tier(tier)

    async def get_enabled_models(self) -> dict[str, ModelSpecification]:
        """Get all enabled models."""

        config = await self.get_configuration()
        return config.get_enabled_models()

    async def get_enabled_providers(self) -> dict[str, ProviderConfiguration]:
        """Get all enabled providers."""

        config = await self.get_configuration()
        return config.get_enabled_providers()

    def add_change_listener(
        self, listener: Callable[[ConfigurationChangeEvent], None]
    ) -> None:
        """Add a listener for configuration changes."""
        self._change_listeners.append(listener)

    def remove_change_listener(
        self, listener: Callable[[ConfigurationChangeEvent], None]
    ) -> None:
        """Remove a configuration change listener."""
        if listener in self._change_listeners:
            self._change_listeners.remove(listener)

    async def validate_configuration(self, config_path: Path | None = None) -> bool:
        """Validate configuration file without loading it."""

        try:
            config_path = config_path or self.base_config_path
            config_data = await self._load_yaml_file(config_path)

            # Validate with Pydantic
            ModelConfiguration(**config_data)
            return True

        except Exception as e:
            logger.error(f"Configuration validation failed: {e}")
            return False

    async def get_configuration_stats(self) -> dict[str, Any]:
        """Get configuration manager statistics."""

        config = await self.get_configuration()

        stats: dict[str, Any] = {
            "config_manager": {
                "load_count": self.load_count,
                "error_count": self.error_count,
                "last_loaded": (
                    self._config_timestamp.isoformat()
                    if self._config_timestamp
                    else None
                ),
            },
            "configuration": {
                "total_models": len(config.models),
                "enabled_models": len(config.get_enabled_models()),
                "total_providers": len(config.providers),
                "enabled_providers": len(config.get_enabled_providers()),
                "environment": self.environment,
                "version": config.version,
            },
            "models_by_tier": {
                tier.value: len(config.get_models_by_tier(tier)) for tier in ModelTier
            },
            "models_by_provider": {
                provider: len(models)
                for provider, models in {
                    name: config.get_models_for_provider(name)
                    for name in config.providers
                }.items()
            },
        }
        return stats

    async def _load_yaml_file(self, path: Path) -> dict[str, Any]:
        """Load YAML file asynchronously."""

        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")

        # Read file content
        content = await asyncio.get_event_loop().run_in_executor(
            None, path.read_text, "utf-8"
        )

        # Parse YAML with environment variable substitution
        content_with_env = os.path.expandvars(content)

        # Load YAML
        config_data = yaml.safe_load(content_with_env)

        if not config_data:
            raise ValueError(f"Empty or invalid YAML configuration: {path}")

        if not isinstance(config_data, dict):
            raise ValueError(
                f"YAML configuration must be a dictionary, got {type(config_data).__name__}: {path}"
            )

        return config_data

    def _merge_configurations(
        self, base_config: dict[str, Any], override_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge base configuration with environment overrides."""

        # Deep merge function
        def deep_merge(
            base: dict[str, Any], override: dict[str, Any]
        ) -> dict[str, Any]:
            result = base.copy()

            for key, value in override.items():
                if (
                    key in result
                    and isinstance(result[key], dict)
                    and isinstance(value, dict)
                ):
                    # Recursively merge nested dictionaries
                    result[key] = deep_merge(result[key], value)
                else:
                    result[key] = value

            return result

        # Skip merging if no override config
        if not override_config:
            return base_config

        # Check if override_config has 'extends' field
        if override_config.get("extends"):
            logger.info(f"Merging {override_config.get('extends')} with overrides")

        merged = deep_merge(base_config, override_config)

        # Debug: log the merged providers to see what's happening
        logger.debug(f"Base providers: {list(base_config.get('providers', {}).keys())}")
        logger.debug(
            f"Override providers: {list(override_config.get('providers', {}).keys())}"
        )
        logger.debug(f"Merged providers: {list(merged.get('providers', {}).keys())}")

        return merged

    async def _notify_change_listeners(
        self,
        change_type: str,
        old_config: ModelConfiguration | None,
        new_config: ModelConfiguration | None,
    ) -> None:
        """Notify all change listeners of configuration updates."""

        event = ConfigurationChangeEvent(
            change_type=change_type,
            config_path=str(self.base_config_path),
            old_config=old_config,
            new_config=new_config,
        )

        for listener in self._change_listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(event)
                else:
                    listener(event)
            except Exception as e:
                logger.error(f"Configuration change listener failed: {e}")

    async def close(self) -> None:
        """Close the configuration manager and cleanup resources."""

        logger.info("Model configuration manager closed")


class ConfigurationCache:
    """Cache for frequently accessed configuration data."""

    def __init__(self, ttl_seconds: int = 300):
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, Any] = {}
        self._timestamps: dict[str, datetime] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Any | None:
        """Get cached value if not expired."""

        with self._lock:
            if key in self._cache:
                timestamp = self._timestamps.get(key)
                if (
                    timestamp
                    and (datetime.now() - timestamp).total_seconds() < self.ttl_seconds
                ):
                    return self._cache[key]
                else:
                    # Expired - remove from cache
                    del self._cache[key]
                    del self._timestamps[key]

        return None

    def set(self, key: str, value: Any) -> None:
        """Cache a value with timestamp."""

        with self._lock:
            self._cache[key] = value
            self._timestamps[key] = datetime.now()

    def invalidate(self, key: str | None = None) -> None:
        """Invalidate cache entry or entire cache."""

        with self._lock:
            if key:
                self._cache.pop(key, None)
                self._timestamps.pop(key, None)
            else:
                self._cache.clear()
                self._timestamps.clear()


# Global configuration manager instance
_config_manager: ModelConfigManager | None = None


async def get_model_config_manager(
    config: dict[str, Any] | None = None,
) -> ModelConfigManager:
    """Get or create global model configuration manager."""

    global _config_manager

    if not _config_manager:
        if not config:
            raise ValueError("Configuration required for first initialization")

        _config_manager = ModelConfigManager(config)
        await _config_manager.initialize()

    return _config_manager


async def get_model_specification(model_name: str) -> ModelSpecification | None:
    """Get model specification by name."""

    manager = await get_model_config_manager()
    return await manager.get_model_specification(model_name)


async def get_provider_configuration(
    provider_name: str,
) -> ProviderConfiguration | None:
    """Get provider configuration by name."""

    manager = await get_model_config_manager()
    return await manager.get_provider_configuration(provider_name)


async def get_enabled_models() -> dict[str, ModelSpecification]:
    """Get all enabled models."""

    manager = await get_model_config_manager()
    return await manager.get_enabled_models()


async def get_models_by_capability(
    capability: ModelCapability,
) -> dict[str, ModelSpecification]:
    """Get models supporting a specific capability."""

    manager = await get_model_config_manager()
    return await manager.get_models_by_capability(capability)


__all__ = [
    "ConfigurationCache",
    "ConfigurationChangeEvent",
    "ModelConfigManager",
    "get_enabled_models",
    "get_model_config_manager",
    "get_model_specification",
    "get_models_by_capability",
    "get_provider_configuration",
]
