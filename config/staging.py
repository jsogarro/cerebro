"""
Staging environment configuration.

This module contains configuration settings specific to the staging
environment. It is similar to production but with more verbose logging
and looser resource limits. Environment variables are loaded through
:class:`_StagingEnv` (a pydantic-settings ``BaseSettings``) rather than
direct ``os.getenv`` calls.
"""

from pydantic_settings import BaseSettings

from config.base import MonitoringConfig, SecurityConfig
from config.production import ENV_SETTINGS_CONFIG, ProductionConfig


class _StagingEnv(BaseSettings):
    """Typed env-var binding for staging-only overrides."""

    model_config = ENV_SETTINGS_CONFIG

    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://otel-collector:4317"
    JWT_SECRET_KEY: str = "staging-secret-key"


class StagingConfig(ProductionConfig):
    """Staging environment configuration."""

    # Application settings
    environment: str = "staging"
    debug: bool = False  # Still false, but we enable more logging

    # API settings
    api_workers: int = 2  # Fewer workers than production

    def model_post_init(self, __context: object) -> None:
        """Apply production overrides first, then staging-specific tweaks."""
        super().model_post_init(__context)

        env = _StagingEnv()

        self.monitoring = MonitoringConfig(
            metrics_enabled=True,
            metrics_port=9090,
            tracing_enabled=True,
            tracing_endpoint=env.OTEL_EXPORTER_OTLP_ENDPOINT,
            tracing_service_name="research-platform-staging",
            tracing_sample_rate=0.5,  # Sample 50% of requests in staging
            log_level="DEBUG",  # More verbose logging in staging
            log_format="json",
            log_output="stdout",
            log_file="/var/log/research/staging.log",
            log_rotation=True,
            log_retention_days=7,  # Shorter retention in staging
            health_check_enabled=True,
            health_check_interval=60,  # Less frequent checks than production
            alerting_enabled=False,  # No alerts in staging
        )

        self.security = SecurityConfig(
            jwt_secret_key=env.JWT_SECRET_KEY,
            jwt_algorithm="HS256",  # Simpler algorithm for staging
            jwt_expiration_minutes=120,  # Longer expiration for testing
            rate_limiting_enabled=True,
            rate_limit_per_minute=200,
            rate_limit_per_hour=2000,
            rate_limit_per_day=20000,
            cors_enabled=True,
            cors_origins=["https://staging.example.com", "http://localhost:3000"],
            security_headers_enabled=True,
            audit_logging_enabled=True,
            audit_log_file="/var/log/research/staging-audit.log",
        )

        self.features = {
            "mcp_tools": True,
            "agent_pooling": True,
            "caching": True,
            "rate_limiting": True,
            "health_checks": True,
            "metrics": True,
            "tracing": True,
            "audit_logging": True,
            "debug_endpoints": True,  # Enable debug endpoints in staging
            "mock_external_services": False,
            "maintenance_mode": False,
            "experimental_features": True,
        }

    def validate_required_env_vars(self) -> None:
        """Less strict validation for staging - only critical vars."""
        critical_vars = [
            ("GEMINI_API_KEY", self.gemini.api_key),
        ]

        missing_vars = [
            var_name for var_name, var_value in critical_vars if not var_value
        ]

        if missing_vars:
            print(f"Warning: Missing environment variables: {', '.join(missing_vars)}")
            print("Using default values for staging environment")


