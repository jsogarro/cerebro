"""
Base configuration settings shared across all environments.

This module contains the foundational configuration that all environments
inherit from. Environment-specific configs override these defaults.
"""

from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPConfig(BaseModel):
    """MCP (Model Context Protocol) configuration."""
    
    enabled: bool = Field(default=True, description="Enable MCP tools")
    server_host: str = Field(default="localhost", description="MCP server host")
    server_port: int = Field(default=9000, description="MCP server port")
    client_timeout: int = Field(default=30, description="Client timeout in seconds")
    max_retries: int = Field(default=3, description="Maximum retry attempts")
    retry_delay: float = Field(default=1.0, description="Initial retry delay in seconds")
    retry_max_delay: float = Field(default=60.0, description="Maximum retry delay")
    retry_exponential_base: float = Field(default=2.0, description="Exponential backoff base")
    enable_fallback: bool = Field(default=True, description="Enable fallback mechanisms")
    
    # Circuit breaker settings
    circuit_breaker_enabled: bool = Field(default=True)
    circuit_breaker_failure_threshold: int = Field(default=5)
    circuit_breaker_recovery_timeout: int = Field(default=60)
    circuit_breaker_expected_exception: str | None = Field(default=None)
    
    # Connection pool settings
    connection_pool_size: int = Field(default=10)
    connection_pool_overflow: int = Field(default=5)
    connection_pool_timeout: int = Field(default=30)
    connection_pool_recycle: int = Field(default=3600)
    
    # Tool-specific settings
    tools: dict[str, dict[str, Any]] = Field(default_factory=lambda: {
        "academic_search": {
            "enabled": True,
            "max_results": 50,
            "databases": ["arxiv", "pubmed"],
            "cache_ttl": 3600
        },
        "citation": {
            "enabled": True,
            "supported_styles": ["APA", "MLA", "Chicago"],
            "doi_resolution": True,
            "cache_ttl": 86400
        },
        "statistics": {
            "enabled": True,
            "max_data_points": 10000,
            "operations": ["descriptive", "correlation", "hypothesis"],
            "cache_ttl": 1800
        },
        "knowledge_graph": {
            "enabled": True,
            "max_entities": 1000,
            "max_relationships": 5000,
            "cache_ttl": 3600
        }
    })


class AgentConfig(BaseModel):
    """Agent configuration settings."""
    
    # Agent pool settings
    pool_size: int = Field(default=5, description="Number of agents in pool")
    pool_overflow: int = Field(default=2, description="Additional agents on demand")
    task_timeout: int = Field(default=300, description="Task timeout in seconds")
    task_retry_attempts: int = Field(default=3, description="Task retry attempts")
    
    # Performance settings
    max_concurrent_tasks: int = Field(default=10, description="Max concurrent tasks per agent")
    task_queue_size: int = Field(default=100, description="Task queue size per agent")
    memory_limit_mb: int = Field(default=512, description="Memory limit per agent in MB")
    cpu_limit: float = Field(default=1.0, description="CPU limit per agent")
    
    # Cache settings
    cache_enabled: bool = Field(default=True)
    cache_ttl: int = Field(default=3600, description="Default cache TTL in seconds")
    cache_max_size: int = Field(default=1000, description="Maximum cache entries")
    
    # MCP integration
    mcp_integration_enabled: bool = Field(default=True)
    mcp_fallback_enabled: bool = Field(default=True)
    
    # Agent-specific configurations
    agents: dict[str, dict[str, Any]] = Field(default_factory=lambda: {
        "literature_review": {
            "max_sources": 100,
            "search_depth": "comprehensive",
            "enable_mcp": True
        },
        "comparative_analysis": {
            "max_items": 20,
            "max_criteria": 15,
            "enable_statistics": True,
            "enable_mcp": True
        },
        "methodology": {
            "recommendation_count": 5,
            "bias_detection": True,
            "enable_mcp": True
        },
        "synthesis": {
            "max_input_size": 50000,
            "coherence_check": True,
            "enable_mcp": True
        },
        "citation": {
            "verification_enabled": True,
            "plagiarism_check": False,
            "enable_mcp": True
        }
    })


class DatabaseConfig(BaseModel):
    """Database configuration settings."""
    
    # Connection settings
    host: str = Field(default="localhost")
    port: int = Field(default=5432)
    database: str = Field(default="research_db")
    username: str = Field(default="research")
    # Password is required - no insecure default. Each environment must
    # provide it explicitly (via env var in production, via test fixture in
    # tests, via dev secret in development).
    password: str = Field(..., description="Database password (required)")
    
    # Pool settings
    pool_size: int = Field(default=20)
    max_overflow: int = Field(default=10)
    pool_timeout: int = Field(default=30)
    pool_recycle: int = Field(default=3600)
    pool_pre_ping: bool = Field(default=True)
    
    # Query settings
    statement_timeout: int = Field(default=30000, description="Statement timeout in ms")
    lock_timeout: int = Field(default=10000, description="Lock timeout in ms")
    idle_in_transaction_timeout: int = Field(default=60000, description="Idle transaction timeout in ms")
    
    # Maintenance settings
    auto_vacuum: bool = Field(default=True)
    backup_enabled: bool = Field(default=True)
    backup_retention_days: int = Field(default=7)
    
    @property
    def url(self) -> str:
        """Generate database URL."""
        return f"postgresql+asyncpg://{self.username}:{self.password}@{self.host}:{self.port}/{self.database}"


class RedisConfig(BaseModel):
    """Redis configuration settings."""
    
    host: str = Field(default="localhost")
    port: int = Field(default=6379)
    db: int = Field(default=0)
    password: str | None = Field(default=None)
    
    # Pool settings
    pool_size: int = Field(default=50)
    pool_timeout: int = Field(default=20)
    socket_timeout: int = Field(default=5)
    socket_connect_timeout: int = Field(default=5)
    
    # Retry settings
    retry_on_timeout: bool = Field(default=True)
    retry_on_error: bool = Field(default=True)
    max_retries: int = Field(default=3)
    
    # Cache settings
    default_ttl: int = Field(default=3600)
    max_memory: str = Field(default="256mb")
    eviction_policy: str = Field(default="allkeys-lru")
    
    # Persistence settings
    persistence_enabled: bool = Field(default=True)
    save_intervals: list = Field(default_factory=lambda: ["900 1", "300 10", "60 10000"])
    
    @property
    def url(self) -> str:
        """Generate Redis URL."""
        if self.password:
            return f"redis://:{self.password}@{self.host}:{self.port}/{self.db}"
        return f"redis://{self.host}:{self.port}/{self.db}"


class TemporalConfig(BaseModel):
    """Temporal configuration settings."""
    
    host: str = Field(default="localhost")
    port: int = Field(default=7233)
    namespace: str = Field(default="default")
    
    # Worker settings
    worker_concurrency: int = Field(default=10)
    worker_max_concurrent_activities: int = Field(default=100)
    worker_max_concurrent_workflows: int = Field(default=50)
    
    # Workflow settings
    workflow_execution_timeout: int = Field(default=86400, description="24 hours")
    workflow_run_timeout: int = Field(default=3600, description="1 hour")
    workflow_task_timeout: int = Field(default=10, description="10 seconds")
    
    # Activity settings
    activity_start_to_close_timeout: int = Field(default=300, description="5 minutes")
    activity_heartbeat_timeout: int = Field(default=30, description="30 seconds")
    activity_retry_max_attempts: int = Field(default=3)
    
    # Connection settings
    connection_timeout: int = Field(default=10)
    rpc_timeout: int = Field(default=30)
    query_timeout: int = Field(default=10)
    
    @property
    def target(self) -> str:
        """Generate Temporal target address."""
        return f"{self.host}:{self.port}"


class GeminiConfig(BaseModel):
    """Gemini API configuration settings."""
    
    api_key: str | None = Field(default=None)
    model: str = Field(default="gemini-pro")
    
    # Rate limiting
    requests_per_minute: int = Field(default=60)
    requests_per_day: int = Field(default=10000)
    tokens_per_minute: int = Field(default=100000)
    
    # Request settings
    timeout: int = Field(default=60)
    max_retries: int = Field(default=3)
    retry_delay: float = Field(default=1.0)
    exponential_backoff: bool = Field(default=True)
    
    # Model parameters
    temperature: float = Field(default=0.7)
    max_output_tokens: int = Field(default=4096)
    top_p: float = Field(default=0.95)
    top_k: int = Field(default=40)
    
    # Safety settings
    safety_threshold: str = Field(default="BLOCK_MEDIUM_AND_ABOVE")
    
    # Cache settings
    cache_responses: bool = Field(default=True)
    cache_ttl: int = Field(default=3600)


class MonitoringConfig(BaseModel):
    """Monitoring and observability configuration."""
    
    # Metrics
    metrics_enabled: bool = Field(default=True)
    metrics_port: int = Field(default=9090)
    metrics_path: str = Field(default="/metrics")
    
    # Tracing
    tracing_enabled: bool = Field(default=True)
    tracing_endpoint: str = Field(default="http://localhost:4317")
    tracing_service_name: str = Field(default="research-platform")
    tracing_sample_rate: float = Field(default=0.1)
    
    # Logging
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")
    log_output: str = Field(default="stdout")
    log_file: str | None = Field(default=None)
    log_rotation: bool = Field(default=True)
    log_retention_days: int = Field(default=30)
    
    # Health checks
    health_check_enabled: bool = Field(default=True)
    health_check_interval: int = Field(default=30)
    health_check_timeout: int = Field(default=5)
    health_check_unhealthy_threshold: int = Field(default=3)
    
    # Alerts
    alerting_enabled: bool = Field(default=False)
    alert_webhook_url: str | None = Field(default=None)
    alert_email: str | None = Field(default=None)


class SecurityConfig(BaseModel):
    """Security configuration settings."""
    
    # Authentication
    # JWT secret is required - no insecure default. Each environment must
    # supply this explicitly (env var in production/staging, dev secret
    # in development, test secret in tests).
    jwt_secret_key: str = Field(..., description="JWT secret key (required)")
    jwt_algorithm: str = Field(default="HS256")
    jwt_expiration_minutes: int = Field(default=60)
    refresh_token_expiration_days: int = Field(default=7)
    
    # Rate limiting
    rate_limiting_enabled: bool = Field(default=True)
    rate_limit_per_minute: int = Field(default=100)
    rate_limit_per_hour: int = Field(default=1000)
    rate_limit_per_day: int = Field(default=10000)
    
    # CORS
    cors_enabled: bool = Field(default=True)
    cors_origins: list = Field(default_factory=lambda: ["http://localhost:3000"])
    cors_methods: list = Field(default_factory=lambda: ["GET", "POST", "PUT", "DELETE"])
    cors_headers: list = Field(default_factory=lambda: ["*"])
    
    # Security headers
    security_headers_enabled: bool = Field(default=True)
    csp_policy: str = Field(default="default-src 'self'")
    
    # Secrets management
    secrets_provider: str = Field(default="env", description="env, vault, aws_secrets")
    vault_url: str | None = Field(default=None)
    vault_token: str | None = Field(default=None)
    
    # Audit
    audit_logging_enabled: bool = Field(default=True)
    audit_log_file: str = Field(default="/var/log/research/audit.log")


class BaseConfig(BaseSettings):
    """Base configuration for all environments.

    Subclasses BaseSettings so that values are automatically loaded from a
    .env file and environment variables. Subclasses (Development /
    Production / Staging / Testing) override defaults with environment-
    specific values.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_assignment=True,
    )

    # Application settings
    app_name: str = Field(default="Research Platform")
    app_version: str = Field(default="1.0.0")
    environment: str = Field(default="development")
    debug: bool = Field(default=False)

    # API settings
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    api_workers: int = Field(default=4)
    api_reload: bool = Field(default=False)

    # Component configurations.
    #
    # ``database`` and ``security`` have no default factory because their
    # nested DTOs require credentials (DB password, JWT secret) that have
    # no safe fallback. Subclasses provide them explicitly — by class-level
    # assignment, ``model_post_init``, or constructor kwargs.
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    agents: AgentConfig = Field(default_factory=AgentConfig)
    database: DatabaseConfig = Field(...)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    temporal: TemporalConfig = Field(default_factory=TemporalConfig)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    security: SecurityConfig = Field(...)

    # Feature flags
    features: dict[str, bool] = Field(default_factory=lambda: {
        "mcp_tools": True,
        "agent_pooling": True,
        "caching": True,
        "rate_limiting": True,
        "health_checks": True,
        "metrics": True,
        "tracing": True,
        "audit_logging": True,
    })