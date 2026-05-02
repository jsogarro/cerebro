"""
Testing environment configuration.

This module contains configuration settings specific to the testing
environment. Optimized for fast test execution with minimal external
dependencies.
"""

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


class TestingConfig(BaseConfig):
    """Testing environment configuration."""
    
    # Application settings
    environment: str = "testing"
    debug: bool = True
    
    # API settings
    api_host: str = "127.0.0.1"
    api_port: int = 8001  # Different port to avoid conflicts
    api_workers: int = 1
    api_reload: bool = False
    
    # Override MCP settings for testing
    mcp: MCPConfig = MCPConfig(
        enabled=False,  # Disable MCP in tests by default
        server_host="localhost",
        server_port=9001,  # Different port for testing
        client_timeout=5,  # Short timeout for tests
        max_retries=0,  # No retries in tests
        enable_fallback=True,  # Always use fallback in tests
        circuit_breaker_enabled=False,
        connection_pool_size=1,
    )
    
    # Override Agent settings for testing
    agents: AgentConfig = AgentConfig(
        pool_size=1,
        pool_overflow=0,
        task_timeout=10,  # Very short timeout for tests
        task_retry_attempts=0,
        max_concurrent_tasks=1,
        task_queue_size=10,
        cache_enabled=False,  # Disable cache in tests
        mcp_integration_enabled=False,  # Use mocks instead
        mcp_fallback_enabled=True,
    )
    
    # Override Database settings for testing
    database: DatabaseConfig = DatabaseConfig(
        host="localhost",
        port=5433,  # Different port for test database
        database="research_db_test",
        username="test_user",
        password="test_password",
        pool_size=1,
        max_overflow=0,
        pool_timeout=5,
        pool_recycle=0,  # No recycling in tests
        pool_pre_ping=False,
        statement_timeout=5000,  # 5 seconds for tests
    )
    
    # Override Redis settings for testing
    redis: RedisConfig = RedisConfig(
        host="localhost",
        port=6380,  # Different port for test Redis
        db=15,  # Use different DB for tests
        password=None,
        pool_size=1,
        pool_timeout=5,
        default_ttl=60,  # Short TTL for tests
        persistence_enabled=False,  # No persistence in tests
    )
    
    # Override Temporal settings for testing
    temporal: TemporalConfig = TemporalConfig(
        host="localhost",
        port=7234,  # Different port for test Temporal
        namespace="test",
        worker_concurrency=1,
        worker_max_concurrent_activities=1,
        worker_max_concurrent_workflows=1,
        workflow_execution_timeout=60,  # 1 minute for tests
        workflow_run_timeout=30,
        workflow_task_timeout=5,
        activity_start_to_close_timeout=10,
        activity_heartbeat_timeout=5,
        activity_retry_max_attempts=0,  # No retries in tests
    )
    
    # Override Gemini settings for testing
    gemini: GeminiConfig = GeminiConfig(
        api_key="test-api-key",
        model="gemini-test",
        requests_per_minute=1000,  # No rate limiting in tests
        timeout=5,
        max_retries=0,
        cache_responses=False,  # No caching in tests
    )
    
    # Override Monitoring settings for testing
    monitoring: MonitoringConfig = MonitoringConfig(
        metrics_enabled=False,  # Disable metrics in tests
        tracing_enabled=False,  # Disable tracing in tests
        log_level="ERROR",  # Only log errors in tests
        log_format="text",
        log_output="null",  # Suppress logs in tests
        health_check_enabled=False,  # Disable health checks in tests
        alerting_enabled=False,
    )
    
    # Override Security settings for testing
    security: SecurityConfig = SecurityConfig(
        jwt_secret_key="test-secret-key",
        jwt_algorithm="HS256",
        jwt_expiration_minutes=5,  # Short expiration for tests
        rate_limiting_enabled=False,  # No rate limiting in tests
        cors_enabled=False,  # Disable CORS in tests
        security_headers_enabled=False,
        audit_logging_enabled=False,  # No audit logging in tests
    )
    
    # Feature flags for testing
    features: dict[str, bool] = {
        "mcp_tools": False,  # Use mocks
        "agent_pooling": False,
        "caching": False,
        "rate_limiting": False,
        "health_checks": False,
        "metrics": False,
        "tracing": False,
        "audit_logging": False,
        "debug_endpoints": True,
        "mock_external_services": True,  # Always mock external services
        "test_mode": True,  # Special flag for test-only features
    }

