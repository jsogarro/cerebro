"""
Model Configuration Management Package

Provides dynamic configuration management for Cerebro's foundation models,
replacing hard-coded model specifications with flexible YAML-based configuration.

Key Components:
- ModelConfigManager: Central configuration loader and manager
- Model Schemas: Pydantic validation schemas for configurations
- Hot-reload capabilities for runtime configuration updates
- Environment-specific configuration support
"""

from .model_config_manager import ModelConfigManager
from .model_schemas import (
    ModelSpecification,
    ProviderConfiguration,
    ModelCapability,
    RoutingConfiguration,
    ConfigurationMetadata,
)

__all__ = [
    "ModelConfigManager",
    "ModelSpecification",
    "ProviderConfiguration",
    "ModelCapability",
    "RoutingConfiguration",
    "ConfigurationMetadata",
]
