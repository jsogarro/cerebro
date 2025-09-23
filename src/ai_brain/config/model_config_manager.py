"""
Model Configuration Manager

Central manager for loading, validating, and managing model configurations.
Supports hot-reloading, environment-specific overrides, and runtime
configuration updates without system restart.

Features:
- YAML configuration loading with inheritance
- Environment-specific overrides
- Hot-reloading with file system watching
- Configuration validation and error handling
- Thread-safe configuration updates
- Configuration change notifications
"""

import asyncio
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Set
import yaml

logger = logging.getLogger(__name__)

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    WATCHDOG_AVAILABLE = True
except ImportError:
    Observer = None
    FileSystemEventHandler = None
    WATCHDOG_AVAILABLE = False
    logger.warning("watchdog not available - file watching disabled")

from .model_schemas import (
    ModelConfiguration,
    ModelSpecification,
    ProviderConfiguration,
    ModelTier,
    ModelCapability,
)

logger = logging.getLogger(__name__)


class ConfigurationChangeEvent:
    """Event for configuration changes."""

    def __init__(
        self,
        change_type: str,
        config_path: str,
        old_config: Optional[ModelConfiguration] = None,
        new_config: Optional[ModelConfiguration] = None,
    ):
        self.change_type = change_type  # created, modified, deleted
        self.config_path = config_path
        self.old_config = old_config
        self.new_config = new_config
        self.timestamp = datetime.now()


class ConfigFileWatcher:
    """File system watcher for configuration changes."""

    def __init__(self, config_manager: "ModelConfigManager"):
        self.config_manager = config_manager
        self.debounce_delay = 1.0  # Seconds to wait before processing changes
        self.pending_changes: Set[str] = set()
        self._debounce_task = None

        # Only inherit from FileSystemEventHandler if available
        if WATCHDOG_AVAILABLE:
            self.__class__.__bases__ = (FileSystemEventHandler,)

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith((".yaml", ".yml")):
            self.pending_changes.add(event.src_path)
            self._schedule_reload()

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith((".yaml", ".yml")):
            self.pending_changes.add(event.src_path)
            self._schedule_reload()

    def _schedule_reload(self):
        """Schedule a debounced reload of configurations."""

        if self._debounce_task:
            self._debounce_task.cancel()

        self._debounce_task = asyncio.create_task(self._debounced_reload())

    async def _debounced_reload(self):
        """Reload configurations after debounce delay."""

        await asyncio.sleep(self.debounce_delay)

        if self.pending_changes:
            logger.info(f"Configuration files changed: {self.pending_changes}")
            await self.config_manager._reload_configurations()
            self.pending_changes.clear()


class ModelConfigManager:
    """
    Central manager for model configurations.

    Provides thread-safe access to model configurations with support for
    hot-reloading, validation, and environment-specific overrides.
    """

    def __init__(self, config: Dict[str, Any]):
        """Initialize model configuration manager."""
        self.config = config

        # Configuration paths
        self.config_dir = Path(config.get("config_dir", "configs/models"))
        self.base_config_path = self.config_dir / "base.yaml"
        self.environment = config.get("environment", "development")
        self.env_config_path = self.config_dir / f"{self.environment}.yaml"

        # Hot-reload configuration
        self.enable_hot_reload = config.get("enable_hot_reload", True)
        self.watch_subdirectories = config.get("watch_subdirectories", True)

        # Current configuration
        self._lock = threading.RLock()
        self._current_config: Optional[ModelConfiguration] = None
        self._config_timestamp: Optional[datetime] = None

        # Change notifications
        self._change_listeners: List[Callable[[ConfigurationChangeEvent], None]] = []

        # File watcher
        self._observer = None
        self._watcher = None

        # Performance tracking
        self.load_count = 0
        self.reload_count = 0
        self.error_count = 0

    async def initialize(self):
        """Initialize the configuration manager."""

        logger.info("Initializing model configuration manager...")

        # Load initial configuration
        await self.load_configuration()

        # Start file watcher if hot-reload is enabled
        if self.enable_hot_reload:
            await self._start_file_watcher()

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

        return self._current_config

    async def get_model_specification(
        self, model_name: str
    ) -> Optional[ModelSpecification]:
        """Get specification for a specific model."""

        config = await self.get_configuration()
        return config.models.get(model_name)

    async def get_provider_configuration(
        self, provider_name: str
    ) -> Optional[ProviderConfiguration]:
        """Get configuration for a specific provider."""

        config = await self.get_configuration()
        return config.providers.get(provider_name)

    async def get_models_for_provider(
        self, provider_name: str
    ) -> Dict[str, ModelSpecification]:
        """Get all models for a specific provider."""

        config = await self.get_configuration()
        return config.get_models_for_provider(provider_name)

    async def get_models_by_capability(
        self, capability: ModelCapability
    ) -> Dict[str, ModelSpecification]:
        """Get all models that support a capability."""

        config = await self.get_configuration()
        return config.get_models_by_capability(capability)

    async def get_models_by_tier(
        self, tier: ModelTier
    ) -> Dict[str, ModelSpecification]:
        """Get all models in a specific tier."""

        config = await self.get_configuration()
        return config.get_models_by_tier(tier)

    async def get_enabled_models(self) -> Dict[str, ModelSpecification]:
        """Get all enabled models."""

        config = await self.get_configuration()
        return config.get_enabled_models()

    async def get_enabled_providers(self) -> Dict[str, ProviderConfiguration]:
        """Get all enabled providers."""

        config = await self.get_configuration()
        return config.get_enabled_providers()

    def add_change_listener(self, listener: Callable[[ConfigurationChangeEvent], None]):
        """Add a listener for configuration changes."""
        self._change_listeners.append(listener)

    def remove_change_listener(
        self, listener: Callable[[ConfigurationChangeEvent], None]
    ):
        """Remove a configuration change listener."""
        if listener in self._change_listeners:
            self._change_listeners.remove(listener)

    async def validate_configuration(self, config_path: Optional[Path] = None) -> bool:
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

    async def get_configuration_stats(self) -> Dict[str, Any]:
        """Get configuration manager statistics."""

        config = await self.get_configuration()

        return {
            "config_manager": {
                "load_count": self.load_count,
                "reload_count": self.reload_count,
                "error_count": self.error_count,
                "hot_reload_enabled": self.enable_hot_reload,
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
                    for name in config.providers.keys()
                }.items()
            },
        }

    async def _load_yaml_file(self, path: Path) -> Dict[str, Any]:
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

        return config_data

    def _merge_configurations(
        self, base_config: Dict[str, Any], override_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Merge base configuration with environment overrides."""

        # Deep merge function
        def deep_merge(base: Dict, override: Dict) -> Dict:
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

    async def _start_file_watcher(self):
        """Start file system watcher for hot-reload."""

        if not WATCHDOG_AVAILABLE:
            logger.warning("Watchdog not available - hot-reload disabled")
            return

        try:
            self._observer = Observer()
            self._watcher = ConfigFileWatcher(self)

            # Watch config directory
            self._observer.schedule(
                self._watcher, str(self.config_dir), recursive=self.watch_subdirectories
            )

            self._observer.start()
            logger.info(f"File watcher started for {self.config_dir}")

        except Exception as e:
            logger.error(f"Failed to start file watcher: {e}")
            self._observer = None

    async def _reload_configurations(self):
        """Reload configurations from files."""

        try:
            old_config = self._current_config
            new_config = await self.load_configuration()

            self.reload_count += 1
            logger.info("Configuration hot-reloaded successfully")

        except Exception as e:
            logger.error(f"Configuration reload failed: {e}")

    async def _notify_change_listeners(
        self,
        change_type: str,
        old_config: Optional[ModelConfiguration],
        new_config: Optional[ModelConfiguration],
    ):
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

    async def close(self):
        """Close the configuration manager and cleanup resources."""

        if self._observer:
            self._observer.stop()
            self._observer.join()

        logger.info("Model configuration manager closed")


class ConfigurationCache:
    """Cache for frequently accessed configuration data."""

    def __init__(self, ttl_seconds: int = 300):
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, Any] = {}
        self._timestamps: Dict[str, datetime] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[Any]:
        """Get cached value if not expired."""

        with self._lock:
            if key in self._cache:
                timestamp = self._timestamps.get(key)
                if (
                    timestamp
                    and (datetime.now() - timestamp).seconds < self.ttl_seconds
                ):
                    return self._cache[key]
                else:
                    # Expired - remove from cache
                    del self._cache[key]
                    del self._timestamps[key]

        return None

    def set(self, key: str, value: Any):
        """Cache a value with timestamp."""

        with self._lock:
            self._cache[key] = value
            self._timestamps[key] = datetime.now()

    def invalidate(self, key: Optional[str] = None):
        """Invalidate cache entry or entire cache."""

        with self._lock:
            if key:
                self._cache.pop(key, None)
                self._timestamps.pop(key, None)
            else:
                self._cache.clear()
                self._timestamps.clear()


# Global configuration manager instance
_config_manager: Optional[ModelConfigManager] = None


async def get_model_config_manager(
    config: Optional[Dict[str, Any]] = None,
) -> ModelConfigManager:
    """Get or create global model configuration manager."""

    global _config_manager

    if not _config_manager:
        if not config:
            raise ValueError("Configuration required for first initialization")

        _config_manager = ModelConfigManager(config)
        await _config_manager.initialize()

    return _config_manager


async def get_model_specification(model_name: str) -> Optional[ModelSpecification]:
    """Get model specification by name."""

    manager = await get_model_config_manager()
    return await manager.get_model_specification(model_name)


async def get_provider_configuration(
    provider_name: str,
) -> Optional[ProviderConfiguration]:
    """Get provider configuration by name."""

    manager = await get_model_config_manager()
    return await manager.get_provider_configuration(provider_name)


async def get_enabled_models() -> Dict[str, ModelSpecification]:
    """Get all enabled models."""

    manager = await get_model_config_manager()
    return await manager.get_enabled_models()


async def get_models_by_capability(
    capability: ModelCapability,
) -> Dict[str, ModelSpecification]:
    """Get models supporting a specific capability."""

    manager = await get_model_config_manager()
    return await manager.get_models_by_capability(capability)


__all__ = [
    "ModelConfigManager",
    "ConfigurationChangeEvent",
    "ConfigFileWatcher",
    "ConfigurationCache",
    "get_model_config_manager",
    "get_model_specification",
    "get_provider_configuration",
    "get_enabled_models",
    "get_models_by_capability",
]
