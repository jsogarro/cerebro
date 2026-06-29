"""
Development environment configuration.

This module contains configuration settings specific to the development
environment. It inherits from base configuration and overrides settings
appropriate for local development.
"""

from config.base import (
    AgentConfig,
    BaseConfig,
    DatabaseConfig,
    MCPConfig,
    MonitoringConfig,
    RedisConfig,
    SecurityConfig,
)


class DevelopmentConfig(BaseConfig):
    """Development environment configuration."""
    
    # Application settings
    environment: str = "development"
    debug: bool = True
    
    # API settings
    api_reload: bool = True  # Enable hot reload for development
    api_workers: int = 1  # Single worker for easier debugging
    
    # Override MCP settings for development
    mcp: MCPConfig = MCPConfig(
        enabled=True,
        server_host="localhost",
        server_port=9000,
        client_timeout=60,  # Longer timeout for debugging
        max_retries=1,  # Less retries for faster feedback
        enable_fallback=True,
        circuit_breaker_enabled=False,  # Disable circuit breaker for testing
        connection_pool_size=2,  # Smaller pool for development
    )
    
    # Override Agent settings for development
    agents: AgentConfig = AgentConfig(
        pool_size=2,  # Smaller pool for development
        pool_overflow=1,
        task_timeout=600,  # Longer timeout for debugging
        max_concurrent_tasks=5,
        cache_enabled=True,
        cache_ttl=300,  # Shorter cache for development
        mcp_integration_enabled=True,
        mcp_fallback_enabled=True,
    )
    
    # Override Database settings for development
    database: DatabaseConfig = DatabaseConfig(
        host="localhost",
        port=5432,
        database="research_db_dev",
        username="research",
        password="research123",
        pool_size=5,  # Smaller pool for development
        max_overflow=2,
        statement_timeout=60000,  # Longer timeout for debugging
    )
    
    # Override Redis settings for development
    redis: RedisConfig = RedisConfig(
        host="localhost",
        port=6379,
        db=0,
        pool_size=10,  # Smaller pool for development
        default_ttl=300,  # Shorter TTL for development
        persistence_enabled=False,  # Disable persistence for speed
    )
    
    # Override Monitoring settings for development
    monitoring: MonitoringConfig = MonitoringConfig(
        metrics_enabled=True,
        tracing_enabled=True,
        tracing_sample_rate=1.0,  # Sample all requests in development
        log_level="DEBUG",
        log_format="text",  # Human-readable format for development
        health_check_interval=60,  # Less frequent health checks
        alerting_enabled=False,  # No alerts in development
    )
    
    # Override Security settings for development
    security: SecurityConfig = SecurityConfig(
        jwt_secret_key="dev-secret-key-not-for-production",
        jwt_expiration_minutes=480,  # Longer expiration for development
        rate_limiting_enabled=False,  # Disable rate limiting for testing
        cors_enabled=True,
        cors_origins=["http://localhost:3000", "http://localhost:8080", "*"],  # Allow all origins in dev
        security_headers_enabled=False,  # Disable security headers for development
        audit_logging_enabled=False,  # Disable audit logging for development
    )
    
    # Feature flags for development
    features: dict[str, bool] = {
        "mcp_tools": True,
        "agent_pooling": True,
        "caching": True,
        "rate_limiting": False,  # Disabled for easier testing
        "health_checks": True,
        "metrics": True,
        "tracing": True,
        "audit_logging": False,  # Disabled for development
        "debug_endpoints": True,  # Enable debug endpoints
        "mock_external_services": True,  # Allow mocking external services
    }

