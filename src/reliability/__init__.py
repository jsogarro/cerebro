"""
Reliability module for production-grade service resilience.

This module provides connection pooling, retry strategies, circuit breakers,
and service discovery mechanisms for high availability.
"""

from src.reliability.connection_pools import (
    DatabasePoolManager,
    HTTPPoolManager,
    PoolMetrics,
    PoolStatus,
    RedisPoolManager,
    TemporalPoolManager,
    close_pools,
    database_pool,
    http_pool,
    initialize_pools,
    redis_pool,
    temporal_pool,
)
from src.reliability.health_checks import (
    ComponentType,
    HealthChecker,
    HealthCheckResult,
    HealthStatus,
    SystemHealth,
    health_checker,
    register_health_endpoints,
)
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
    # Health checks
    "HealthStatus",
    "ComponentType",
    "HealthCheckResult",
    "SystemHealth",
    "HealthChecker",
    "health_checker",
    "register_health_endpoints",
    # Connection pools
    "PoolStatus",
    "PoolMetrics",
    "DatabasePoolManager",
    "RedisPoolManager",
    "HTTPPoolManager",
    "TemporalPoolManager",
    "database_pool",
    "redis_pool",
    "http_pool",
    "temporal_pool",
    "initialize_pools",
    "close_pools",
    # Retry strategies
    "RetryPolicy",
    "CircuitState",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "ExponentialBackoff",
    "BulkheadExecutor",
    "BulkheadConfig",
    "RetryBudget",
    "with_retry",
    "with_circuit_breaker",
    "with_bulkhead",
    # Service registry
    "ServiceStatus",
    "LoadBalancingStrategy",
    "ServiceMetadata",
    "ServiceInstance",
    "ServiceRegistry",
    "ServiceDiscovery",
    "LoadBalancer",
    "initialize_service_registry",
    "get_service_registry",
    "get_service_discovery",
]
