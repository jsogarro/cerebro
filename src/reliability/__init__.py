"""Reliability module for production-grade service resilience.

This module provides connection pooling, retry strategies, circuit breakers,
and service discovery mechanisms for high availability.
"""

# NOTE: connection_pools and health_checks temporarily disabled due to config
# structure mismatch (expect config.database.url but settings has DATABASE_URL).
# These modules are not currently used by active code paths. Re-enable after
# refactoring to use src.core.config.settings.
#
# from src.reliability.connection_pools import (
#     DatabasePoolManager,
#     HTTPPoolManager,
#     PoolMetrics,
#     PoolStatus,
#     RedisPoolManager,
#     TemporalPoolManager,
#     close_pools,
#     database_pool,
#     http_pool,
#     initialize_pools,
#     redis_pool,
#     temporal_pool,
# )
# from src.reliability.health_checks import (
#     ComponentType,
#     HealthChecker,
#     HealthCheckResult,
#     HealthStatus,
#     SystemHealth,
#     health_checker,
#     register_health_endpoints,
# )
from src.reliability.retry_strategies import (
    BulkheadConfig,
    BulkheadExecutor,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    ExponentialBackoff,
    RetryBudget,
    RetryPolicy,
    with_bulkhead,
    with_circuit_breaker,
    with_retry,
)
from src.reliability.service_registry import (
    LoadBalancer,
    LoadBalancingStrategy,
    ServiceDiscovery,
    ServiceInstance,
    ServiceMetadata,
    ServiceRegistry,
    ServiceStatus,
    get_service_discovery,
    get_service_registry,
    initialize_service_registry,
)

__all__ = [
    "BulkheadConfig",
    "BulkheadExecutor",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitState",
    # "ComponentType",  # from health_checks (disabled)
    # "DatabasePoolManager",  # from connection_pools (disabled)
    "ExponentialBackoff",
    # "HTTPPoolManager",  # from connection_pools (disabled)
    # "HealthCheckResult",  # from health_checks (disabled)
    # "HealthChecker",  # from health_checks (disabled)
    # "HealthStatus",  # from health_checks (disabled)
    "LoadBalancer",
    "LoadBalancingStrategy",
    # "PoolMetrics",  # from connection_pools (disabled)
    # "PoolStatus",  # from connection_pools (disabled)
    # "RedisPoolManager",  # from connection_pools (disabled)
    "RetryBudget",
    "RetryPolicy",
    "ServiceDiscovery",
    "ServiceInstance",
    "ServiceMetadata",
    "ServiceRegistry",
    "ServiceStatus",
    # "SystemHealth",  # from health_checks (disabled)
    # "TemporalPoolManager",  # from connection_pools (disabled)
    # "close_pools",  # from connection_pools (disabled)
    # "database_pool",  # from connection_pools (disabled)
    "get_service_discovery",
    "get_service_registry",
    # "health_checker",  # from health_checks (disabled)
    # "http_pool",  # from connection_pools (disabled)
    # "initialize_pools",  # from connection_pools (disabled)
    "initialize_service_registry",
    # "redis_pool",  # from connection_pools (disabled)
    # "register_health_endpoints",  # from health_checks (disabled)
    # "temporal_pool",  # from connection_pools (disabled)
    "with_bulkhead",
    "with_circuit_breaker",
    "with_retry",
]
