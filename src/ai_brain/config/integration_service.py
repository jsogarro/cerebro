"""
Configuration Integration Service

Provides integration between the model configuration system and the rest of
the Cerebro AI Brain components. Handles initialization, configuration updates,
and coordination between different system components.

This service acts as the glue between:
- ModelConfigManager (configuration loading)
- Model Providers (dynamic model specifications)
- MASR Router (cost optimization and routing)
- Memory System (configuration for different memory tiers)
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List, Set
from datetime import datetime

from .model_config_manager import ModelConfigManager, ConfigurationChangeEvent
from .model_schemas import ModelConfiguration, ModelSpecification, ProviderConfiguration
from ..router.masr import MASRouter
from ..router.cost_optimizer import CostOptimizer
from ..providers.model_router import ModelRouter

logger = logging.getLogger(__name__)


class ConfigurationIntegrationService:
    """
    Integrates configuration management across all AI Brain components.

    Responsibilities:
    - Initialize configuration manager and components
    - Coordinate configuration updates across system
    - Handle hot-reload and configuration changes
    - Maintain component health and consistency
    """

    def __init__(self, config: Dict[str, Any]):
        """Initialize configuration integration service."""
        self.config = config

        # Core components
        self.model_config_manager: Optional[ModelConfigManager] = None
        self.masr_router: Optional[MASRouter] = None
        self.cost_optimizer: Optional[CostOptimizer] = None
        self.model_router: Optional[ModelRouter] = None

        # Component registry
        self._components: Dict[str, Any] = {}
        self._configuration_dependent_components: Set[str] = set()

        # Status tracking
        self.initialized = False
        self.last_update = None
        self.update_count = 0

    async def initialize(self):
        """Initialize all configuration-dependent components."""

        logger.info("Initializing configuration integration service...")

        try:
            # Initialize model configuration manager
            self.model_config_manager = ModelConfigManager(
                self.config.get("model_config_manager", {})
            )
            await self.model_config_manager.initialize()
            self._components["model_config_manager"] = self.model_config_manager

            # Register for configuration change notifications
            self.model_config_manager.add_change_listener(self._on_configuration_change)

            # Initialize MASR with configuration manager
            masr_config = self.config.get("masr", {})
            masr_config["model_config_manager"] = self.model_config_manager
            self.masr_router = MASRouter(masr_config)
            self._components["masr_router"] = self.masr_router
            self._configuration_dependent_components.add("masr_router")

            # Initialize cost optimizer with dynamic configuration
            cost_config = self.config.get("cost_optimizer", {})
            cost_config["model_config_manager"] = self.model_config_manager
            self.cost_optimizer = CostOptimizer(cost_config)
            self._components["cost_optimizer"] = self.cost_optimizer
            self._configuration_dependent_components.add("cost_optimizer")

            # Initialize model router with configuration manager
            router_config = self.config.get("model_router", {})
            router_config["model_config_manager"] = self.model_config_manager
            self.model_router = ModelRouter(router_config)
            self._components["model_router"] = self.model_router
            self._configuration_dependent_components.add("model_router")

            self.initialized = True
            self.last_update = datetime.now()

            logger.info("Configuration integration service initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize configuration integration service: {e}")
            raise

    async def _on_configuration_change(self, event: ConfigurationChangeEvent):
        """Handle configuration change events."""

        logger.info(f"Configuration change detected: {event.change_type}")

        try:
            # Update all configuration-dependent components
            await self._update_components(event)

            self.update_count += 1
            self.last_update = datetime.now()

            logger.info("Configuration update completed successfully")

        except Exception as e:
            logger.error(f"Failed to handle configuration change: {e}")

    async def _update_components(self, event: ConfigurationChangeEvent):
        """Update components after configuration change."""

        # Update MASR router
        if (
            self.masr_router
            and "masr_router" in self._configuration_dependent_components
        ):
            await self._update_masr_router(event)

        # Update cost optimizer
        if (
            self.cost_optimizer
            and "cost_optimizer" in self._configuration_dependent_components
        ):
            await self._update_cost_optimizer(event)

        # Update model router
        if (
            self.model_router
            and "model_router" in self._configuration_dependent_components
        ):
            await self._update_model_router(event)

    async def _update_masr_router(self, event: ConfigurationChangeEvent):
        """Update MASR router with new configuration."""

        try:
            # MASR router needs to know about model changes for routing decisions
            if hasattr(self.masr_router, "reload_configuration"):
                await self.masr_router.reload_configuration()
            else:
                logger.info(
                    "MASR router doesn't support hot-reload, restart may be needed"
                )
        except Exception as e:
            logger.error(f"Failed to update MASR router: {e}")

    async def _update_cost_optimizer(self, event: ConfigurationChangeEvent):
        """Update cost optimizer with new model specifications."""

        try:
            # Cost optimizer needs fresh model specifications for accurate cost calculation
            if hasattr(self.cost_optimizer, "reload_models"):
                await self.cost_optimizer.reload_models()
            else:
                logger.info(
                    "Cost optimizer doesn't support hot-reload, restart may be needed"
                )
        except Exception as e:
            logger.error(f"Failed to update cost optimizer: {e}")

    async def _update_model_router(self, event: ConfigurationChangeEvent):
        """Update model router with new provider configurations."""

        try:
            # Model router needs to refresh provider registry
            if hasattr(self.model_router, "reload_providers"):
                await self.model_router.reload_providers()
            else:
                logger.info(
                    "Model router doesn't support hot-reload, restart may be needed"
                )
        except Exception as e:
            logger.error(f"Failed to update model router: {e}")

    async def get_current_configuration(self) -> ModelConfiguration:
        """Get the current model configuration."""

        if not self.model_config_manager:
            raise RuntimeError("Configuration manager not initialized")

        return await self.model_config_manager.get_configuration()

    async def get_enabled_models(self) -> Dict[str, ModelSpecification]:
        """Get all currently enabled models."""

        if not self.model_config_manager:
            raise RuntimeError("Configuration manager not initialized")

        return await self.model_config_manager.get_enabled_models()

    async def get_enabled_providers(self) -> Dict[str, ProviderConfiguration]:
        """Get all currently enabled providers."""

        if not self.model_config_manager:
            raise RuntimeError("Configuration manager not initialized")

        return await self.model_config_manager.get_enabled_providers()

    async def validate_system_configuration(self) -> Dict[str, Any]:
        """Validate the entire system configuration."""

        validation_results = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "component_status": {},
        }

        try:
            # Validate model configuration
            config = await self.get_current_configuration()

            # Check that all enabled models have valid providers
            enabled_models = config.get_enabled_models()
            enabled_providers = config.get_enabled_providers()

            for model_name, model_spec in enabled_models.items():
                if model_spec.provider not in enabled_providers:
                    validation_results["errors"].append(
                        f"Model '{model_name}' references disabled provider '{model_spec.provider}'"
                    )
                    validation_results["valid"] = False

            # Check provider connectivity
            for provider_name, provider_config in enabled_providers.items():
                component_status = await self._validate_provider_connectivity(
                    provider_name, provider_config
                )
                validation_results["component_status"][provider_name] = component_status

                if not component_status["reachable"]:
                    validation_results["warnings"].append(
                        f"Provider '{provider_name}' is not reachable"
                    )

            # Validate component health
            for component_name, component in self._components.items():
                if hasattr(component, "health_check"):
                    try:
                        health = await component.health_check()
                        validation_results["component_status"][component_name] = {
                            "healthy": getattr(health, "healthy", True),
                            "status": "ok",
                        }
                    except Exception as e:
                        validation_results["component_status"][component_name] = {
                            "healthy": False,
                            "error": str(e),
                        }
                        validation_results["warnings"].append(
                            f"Component '{component_name}' health check failed: {e}"
                        )

        except Exception as e:
            validation_results["valid"] = False
            validation_results["errors"].append(f"Configuration validation failed: {e}")

        return validation_results

    async def _validate_provider_connectivity(
        self, provider_name: str, provider_config: ProviderConfiguration
    ) -> Dict[str, Any]:
        """Validate that a provider is reachable."""

        try:
            # Simple connectivity check
            import httpx

            health_endpoint = provider_config.health_check_endpoint
            if health_endpoint:
                full_url = f"{provider_config.api_endpoint.rstrip('/')}/{health_endpoint.lstrip('/')}"
            else:
                full_url = provider_config.api_endpoint

            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(full_url)

                return {
                    "reachable": response.status_code < 500,
                    "status_code": response.status_code,
                    "response_time_ms": 0,  # Would measure actual response time
                }

        except Exception as e:
            return {"reachable": False, "error": str(e), "status_code": None}

    async def get_integration_stats(self) -> Dict[str, Any]:
        """Get integration service statistics."""

        stats = {
            "service": {
                "initialized": self.initialized,
                "last_update": (
                    self.last_update.isoformat() if self.last_update else None
                ),
                "update_count": self.update_count,
                "components_count": len(self._components),
                "config_dependent_components": len(
                    self._configuration_dependent_components
                ),
            }
        }

        # Get component statistics
        for component_name, component in self._components.items():
            if hasattr(component, "get_stats") or hasattr(
                component, "get_configuration_stats"
            ):
                try:
                    if hasattr(component, "get_configuration_stats"):
                        component_stats = await component.get_configuration_stats()
                    else:
                        component_stats = await component.get_stats()

                    stats[component_name] = component_stats
                except Exception as e:
                    stats[component_name] = {"error": str(e)}

        return stats

    async def close(self):
        """Close the integration service and all components."""

        logger.info("Closing configuration integration service...")

        # Close all components
        for component_name, component in self._components.items():
            if hasattr(component, "close"):
                try:
                    await component.close()
                    logger.debug(f"Closed component: {component_name}")
                except Exception as e:
                    logger.error(f"Failed to close component {component_name}: {e}")

        self._components.clear()
        self._configuration_dependent_components.clear()

        logger.info("Configuration integration service closed")


# Global integration service instance
_integration_service: Optional[ConfigurationIntegrationService] = None


async def get_integration_service(
    config: Optional[Dict[str, Any]] = None,
) -> ConfigurationIntegrationService:
    """Get or create global configuration integration service."""

    global _integration_service

    if not _integration_service:
        if not config:
            raise ValueError("Configuration required for first initialization")

        _integration_service = ConfigurationIntegrationService(config)
        await _integration_service.initialize()

    return _integration_service


async def get_model_config_manager() -> ModelConfigManager:
    """Get the model configuration manager from integration service."""

    service = await get_integration_service()

    if not service.model_config_manager:
        raise RuntimeError("Model configuration manager not available")

    return service.model_config_manager


__all__ = [
    "ConfigurationIntegrationService",
    "get_integration_service",
    "get_model_config_manager",
]
