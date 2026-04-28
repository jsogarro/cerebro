"""
Production environment configuration.

This module contains configuration settings specific to the production
environment. It inherits from base configuration and overrides settings
for optimal performance, security, and reliability in production.

Environment variables are loaded through pydantic-settings rather than
direct ``os.getenv`` calls. The :class:`_ProductionEnv` BaseSettings holds
typed env bindings; :class:`ProductionConfig` reads those bindings when
constructing nested DTOs in ``model_post_init``.
"""

import os
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

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

# Shared settings config for env-binding helpers.
ENV_SETTINGS_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    case_sensitive=True,
    extra="ignore",
)


class _ProductionEnv(BaseSettings):
    """Typed env-var binding for production overrides.

    Pydantic-settings reads these from ``.env`` and the process environment
    automatically. ``None`` indicates "no override; keep DTO default".
    """

    model_config = ENV_SETTINGS_CONFIG

    # MCP
    MCP_SERVER_HOST: str = "mcp-server"
    MCP_SERVER_PORT: int = 9000

    # Database
    DB_HOST: str = "postgres"
    DB_PORT: int = 5432
    DB_NAME: str = "research_db"
    DB_USER: str = "research"
    DB_PASSWORD: str = Field(default="", description="Required in production")

    # Redis
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str | None = None

    # Temporal
    TEMPORAL_HOST: str = "temporal"
    TEMPORAL_PORT: int = 7233
    TEMPORAL_NAMESPACE: str = "research-platform"

    # Gemini
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-pro"

    # Monitoring
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://otel-collector:4317"
    LOG_LEVEL: str = "INFO"
    ALERT_WEBHOOK_URL: str | None = None
    ALERT_EMAIL: str | None = None

    # Security
    JWT_SECRET_KEY: str = Field(default="", description="Required in production")
    CORS_ORIGINS: str = ""
    SECRETS_PROVIDER: str = "vault"
    VAULT_URL: str | None = None
    VAULT_TOKEN: str | None = None


class ProductionConfig(BaseConfig):
    """Production environment configuration."""

    # Application settings
    environment: str = "production"
    debug: bool = False

    # API settings
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = os.cpu_count() or 4
    api_reload: bool = False

    def __init__(
        self,
        *,
        database: DatabaseConfig | None = None,
        security: SecurityConfig | None = None,
        **data: Any,
    ) -> None:
        """Build nested DTOs from env bindings before BaseSettings validates.

        Production reads its required secrets (``DB_PASSWORD``, ``JWT_SECRET_KEY``,
        ``GEMINI_API_KEY``) from environment via :class:`_ProductionEnv`. We
        construct the ``database`` and ``security`` DTOs eagerly here so that
        ``BaseConfig``'s required-field validation has the values it needs.
        Callers can still pass an explicit ``database`` or ``security`` to
        bypass env binding (mainly used by tests).
        """
        env = _ProductionEnv()
        kwargs: dict[str, Any] = dict(data)
        if database is not None:
            kwargs["database"] = database
        if security is not None:
            kwargs["security"] = security

        kwargs.setdefault(
            "database",
            DatabaseConfig(
                host=env.DB_HOST,
                port=env.DB_PORT,
                database=env.DB_NAME,
                username=env.DB_USER,
                password=env.DB_PASSWORD,
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
            ),
        )

        cors_origins: list[str] = (
            [origin.strip() for origin in env.CORS_ORIGINS.split(",") if origin.strip()]
            if env.CORS_ORIGINS
            else []
        )
        kwargs.setdefault(
            "security",
            SecurityConfig(
                jwt_secret_key=env.JWT_SECRET_KEY,
                jwt_algorithm="RS256",
                jwt_expiration_minutes=60,
                refresh_token_expiration_days=7,
                rate_limiting_enabled=True,
                rate_limit_per_minute=100,
                rate_limit_per_hour=1000,
                rate_limit_per_day=10000,
                cors_enabled=True,
                cors_origins=cors_origins,
                cors_methods=["GET", "POST", "PUT", "DELETE"],
                cors_headers=["Content-Type", "Authorization"],
                security_headers_enabled=True,
                csp_policy=(
                    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                    "style-src 'self' 'unsafe-inline'"
                ),
                secrets_provider=env.SECRETS_PROVIDER,
                vault_url=env.VAULT_URL,
                vault_token=env.VAULT_TOKEN,
                audit_logging_enabled=True,
                audit_log_file="/var/log/research/audit.log",
            ),
        )

        # Stash the env binding so ``model_post_init`` can use it without
        # re-reading the environment.
        self.__dict__["_env"] = env
        super().__init__(**kwargs)

    def model_post_init(self, __context: object) -> None:
        """Populate the remaining nested DTOs from the cached env binding."""
        env: _ProductionEnv = self.__dict__.pop("_env", None) or _ProductionEnv()

        self.mcp = MCPConfig(
            enabled=True,
            server_host=env.MCP_SERVER_HOST,
            server_port=env.MCP_SERVER_PORT,
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

        self.agents = AgentConfig(
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

        # ``database`` and ``security`` were already constructed in __init__
        # so that BaseConfig's required-field validation sees them.

        self.redis = RedisConfig(
            host=env.REDIS_HOST,
            port=env.REDIS_PORT,
            db=env.REDIS_DB,
            password=env.REDIS_PASSWORD,
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

        self.temporal = TemporalConfig(
            host=env.TEMPORAL_HOST,
            port=env.TEMPORAL_PORT,
            namespace=env.TEMPORAL_NAMESPACE,
            worker_concurrency=20,
            worker_max_concurrent_activities=200,
            worker_max_concurrent_workflows=100,
            workflow_execution_timeout=86400,
            workflow_run_timeout=3600,
            workflow_task_timeout=10,
            activity_start_to_close_timeout=300,
            activity_heartbeat_timeout=30,
            activity_retry_max_attempts=3,
            connection_timeout=10,
            rpc_timeout=30,
            query_timeout=10,
        )

        self.gemini = GeminiConfig(
            api_key=env.GEMINI_API_KEY,
            model=env.GEMINI_MODEL,
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

        self.monitoring = MonitoringConfig(
            metrics_enabled=True,
            metrics_port=9090,
            metrics_path="/metrics",
            tracing_enabled=True,
            tracing_endpoint=env.OTEL_EXPORTER_OTLP_ENDPOINT,
            tracing_service_name="research-platform",
            tracing_sample_rate=0.1,
            log_level=env.LOG_LEVEL,
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
            alert_webhook_url=env.ALERT_WEBHOOK_URL,
            alert_email=env.ALERT_EMAIL,
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
            "debug_endpoints": False,
            "mock_external_services": False,
            "maintenance_mode": False,
        }

        # Eager startup health check. Previously this was gated behind a
        # ``VALIDATE_ENV_VARS=true`` flag, which meant a misconfigured
        # production process booted happily with empty secrets. Run it
        # always now so prod fails fast at construction.
        self.validate_required_env_vars()

    def validate_required_env_vars(self) -> None:
        """Validate that all required environment variables are set.

        Raises ``ValueError`` if ``DB_PASSWORD``, ``GEMINI_API_KEY``, or
        ``JWT_SECRET_KEY`` are missing or empty. Also rejects known dev
        defaults that would otherwise produce a "looks-authenticated"
        production system with a public secret.
        """
        required_vars = [
            ("DB_PASSWORD", self.database.password),
            ("GEMINI_API_KEY", self.gemini.api_key),
            ("JWT_SECRET_KEY", self.security.jwt_secret_key),
        ]

        missing_vars = [
            var_name for var_name, var_value in required_vars if not var_value
        ]

        if missing_vars:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing_vars)}"
            )

        # Reject known-insecure dev defaults that would otherwise pass
        # the empty-string check above.
        insecure_defaults = {
            "DB_PASSWORD": {"research123", "postgres", "password"},
            "JWT_SECRET_KEY": {
                "change-me-in-production",
                "dev-secret-key-not-for-production",
                "staging-secret-key",
            },
        }
        violations = []
        if self.database.password in insecure_defaults["DB_PASSWORD"]:
            violations.append("DB_PASSWORD")
        if self.security.jwt_secret_key in insecure_defaults["JWT_SECRET_KEY"]:
            violations.append("JWT_SECRET_KEY")
        if violations:
            raise ValueError(
                "Production environment variables match known dev defaults: "
                f"{', '.join(violations)}. Rotate these secrets before deploy."
            )


