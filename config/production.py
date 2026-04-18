"""
Production environment configuration.

This module contains configuration settings specific to the production
environment. It inherits from base configuration and overrides settings
for optimal performance, security, and reliability in production.
"""

import os

from config.base import (
    AgentConfig,
    BaseConfig,
    DatabaseConfig,
    GeminiConfig,
    MCPConfig,
    MonitoringConfig,
    RedisConfig,
    SecurityConfig,
    TemporalConfig,
)


class ProductionConfig(BaseConfig):
    """Production environment configuration."""
    
    # Application settings
    environment: str = "production"
    debug: bool = False
    
    # API settings
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = os.cpu_count() or 4  # Use all available CPUs
    api_reload: bool = False
    
    # Override MCP settings for production
    mcp: MCPConfig = MCPConfig(
        enabled=True,
        server_host=os.getenv("MCP_SERVER_HOST", "mcp-server"),
        server_port=int(os.getenv("MCP_SERVER_PORT", "9000")),
        client_timeout=30,
        max_retries=3,
        retry_delay=1.0,
        retry_max_delay=60.0,
        enable_fallback=True,
        circuit_breaker_enabled=True,
        circuit_breaker_failure_threshold=5,
        circuit_breaker_recovery_timeout=60,
        connection_pool_size=20,
        connection_pool_overflow=10,
        connection_pool_timeout=30,
        connection_pool_recycle=3600,
    )
    
    # Override Agent settings for production
    agents: AgentConfig = AgentConfig(
        pool_size=10,
        pool_overflow=5,
        task_timeout=300,
        task_retry_attempts=3,
        max_concurrent_tasks=20,
        task_queue_size=200,
        memory_limit_mb=1024,
        cpu_limit=2.0,
        cache_enabled=True,
        cache_ttl=3600,
        cache_max_size=10000,
        mcp_integration_enabled=True,
        mcp_fallback_enabled=True,
    )
    
    # Override Database settings for production
    database: DatabaseConfig = DatabaseConfig(
        host=os.getenv("DB_HOST", "postgres"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME", "research_db"),
        username=os.getenv("DB_USER", "research"),
        password=os.getenv("DB_PASSWORD", ""),  # Must be set via environment
        pool_size=50,
        max_overflow=20,
        pool_timeout=30,
        pool_recycle=3600,
        pool_pre_ping=True,
        statement_timeout=30000,
        lock_timeout=10000,
        idle_in_transaction_timeout=60000,
        auto_vacuum=True,
        backup_enabled=True,
        backup_retention_days=30,
    )
    
    # Override Redis settings for production
    redis: RedisConfig = RedisConfig(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=int(os.getenv("REDIS_DB", "0")),
        password=os.getenv("REDIS_PASSWORD"),
        pool_size=100,
        pool_timeout=20,
        socket_timeout=5,
        socket_connect_timeout=5,
        retry_on_timeout=True,
        retry_on_error=True,
        max_retries=3,
        default_ttl=3600,
        max_memory="1gb",
        eviction_policy="allkeys-lru",
        persistence_enabled=True,
        save_intervals=["900 1", "300 10", "60 10000"],
    )
    
    # Override Temporal settings for production
    temporal: TemporalConfig = TemporalConfig(
        host=os.getenv("TEMPORAL_HOST", "temporal"),
        port=int(os.getenv("TEMPORAL_PORT", "7233")),
        namespace=os.getenv("TEMPORAL_NAMESPACE", "research-platform"),
        worker_concurrency=20,
        worker_max_concurrent_activities=200,
        worker_max_concurrent_workflows=100,
        workflow_execution_timeout=86400,  # 24 hours
        workflow_run_timeout=3600,  # 1 hour
        workflow_task_timeout=10,
        activity_start_to_close_timeout=300,
        activity_heartbeat_timeout=30,
        activity_retry_max_attempts=3,
        connection_timeout=10,
        rpc_timeout=30,
        query_timeout=10,
    )
    
    # Override Gemini settings for production
    gemini: GeminiConfig = GeminiConfig(
        api_key=os.getenv("GEMINI_API_KEY"),  # Must be set via environment
        model=os.getenv("GEMINI_MODEL", "gemini-pro"),
        requests_per_minute=60,
        requests_per_day=10000,
        tokens_per_minute=100000,
        timeout=60,
        max_retries=3,
        retry_delay=1.0,
        exponential_backoff=True,
        temperature=0.7,
        max_output_tokens=4096,
        cache_responses=True,
        cache_ttl=3600,
    )
    
    # Override Monitoring settings for production
    monitoring: MonitoringConfig = MonitoringConfig(
        metrics_enabled=True,
        metrics_port=9090,
        metrics_path="/metrics",
        tracing_enabled=True,
        tracing_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317"),
        tracing_service_name="research-platform",
        tracing_sample_rate=0.1,  # Sample 10% of requests
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        log_format="json",
        log_output="stdout",
        log_file="/var/log/research/app.log",
        log_rotation=True,
        log_retention_days=30,
        health_check_enabled=True,
        health_check_interval=30,
        health_check_timeout=5,
        health_check_unhealthy_threshold=3,
        alerting_enabled=True,
        alert_webhook_url=os.getenv("ALERT_WEBHOOK_URL"),
        alert_email=os.getenv("ALERT_EMAIL"),
    )
    
    # Override Security settings for production
    security: SecurityConfig = SecurityConfig(
        jwt_secret_key=os.getenv("JWT_SECRET_KEY", ""),  # Must be set via environment
        jwt_algorithm="RS256",  # Use RSA in production
        jwt_expiration_minutes=60,
        refresh_token_expiration_days=7,
        rate_limiting_enabled=True,
        rate_limit_per_minute=100,
        rate_limit_per_hour=1000,
        rate_limit_per_day=10000,
        cors_enabled=True,
        cors_origins=os.getenv("CORS_ORIGINS", "").split(",") if os.getenv("CORS_ORIGINS") else [],
        cors_methods=["GET", "POST", "PUT", "DELETE"],
        cors_headers=["Content-Type", "Authorization"],
        security_headers_enabled=True,
        csp_policy="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'",
        secrets_provider=os.getenv("SECRETS_PROVIDER", "vault"),
        vault_url=os.getenv("VAULT_URL"),
        vault_token=os.getenv("VAULT_TOKEN"),
        audit_logging_enabled=True,
        audit_log_file="/var/log/research/audit.log",
    )
    
    # Feature flags for production
    features: dict = {
        "mcp_tools": True,
        "agent_pooling": True,
        "caching": True,
        "rate_limiting": True,
        "health_checks": True,
        "metrics": True,
        "tracing": True,
        "audit_logging": True,
        "debug_endpoints": False,  # Disable debug endpoints in production
        "mock_external_services": False,  # No mocking in production
        "maintenance_mode": False,  # Can be toggled for maintenance
    }
    
    def validate_required_env_vars(self):
        """Validate that all required environment variables are set."""
        required_vars = [
            ("DB_PASSWORD", self.database.password),
            ("GEMINI_API_KEY", self.gemini.api_key),
            ("JWT_SECRET_KEY", self.security.jwt_secret_key),
        ]
        
        missing_vars = []
        for var_name, var_value in required_vars:
            if not var_value:
                missing_vars.append(var_name)
        
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {', '.join(missing_vars)}")
    
    def __init__(self, **kwargs):
        """Initialize production config with validation."""
        super().__init__(**kwargs)
        # Only validate in actual production, not during development
        if os.getenv("VALIDATE_ENV_VARS", "false").lower() == "true":
            self.validate_required_env_vars()


# Create singleton instance
production_config = ProductionConfig()