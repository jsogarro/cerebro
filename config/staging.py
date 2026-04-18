"""
Staging environment configuration.

This module contains configuration settings specific to the staging
environment. It's similar to production but with some debugging features
enabled and less strict resource limits.
"""

import os

from config.base import MonitoringConfig, SecurityConfig
from config.production import ProductionConfig


class StagingConfig(ProductionConfig):
    """Staging environment configuration."""
    
    # Application settings
    environment: str = "staging"
    debug: bool = False  # Still false, but we enable more logging
    
    # API settings
    api_workers: int = 2  # Fewer workers than production
    
    # Override Monitoring for more verbose logging in staging
    monitoring: MonitoringConfig = MonitoringConfig(
        metrics_enabled=True,
        metrics_port=9090,
        tracing_enabled=True,
        tracing_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317"),
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
    
    # Override Security for staging
    security: SecurityConfig = SecurityConfig(
        jwt_secret_key=os.getenv("JWT_SECRET_KEY", "staging-secret-key"),
        jwt_algorithm="HS256",  # Simpler algorithm for staging
        jwt_expiration_minutes=120,  # Longer expiration for testing
        rate_limiting_enabled=True,
        rate_limit_per_minute=200,  # Higher limits for testing
        rate_limit_per_hour=2000,
        rate_limit_per_day=20000,
        cors_enabled=True,
        cors_origins=["https://staging.example.com", "http://localhost:3000"],
        security_headers_enabled=True,
        audit_logging_enabled=True,
        audit_log_file="/var/log/research/staging-audit.log",
    )
    
    # Feature flags for staging
    features: dict = {
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
        "experimental_features": True,  # Test experimental features in staging
    }
    
    def validate_required_env_vars(self):
        """Less strict validation for staging - only critical vars."""
        critical_vars = [
            ("GEMINI_API_KEY", self.gemini.api_key),
        ]
        
        missing_vars = []
        for var_name, var_value in critical_vars:
            if not var_value:
                missing_vars.append(var_name)
        
        if missing_vars:
            print(f"Warning: Missing environment variables: {', '.join(missing_vars)}")
            print("Using default values for staging environment")


# Create singleton instance
staging_config = StagingConfig()