"""Characterization tests for the config/ package.

These tests lock in the PUBLIC CONTRACT of the config/ package before the
Pydantic Settings migration. They are designed to survive the migration and
prove that:

  1. The class hierarchy is preserved.
  2. Every public attribute path is preserved.
  3. Environment-variable binding remains correct (now via BaseSettings).
  4. Type coercion (int ports, list origins) keeps working.
  5. The get_config() factory still routes env names to the right class.
  6. Config inheritance still applies development/staging/production
     overrides correctly.
  7. URL helper properties still construct correct URLs.

Tests that lock in the INSECURE DEFAULTS (e.g., the empty JWT secret in
production, the literal "research123" DB password in base) are intentionally
NOT written — those defaults are the smell being eliminated. The refactor
adds positive fail-fast tests in their place. The preserved tests below all
provide values explicitly so they pass both before and after the migration.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Strip every env var the config package reads, then restore."""
    keys_to_clear = [
        "ENVIRONMENT",
        "MCP_SERVER_HOST",
        "MCP_SERVER_PORT",
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_DB",
        "REDIS_PASSWORD",
        "TEMPORAL_HOST",
        "TEMPORAL_PORT",
        "TEMPORAL_NAMESPACE",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "LOG_LEVEL",
        "ALERT_WEBHOOK_URL",
        "ALERT_EMAIL",
        "JWT_SECRET_KEY",
        "CORS_ORIGINS",
        "SECRETS_PROVIDER",
        "VAULT_URL",
        "VAULT_TOKEN",
        "VALIDATE_ENV_VARS",
    ]
    for k in keys_to_clear:
        monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture
def reload_config(isolated_env: None) -> Iterator[Any]:
    """Force a fresh import of the config package after env manipulation."""
    for mod in list(sys.modules):
        if mod == "config" or mod.startswith("config."):
            del sys.modules[mod]
    import config as cfg

    cfg.reload_config()
    yield cfg
    for mod in list(sys.modules):
        if mod == "config" or mod.startswith("config."):
            del sys.modules[mod]


# ---------------------------------------------------------------------------
# Class hierarchy and public surface
# ---------------------------------------------------------------------------


def test_public_classes_exported(reload_config: Any) -> None:
    """The config package exports the documented public classes."""
    cfg = reload_config
    assert hasattr(cfg, "BaseConfig")
    assert hasattr(cfg, "DevelopmentConfig")
    assert hasattr(cfg, "ProductionConfig")
    assert hasattr(cfg, "StagingConfig")
    assert hasattr(cfg, "TestingConfig")
    assert hasattr(cfg, "get_config")
    assert hasattr(cfg, "reload_config")
    assert hasattr(cfg, "config")


def test_environment_subclasses_inherit_from_base(reload_config: Any) -> None:
    """Each environment-specific config inherits from BaseConfig."""
    cfg = reload_config
    assert issubclass(cfg.DevelopmentConfig, cfg.BaseConfig)
    assert issubclass(cfg.ProductionConfig, cfg.BaseConfig)
    assert issubclass(cfg.TestingConfig, cfg.BaseConfig)
    # StagingConfig inherits from ProductionConfig per current architecture.
    assert issubclass(cfg.StagingConfig, cfg.ProductionConfig)


def test_nested_config_models_present(reload_config: Any) -> None:
    """Each public nested config DTO is exported on BaseConfig."""
    cfg = reload_config
    base = cfg.DevelopmentConfig()
    expected_attributes = {
        "mcp",
        "agents",
        "database",
        "redis",
        "temporal",
        "gemini",
        "monitoring",
        "security",
        "features",
    }
    for attr in expected_attributes:
        assert hasattr(base, attr), f"Missing attribute: {attr}"


def test_top_level_attributes_preserved(reload_config: Any) -> None:
    """BaseConfig top-level attribute paths are stable."""
    cfg = reload_config
    dev = cfg.DevelopmentConfig()
    for attr in ["app_name", "app_version", "environment", "debug",
                 "api_host", "api_port", "api_workers", "api_reload"]:
        assert hasattr(dev, attr), f"Missing top-level attribute: {attr}"


# ---------------------------------------------------------------------------
# Type coercion
# ---------------------------------------------------------------------------


def test_database_port_is_int(reload_config: Any) -> None:
    cfg = reload_config
    dev = cfg.DevelopmentConfig()
    assert isinstance(dev.database.port, int)
    assert dev.database.port == 5432


def test_redis_port_is_int(reload_config: Any) -> None:
    cfg = reload_config
    dev = cfg.DevelopmentConfig()
    assert isinstance(dev.redis.port, int)


def test_api_port_is_int(reload_config: Any) -> None:
    cfg = reload_config
    dev = cfg.DevelopmentConfig()
    assert isinstance(dev.api_port, int)


# ---------------------------------------------------------------------------
# URL helper properties
# ---------------------------------------------------------------------------


def test_database_url_constructs_correctly(reload_config: Any) -> None:
    cfg = reload_config
    dev = cfg.DevelopmentConfig()
    url = dev.database.url
    assert url.startswith("postgresql+asyncpg://")
    assert dev.database.username in url
    assert str(dev.database.port) in url
    assert dev.database.database in url


def test_redis_url_constructs_correctly_without_password(
    reload_config: Any,
) -> None:
    cfg = reload_config
    dev = cfg.DevelopmentConfig()
    url = dev.redis.url
    assert url.startswith("redis://")
    assert str(dev.redis.port) in url


def test_redis_url_includes_password_when_set(reload_config: Any) -> None:
    from config.base import RedisConfig

    redis_cfg = RedisConfig(
        host="localhost", port=6379, db=0, password="secret"
    )
    assert "secret" in redis_cfg.url


def test_temporal_target_address(reload_config: Any) -> None:
    cfg = reload_config
    dev = cfg.DevelopmentConfig()
    assert dev.temporal.target == f"{dev.temporal.host}:{dev.temporal.port}"


# ---------------------------------------------------------------------------
# Environment-variable binding (the behaviour the refactor must preserve)
# ---------------------------------------------------------------------------


def test_db_host_env_var_overrides_default_in_production(
    monkeypatch: pytest.MonkeyPatch, reload_config: Any
) -> None:
    """Setting DB_HOST env var overrides the production default."""
    del reload_config  # fixture only ensures fresh import
    monkeypatch.setenv("DB_HOST", "custom-db.internal")
    monkeypatch.setenv("DB_PASSWORD", "supersecret")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    # Reimport because production reads env at class-body in the legacy code.
    for mod in list(sys.modules):
        if mod == "config" or mod.startswith("config."):
            del sys.modules[mod]
    import config as cfg_reloaded

    prod = cfg_reloaded.ProductionConfig()
    assert prod.database.host == "custom-db.internal"
    assert prod.database.password == "supersecret"
    assert prod.security.jwt_secret_key == "x" * 32


def test_db_port_env_var_coerced_to_int(
    monkeypatch: pytest.MonkeyPatch, reload_config: Any
) -> None:
    monkeypatch.setenv("DB_PORT", "6543")
    monkeypatch.setenv("DB_PASSWORD", "supersecret")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    for mod in list(sys.modules):
        if mod == "config" or mod.startswith("config."):
            del sys.modules[mod]
    import config as cfg_reloaded

    prod = cfg_reloaded.ProductionConfig()
    assert prod.database.port == 6543
    assert isinstance(prod.database.port, int)


def test_cors_origins_env_var_split_into_list(
    monkeypatch: pytest.MonkeyPatch, reload_config: Any
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example,https://b.example")
    monkeypatch.setenv("DB_PASSWORD", "supersecret")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    for mod in list(sys.modules):
        if mod == "config" or mod.startswith("config."):
            del sys.modules[mod]
    import config as cfg_reloaded

    prod = cfg_reloaded.ProductionConfig()
    assert "https://a.example" in prod.security.cors_origins
    assert "https://b.example" in prod.security.cors_origins


# ---------------------------------------------------------------------------
# get_config factory routing
# ---------------------------------------------------------------------------


def test_get_config_returns_development_by_default(reload_config: Any) -> None:
    cfg = reload_config
    cfg.reload_config()
    instance = cfg.get_config()
    assert instance.environment == "development"


def test_get_config_routes_explicit_environments(reload_config: Any) -> None:
    cfg = reload_config
    cfg.reload_config()
    assert cfg.get_config("development").environment == "development"
    cfg.reload_config()
    assert cfg.get_config("testing").environment == "testing"
    cfg.reload_config()
    assert cfg.get_config("staging").environment == "staging"


def test_get_config_aliases_resolve(reload_config: Any) -> None:
    cfg = reload_config
    cfg.reload_config()
    assert cfg.get_config("dev").environment == "development"
    cfg.reload_config()
    assert cfg.get_config("test").environment == "testing"


def test_get_config_unknown_environment_raises(reload_config: Any) -> None:
    cfg = reload_config
    with pytest.raises(ValueError, match="Unknown environment"):
        cfg.get_config("nonsense")


def test_get_config_environment_var_drives_default(
    monkeypatch: pytest.MonkeyPatch, reload_config: Any
) -> None:
    cfg = reload_config
    monkeypatch.setenv("ENVIRONMENT", "testing")
    cfg.reload_config()
    assert cfg.get_config().environment == "testing"


# ---------------------------------------------------------------------------
# Inheritance / override semantics
# ---------------------------------------------------------------------------


def test_development_overrides_api_reload(reload_config: Any) -> None:
    cfg = reload_config
    dev = cfg.DevelopmentConfig()
    assert dev.api_reload is True
    assert dev.debug is True
    assert dev.environment == "development"


def test_development_overrides_security(reload_config: Any) -> None:
    cfg = reload_config
    dev = cfg.DevelopmentConfig()
    assert dev.security.rate_limiting_enabled is False


def test_testing_disables_mcp(reload_config: Any) -> None:
    cfg = reload_config
    test_cfg = cfg.TestingConfig()
    assert test_cfg.mcp.enabled is False
    assert test_cfg.api_port == 8001


def test_staging_uses_debug_log_level(
    monkeypatch: pytest.MonkeyPatch, reload_config: Any
) -> None:
    monkeypatch.setenv("DB_PASSWORD", "supersecret")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    for mod in list(sys.modules):
        if mod == "config" or mod.startswith("config."):
            del sys.modules[mod]
    import config as cfg_reloaded

    staging = cfg_reloaded.StagingConfig()
    assert staging.monitoring.log_level == "DEBUG"
    assert staging.environment == "staging"


# ---------------------------------------------------------------------------
# Production validation hook (preserved post-refactor)
# ---------------------------------------------------------------------------


def test_production_validate_required_env_vars_method_exists(
    monkeypatch: pytest.MonkeyPatch, reload_config: Any
) -> None:
    """ProductionConfig exposes validate_required_env_vars(). Post-refactor
    this becomes the eager startup check.
    """
    monkeypatch.setenv("DB_PASSWORD", "supersecret")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    for mod in list(sys.modules):
        if mod == "config" or mod.startswith("config."):
            del sys.modules[mod]
    import config as cfg_reloaded

    prod = cfg_reloaded.ProductionConfig()
    assert hasattr(prod, "validate_required_env_vars")
    # With required env vars set, validation should pass.
    prod.validate_required_env_vars()


def test_production_construction_raises_on_empty_password(
    monkeypatch: pytest.MonkeyPatch, reload_config: Any
) -> None:
    """ProductionConfig() construction raises if DB_PASSWORD is missing.

    The validator runs eagerly at construction time so production fails
    fast on misconfiguration rather than booting with empty credentials.
    """
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    for mod in list(sys.modules):
        if mod == "config" or mod.startswith("config."):
            del sys.modules[mod]
    import config as cfg_reloaded

    with pytest.raises(ValueError, match="Missing required environment variables"):
        cfg_reloaded.ProductionConfig()


def test_production_construction_raises_on_dev_default_jwt(
    monkeypatch: pytest.MonkeyPatch, reload_config: Any
) -> None:
    """ProductionConfig refuses known dev defaults like 'change-me-in-production'."""
    monkeypatch.setenv("DB_PASSWORD", "supersecret")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("JWT_SECRET_KEY", "change-me-in-production")
    for mod in list(sys.modules):
        if mod == "config" or mod.startswith("config."):
            del sys.modules[mod]
    import config as cfg_reloaded

    with pytest.raises(ValueError, match="match known dev defaults"):
        cfg_reloaded.ProductionConfig()


# ---------------------------------------------------------------------------
# Module singleton (eager init at import time)
# ---------------------------------------------------------------------------


def test_module_level_config_singleton_present(reload_config: Any) -> None:
    """`from config import config` returns a BaseConfig instance."""
    cfg = reload_config
    assert isinstance(cfg.config, cfg.BaseConfig)


def test_reload_config_clears_cache(reload_config: Any) -> None:
    """reload_config() forces re-evaluation of get_config()."""
    cfg = reload_config
    instance1 = cfg.get_config("development")
    cfg.reload_config()
    instance2 = cfg.get_config("development")
    # Must be a fresh instance.
    assert instance1 is not instance2


# ---------------------------------------------------------------------------
# Field surface — preserve attribute count to detect accidental drift
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config_class,expected_min_top_level_fields",
    [
        ("DevelopmentConfig", 8),
        ("TestingConfig", 8),
    ],
)
def test_config_field_surface_minimum_size(
    reload_config: Any,
    config_class: str,
    expected_min_top_level_fields: int,
) -> None:
    """Detect if a refactor accidentally drops top-level fields."""
    cfg = reload_config
    klass = getattr(cfg, config_class)
    # Access model_fields on the class (Pydantic v2 idiom).
    fields = klass.model_fields
    assert len(fields) >= expected_min_top_level_fields


def test_database_config_field_surface(reload_config: Any) -> None:
    """DatabaseConfig keeps its full attribute surface."""
    cfg = reload_config
    dev = cfg.DevelopmentConfig()
    db_attrs = {
        "host", "port", "database", "username", "password",
        "pool_size", "max_overflow", "pool_timeout",
        "pool_recycle", "pool_pre_ping",
        "statement_timeout", "lock_timeout",
        "idle_in_transaction_timeout",
        "auto_vacuum", "backup_enabled", "backup_retention_days",
    }
    for attr in db_attrs:
        assert hasattr(dev.database, attr), f"DatabaseConfig missing: {attr}"


def test_security_config_field_surface(reload_config: Any) -> None:
    """SecurityConfig keeps its full attribute surface."""
    cfg = reload_config
    dev = cfg.DevelopmentConfig()
    sec_attrs = {
        "jwt_secret_key", "jwt_algorithm", "jwt_expiration_minutes",
        "refresh_token_expiration_days",
        "rate_limiting_enabled", "rate_limit_per_minute",
        "rate_limit_per_hour", "rate_limit_per_day",
        "cors_enabled", "cors_origins", "cors_methods", "cors_headers",
        "security_headers_enabled", "csp_policy",
        "secrets_provider", "vault_url", "vault_token",
        "audit_logging_enabled", "audit_log_file",
    }
    for attr in sec_attrs:
        assert hasattr(dev.security, attr), f"SecurityConfig missing: {attr}"
