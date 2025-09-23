"""
Configuration module for the Research Platform.

This module provides environment-specific configuration management,
automatically loading the appropriate configuration based on the
ENVIRONMENT variable.
"""

import os
from typing import Optional
from functools import lru_cache

from config.base import BaseConfig
from config.development import DevelopmentConfig
from config.staging import StagingConfig
from config.production import ProductionConfig
from config.testing import TestingConfig


# Configuration class mapping
CONFIG_CLASSES = {
    "development": DevelopmentConfig,
    "staging": StagingConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "test": TestingConfig,  # Alias for testing
    "dev": DevelopmentConfig,  # Alias for development
    "prod": ProductionConfig,  # Alias for production
}


@lru_cache(maxsize=1)
def get_config(environment: Optional[str] = None) -> BaseConfig:
    """
    Get configuration for the specified environment.
    
    Args:
        environment: Environment name. If not provided, uses ENVIRONMENT
                    environment variable, defaulting to 'development'.
    
    Returns:
        Configuration instance for the specified environment.
    
    Raises:
        ValueError: If the environment is not recognized.
    """
    if environment is None:
        environment = os.getenv("ENVIRONMENT", "development")
    
    environment = environment.lower()
    
    if environment not in CONFIG_CLASSES:
        raise ValueError(
            f"Unknown environment: {environment}. "
            f"Valid environments: {', '.join(CONFIG_CLASSES.keys())}"
        )
    
    config_class = CONFIG_CLASSES[environment]
    config = config_class()
    
    # Log configuration loading
    print(f"Loaded configuration for environment: {config.environment}")
    
    return config


def reload_config():
    """Clear the configuration cache to force reload."""
    get_config.cache_clear()


# Export the current configuration
config = get_config()

# Export configuration classes for direct use if needed
__all__ = [
    "config",
    "get_config",
    "reload_config",
    "BaseConfig",
    "DevelopmentConfig",
    "StagingConfig",
    "ProductionConfig",
    "TestingConfig",
]