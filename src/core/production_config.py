"""
Production configuration enhancements for the Research Platform.

This module provides production-specific configurations and utilities
that integrate with the main configuration system.
"""

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any

from config import config as app_config


class DeploymentMode(Enum):
    """Deployment mode enumeration."""

    SINGLE_INSTANCE = "single_instance"
    MULTI_INSTANCE = "multi_instance"
    KUBERNETES = "kubernetes"
    DOCKER_SWARM = "docker_swarm"


@dataclass
class ProductionSettings:
    """Production-specific settings."""

    # Deployment settings
    deployment_mode: DeploymentMode = DeploymentMode.KUBERNETES
    instance_id: str = os.getenv("INSTANCE_ID", "default")
    region: str = os.getenv("REGION", "us-east-1")
    availability_zone: str = os.getenv("AVAILABILITY_ZONE", "us-east-1a")

    # Performance settings
    enable_response_compression: bool = True
    compression_level: int = 6
    enable_http2: bool = True
    enable_keepalive: bool = True
    keepalive_timeout: int = 75

    # Resource limits
    max_request_size: int = 10 * 1024 * 1024  # 10MB
    max_upload_size: int = 100 * 1024 * 1024  # 100MB
    max_websocket_connections: int = 1000
    max_concurrent_requests: int = 500

    # Cache settings
    enable_cdn: bool = True
    cdn_url: str = os.getenv("CDN_URL", "")
    cache_control_max_age: int = 3600
    enable_etag: bool = True

    # Database optimization
    enable_query_cache: bool = True
    query_cache_size: int = 100 * 1024 * 1024  # 100MB
    enable_prepared_statements: bool = True
    max_prepared_statements: int = 1000

    # Background jobs
    enable_background_jobs: bool = True
    job_queue_name: str = "research-jobs"
    max_job_retries: int = 3
    job_timeout: int = 3600  # 1 hour

    # Maintenance
    maintenance_mode: bool = False
    maintenance_message: str = "System is under maintenance. Please try again later."
    maintenance_allowed_ips: list[str] = []

    # Feature rollout
    feature_rollout_percentage: dict[str, int] | None = None

    def __post_init__(self) -> None:
        """Initialize feature rollout percentages."""
        if self.feature_rollout_percentage is None:
            self.feature_rollout_percentage = {
                "new_ui": 100,
                "advanced_search": 100,
                "realtime_collaboration": 50,
                "experimental_agents": 10,
            }


class ProductionOptimizations:
    """Production optimization utilities."""

    @staticmethod
    def get_database_pool_settings() -> dict[str, Any]:
        """Get optimized database pool settings for production."""
        return {
            "pool_size": app_config.database.pool_size,
            "max_overflow": app_config.database.max_overflow,
            "pool_timeout": app_config.database.pool_timeout,
            "pool_recycle": app_config.database.pool_recycle,
            "pool_pre_ping": app_config.database.pool_pre_ping,
            "echo": False,  # Disable SQL echo in production
            "echo_pool": False,
            "query_cache_size": 1024,
            "connect_args": {
                "server_settings": {
                    "application_name": f"research-platform-{app_config.environment}",
                    "jit": "on",
                },
                "command_timeout": app_config.database.statement_timeout,
                "prepared_statement_cache_size": 0,  # Disable to prevent memory leaks
                "prepared_statement_name_func": lambda: f"stmt_{os.getpid()}",
            },
        }

    @staticmethod
    def get_redis_pool_settings() -> dict[str, Any]:
        """Get optimized Redis pool settings for production."""
        return {
            "max_connections": app_config.redis.pool_size,
            "decode_responses": True,
            "socket_timeout": app_config.redis.socket_timeout,
            "socket_connect_timeout": app_config.redis.socket_connect_timeout,
            "socket_keepalive": True,
            "socket_keepalive_options": {
                1: 1,  # TCP_KEEPIDLE
                2: 2,  # TCP_KEEPINTVL
                3: 3,  # TCP_KEEPCNT
            },
            "retry_on_timeout": app_config.redis.retry_on_timeout,
            "retry_on_error": [ConnectionError, TimeoutError],
            "health_check_interval": 30,
        }

    @staticmethod
    def get_mcp_client_settings() -> dict[str, Any]:
        """Get optimized MCP client settings for production."""
        return {
            "timeout": app_config.mcp.client_timeout,
            "max_retries": app_config.mcp.max_retries,
            "retry_delay": app_config.mcp.retry_delay,
            "retry_max_delay": app_config.mcp.retry_max_delay,
            "retry_exponential_base": app_config.mcp.retry_exponential_base,
            "pool_size": app_config.mcp.connection_pool_size,
            "pool_overflow": app_config.mcp.connection_pool_overflow,
            "pool_timeout": app_config.mcp.connection_pool_timeout,
            "pool_recycle": app_config.mcp.connection_pool_recycle,
            "circuit_breaker": {
                "enabled": app_config.mcp.circuit_breaker_enabled,
                "failure_threshold": app_config.mcp.circuit_breaker_failure_threshold,
                "recovery_timeout": app_config.mcp.circuit_breaker_recovery_timeout,
                "expected_exception": app_config.mcp.circuit_breaker_expected_exception,
            },
        }

    @staticmethod
    def get_agent_pool_settings() -> dict[str, Any]:
        """Get optimized agent pool settings for production."""
        return {
            "min_size": app_config.agents.pool_size,
            "max_size": app_config.agents.pool_size + app_config.agents.pool_overflow,
            "max_tasks_per_agent": app_config.agents.max_concurrent_tasks,
            "task_timeout": app_config.agents.task_timeout,
            "task_retry_attempts": app_config.agents.task_retry_attempts,
            "recycle_agents_after": 1000,  # Recycle after 1000 tasks
            "health_check_interval": 60,
            "memory_limit": app_config.agents.memory_limit_mb * 1024 * 1024,
            "cpu_limit": app_config.agents.cpu_limit,
        }

    @staticmethod
    def get_temporal_worker_settings() -> dict[str, Any]:
        """Get optimized Temporal worker settings for production."""
        return {
            "task_queue": "research-tasks",
            "max_concurrent_activities": app_config.temporal.worker_max_concurrent_activities,
            "max_concurrent_workflows": app_config.temporal.worker_max_concurrent_workflows,
            "max_concurrent_activity_task_pollers": 5,
            "max_concurrent_workflow_task_pollers": 5,
            "max_cached_workflows": 100,
            "sticky_schedule_to_start_timeout": 10,
            "worker_stop_timeout": 30,
            "enable_logging_in_replay": False,
            "disable_eager_activities": False,
            "max_heartbeat_throttle_interval": 30,
            "default_heartbeat_throttle_interval": 10,
        }


class ProductionValidation:
    """Production configuration validation."""

    @staticmethod
    def validate_environment() -> dict[str, bool]:
        """Validate production environment configuration."""
        validations = {}

        # Check required environment variables
        required_vars = [
            "DATABASE_URL",
            "REDIS_URL",
            "GEMINI_API_KEY",
            "JWT_SECRET_KEY",
            "TEMPORAL_HOST",
        ]

        for var in required_vars:
            validations[f"env_{var}"] = bool(os.getenv(var))

        # Check service connectivity
        validations["database_url_valid"] = bool(app_config.database.url)
        validations["redis_url_valid"] = bool(app_config.redis.url)
        validations["temporal_target_valid"] = bool(app_config.temporal.target)

        # Check security settings
        validations["jwt_secret_secure"] = (
            app_config.security.jwt_secret_key != "change-me-in-production"
        )
        validations["cors_configured"] = bool(app_config.security.cors_origins)
        validations["rate_limiting_enabled"] = app_config.security.rate_limiting_enabled

        # Check monitoring settings
        validations["metrics_enabled"] = app_config.monitoring.metrics_enabled
        validations["tracing_enabled"] = app_config.monitoring.tracing_enabled
        validations["health_checks_enabled"] = (
            app_config.monitoring.health_check_enabled
        )

        # Check feature flags
        validations["production_features"] = (
            app_config.features.get("debug_endpoints", True) is False
        )

        return validations

    @staticmethod
    def is_production_ready() -> tuple[bool, list[str]]:
        """
        Check if the system is production ready.

        Returns:
            Tuple of (is_ready, list_of_issues)
        """
        validations = ProductionValidation.validate_environment()
        issues = []

        for check, passed in validations.items():
            if not passed:
                issues.append(f"Failed validation: {check}")

        is_ready = len(issues) == 0
        return is_ready, issues


# Create singleton instances
production_settings = ProductionSettings()
production_optimizations = ProductionOptimizations()
production_validation = ProductionValidation()


def get_production_config() -> dict[str, Any]:
    """
    Get complete production configuration.

    Returns:
        Dictionary containing all production settings.
    """
    return {
        "environment": app_config.environment,
        "deployment": {
            "mode": production_settings.deployment_mode.value,
            "instance_id": production_settings.instance_id,
            "region": production_settings.region,
        },
        "database": production_optimizations.get_database_pool_settings(),
        "redis": production_optimizations.get_redis_pool_settings(),
        "mcp": production_optimizations.get_mcp_client_settings(),
        "agents": production_optimizations.get_agent_pool_settings(),
        "temporal": production_optimizations.get_temporal_worker_settings(),
        "validation": production_validation.validate_environment(),
        "features": app_config.features,
    }


__all__ = [
    "DeploymentMode",
    "ProductionOptimizations",
    "ProductionSettings",
    "ProductionValidation",
    "get_production_config",
    "production_optimizations",
    "production_settings",
    "production_validation",
]
