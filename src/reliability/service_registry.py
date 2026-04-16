"""
Service registry and discovery system.

This module provides service registration, discovery, and load balancing
for microservices architecture with health-aware routing.
"""

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import redis.asyncio as redis
from src.utils.serialization import serialize_for_cache, deserialize_from_cache

logger = logging.getLogger(__name__)


class ServiceStatus(Enum):
    """Service status enumeration."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    STARTING = "starting"
    STOPPING = "stopping"


class LoadBalancingStrategy(Enum):
    """Load balancing strategies."""

    ROUND_ROBIN = "round_robin"
    LEAST_CONNECTIONS = "least_connections"
    WEIGHTED = "weighted"
    RANDOM = "random"
    IP_HASH = "ip_hash"


@dataclass
class ServiceMetadata:
    """Service metadata."""

    name: str
    version: str
    instance_id: str
    host: str
    port: int
    protocol: str = "http"
    weight: int = 1
    tags: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "instance_id": self.instance_id,
            "host": self.host,
            "port": self.port,
            "protocol": self.protocol,
            "weight": self.weight,
            "tags": self.tags,
            "capabilities": self.capabilities,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ServiceMetadata":
        """Create from dictionary."""
        return cls(**data)


@dataclass
class ServiceInstance:
    """Service instance with health information."""

    metadata: ServiceMetadata
    status: ServiceStatus = ServiceStatus.STARTING
    health_check_url: str | None = None
    registered_at: datetime = field(default_factory=datetime.utcnow)
    last_heartbeat: datetime = field(default_factory=datetime.utcnow)
    last_health_check: datetime | None = None
    health_check_failures: int = 0
    active_connections: int = 0
    total_requests: int = 0
    failed_requests: int = 0
    avg_response_time_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "metadata": self.metadata.to_dict(),
            "status": self.status.value,
            "health_check_url": self.health_check_url,
            "registered_at": self.registered_at.isoformat(),
            "last_heartbeat": self.last_heartbeat.isoformat(),
            "last_health_check": (
                self.last_health_check.isoformat() if self.last_health_check else None
            ),
            "health_check_failures": self.health_check_failures,
            "active_connections": self.active_connections,
            "total_requests": self.total_requests,
            "failed_requests": self.failed_requests,
            "avg_response_time_ms": self.avg_response_time_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ServiceInstance":
        """Create from dictionary."""
        metadata = ServiceMetadata.from_dict(data["metadata"])
        instance = cls(metadata=metadata)

        instance.status = ServiceStatus(data.get("status", "starting"))
        instance.health_check_url = data.get("health_check_url")
        instance.registered_at = datetime.fromisoformat(
            data.get("registered_at", datetime.utcnow().isoformat())
        )
        instance.last_heartbeat = datetime.fromisoformat(
            data.get("last_heartbeat", datetime.utcnow().isoformat())
        )

        if data.get("last_health_check"):
            instance.last_health_check = datetime.fromisoformat(
                data["last_health_check"]
            )

        instance.health_check_failures = data.get("health_check_failures", 0)
        instance.active_connections = data.get("active_connections", 0)
        instance.total_requests = data.get("total_requests", 0)
        instance.failed_requests = data.get("failed_requests", 0)
        instance.avg_response_time_ms = data.get("avg_response_time_ms", 0.0)

        return instance

    @property
    def url(self) -> str:
        """Get service URL."""
        return f"{self.metadata.protocol}://{self.metadata.host}:{self.metadata.port}"

    def is_healthy(self) -> bool:
        """Check if instance is healthy."""
        return self.status in [ServiceStatus.HEALTHY, ServiceStatus.DEGRADED]

    def is_available(self) -> bool:
        """Check if instance is available for requests."""
        return (
            self.status == ServiceStatus.HEALTHY
            and (datetime.utcnow() - self.last_heartbeat).total_seconds() < 60
        )


class ServiceRegistry:
    """
    Service registry for service registration and management.

    Features:
    - Service registration with TTL
    - Health status tracking
    - Service metadata management
    - Redis-based persistence
    """

    def __init__(self, redis_client: redis.Redis | None = None):
        """
        Initialize service registry.

        Args:
            redis_client: Optional Redis client for persistence
        """
        self._services: dict[str, dict[str, ServiceInstance]] = {}
        self._redis_client = redis_client
        self._registry_prefix = "service_registry:"
        self._ttl = 300  # 5 minutes default TTL
        self._heartbeat_interval = 30  # 30 seconds
        self._health_check_interval = 60  # 60 seconds
        self._background_tasks: list[asyncio.Task] = []

    async def register(
        self,
        service: ServiceMetadata,
        health_check_url: str | None = None,
        ttl: int | None = None,
    ) -> ServiceInstance:
        """
        Register a service instance.

        Args:
            service: Service metadata
            health_check_url: Health check endpoint URL
            ttl: Time to live in seconds

        Returns:
            Registered service instance
        """
        instance = ServiceInstance(
            metadata=service,
            health_check_url=health_check_url
            or f"{service.protocol}://{service.host}:{service.port}/health",
        )

        # Store in memory
        if service.name not in self._services:
            self._services[service.name] = {}

        self._services[service.name][service.instance_id] = instance

        # Store in Redis if available
        if self._redis_client:
            key = f"{self._registry_prefix}{service.name}:{service.instance_id}"
            await self._redis_client.setex(
                key, ttl or self._ttl, serialize_for_cache(instance.to_dict().decode("utf-8"))
            )

        logger.info(
            f"Registered service: {service.name}/{service.instance_id} at {service.host}:{service.port}"
        )

        return instance

    async def deregister(self, service_name: str, instance_id: str) -> None:
        """
        Deregister a service instance.

        Args:
            service_name: Service name
            instance_id: Instance ID
        """
        # Remove from memory
        if service_name in self._services:
            if instance_id in self._services[service_name]:
                del self._services[service_name][instance_id]

                if not self._services[service_name]:
                    del self._services[service_name]

        # Remove from Redis
        if self._redis_client:
            key = f"{self._registry_prefix}{service_name}:{instance_id}"
            await self._redis_client.delete(key)

        logger.info(f"Deregistered service: {service_name}/{instance_id}")

    async def heartbeat(self, service_name: str, instance_id: str) -> None:
        """
        Update service heartbeat.

        Args:
            service_name: Service name
            instance_id: Instance ID
        """
        if (
            service_name in self._services
            and instance_id in self._services[service_name]
        ):
            instance = self._services[service_name][instance_id]
            instance.last_heartbeat = datetime.utcnow()

            # Update in Redis
            if self._redis_client:
                key = f"{self._registry_prefix}{service_name}:{instance_id}"
                await self._redis_client.setex(
                    key, self._ttl, serialize_for_cache(instance.to_dict().decode("utf-8"))
                )

    async def update_status(
        self, service_name: str, instance_id: str, status: ServiceStatus
    ) -> None:
        """
        Update service status.

        Args:
            service_name: Service name
            instance_id: Instance ID
            status: New status
        """
        if (
            service_name in self._services
            and instance_id in self._services[service_name]
        ):
            instance = self._services[service_name][instance_id]
            instance.status = status

            # Update in Redis
            if self._redis_client:
                key = f"{self._registry_prefix}{service_name}:{instance_id}"
                await self._redis_client.setex(
                    key, self._ttl, serialize_for_cache(instance.to_dict().decode("utf-8"))
                )

            logger.info(
                f"Updated service status: {service_name}/{instance_id} -> {status.value}"
            )

    async def get_service(
        self, service_name: str, instance_id: str
    ) -> ServiceInstance | None:
        """
        Get a specific service instance.

        Args:
            service_name: Service name
            instance_id: Instance ID

        Returns:
            Service instance or None
        """
        # Check memory first
        if (
            service_name in self._services
            and instance_id in self._services[service_name]
        ):
            return self._services[service_name][instance_id]

        # Check Redis
        if self._redis_client:
            key = f"{self._registry_prefix}{service_name}:{instance_id}"
            data = await self._redis_client.get(key)

            if data:
                instance = ServiceInstance.from_dict(deserialize_from_cache(data))

                # Cache in memory
                if service_name not in self._services:
                    self._services[service_name] = {}
                self._services[service_name][instance_id] = instance

                return instance

        return None

    async def get_all_instances(self, service_name: str) -> list[ServiceInstance]:
        """
        Get all instances of a service.

        Args:
            service_name: Service name

        Returns:
            List of service instances
        """
        instances = []

        # Get from memory
        if service_name in self._services:
            instances.extend(self._services[service_name].values())

        # Get from Redis
        if self._redis_client:
            pattern = f"{self._registry_prefix}{service_name}:*"
            keys = await self._redis_client.keys(pattern)

            for key in keys:
                data = await self._redis_client.get(key)
                if data:
                    instance = ServiceInstance.from_dict(deserialize_from_cache(data))

                    # Check if not already in list
                    if not any(
                        i.metadata.instance_id == instance.metadata.instance_id
                        for i in instances
                    ):
                        instances.append(instance)

        return instances

    async def get_healthy_instances(self, service_name: str) -> list[ServiceInstance]:
        """
        Get healthy instances of a service.

        Args:
            service_name: Service name

        Returns:
            List of healthy service instances
        """
        all_instances = await self.get_all_instances(service_name)
        return [i for i in all_instances if i.is_available()]

    async def cleanup_expired(self) -> None:
        """Clean up expired service registrations."""
        current_time = datetime.utcnow()
        expired = []

        for service_name, instances in self._services.items():
            for instance_id, instance in instances.items():
                # Check if heartbeat expired
                if (current_time - instance.last_heartbeat).total_seconds() > self._ttl:
                    expired.append((service_name, instance_id))

        # Remove expired instances
        for service_name, instance_id in expired:
            await self.deregister(service_name, instance_id)
            logger.warning(f"Cleaned up expired service: {service_name}/{instance_id}")


class ServiceDiscovery:
    """
    Service discovery with load balancing.

    Features:
    - Multiple load balancing strategies
    - Health-aware routing
    - Circuit breaker integration
    - Request tracking
    """

    def __init__(self, registry: ServiceRegistry):
        """
        Initialize service discovery.

        Args:
            registry: Service registry instance
        """
        self._registry = registry
        self._round_robin_counters: dict[str, int] = {}
        self._circuit_breakers: dict[str, Any] = (
            {}
        )  # Would integrate with CircuitBreaker class

    async def discover(
        self,
        service_name: str,
        strategy: LoadBalancingStrategy = LoadBalancingStrategy.ROUND_ROBIN,
        tags: list[str] | None = None,
        capabilities: list[str] | None = None,
    ) -> ServiceInstance | None:
        """
        Discover a service instance.

        Args:
            service_name: Service name
            strategy: Load balancing strategy
            tags: Required tags
            capabilities: Required capabilities

        Returns:
            Selected service instance or None
        """
        # Get healthy instances
        instances = await self._registry.get_healthy_instances(service_name)

        if not instances:
            logger.warning(f"No healthy instances found for service: {service_name}")
            return None

        # Filter by tags
        if tags:
            instances = [
                i for i in instances if all(tag in i.metadata.tags for tag in tags)
            ]

        # Filter by capabilities
        if capabilities:
            instances = [
                i
                for i in instances
                if all(cap in i.metadata.capabilities for cap in capabilities)
            ]

        if not instances:
            logger.warning(f"No instances match criteria for service: {service_name}")
            return None

        # Select instance based on strategy
        return await self._select_instance(service_name, instances, strategy)

    async def _select_instance(
        self,
        service_name: str,
        instances: list[ServiceInstance],
        strategy: LoadBalancingStrategy,
    ) -> ServiceInstance:
        """
        Select an instance based on load balancing strategy.

        Args:
            service_name: Service name
            instances: Available instances
            strategy: Load balancing strategy

        Returns:
            Selected instance
        """
        if strategy == LoadBalancingStrategy.ROUND_ROBIN:
            return self._round_robin_select(service_name, instances)

        elif strategy == LoadBalancingStrategy.LEAST_CONNECTIONS:
            return min(instances, key=lambda i: i.active_connections)

        elif strategy == LoadBalancingStrategy.WEIGHTED:
            return self._weighted_select(instances)

        elif strategy == LoadBalancingStrategy.RANDOM:
            return random.choice(instances)

        elif strategy == LoadBalancingStrategy.IP_HASH:
            # Would need client IP for proper implementation
            return instances[hash(service_name) % len(instances)]

        else:
            return instances[0]

    def _round_robin_select(
        self, service_name: str, instances: list[ServiceInstance]
    ) -> ServiceInstance:
        """Round-robin selection."""
        if service_name not in self._round_robin_counters:
            self._round_robin_counters[service_name] = 0

        index = self._round_robin_counters[service_name] % len(instances)
        self._round_robin_counters[service_name] += 1

        return instances[index]

    def _weighted_select(self, instances: list[ServiceInstance]) -> ServiceInstance:
        """Weighted random selection."""
        weights = [i.metadata.weight for i in instances]
        return random.choices(instances, weights=weights)[0]

    async def call_service(
        self,
        service_name: str,
        endpoint: str,
        method: str = "GET",
        strategy: LoadBalancingStrategy = LoadBalancingStrategy.ROUND_ROBIN,
        **kwargs,
    ) -> Any:
        """
        Call a service with automatic discovery and load balancing.

        Args:
            service_name: Service name
            endpoint: API endpoint
            method: HTTP method
            strategy: Load balancing strategy
            **kwargs: Additional request parameters

        Returns:
            Service response
        """
        instance = await self.discover(service_name, strategy)

        if not instance:
            raise Exception(f"No available instances for service: {service_name}")

        # Track request
        instance.active_connections += 1
        instance.total_requests += 1

        try:
            # Would use HTTP client pool here
            url = f"{instance.url}{endpoint}"

            # Import httpx dynamically
            import httpx

            async with httpx.AsyncClient() as client:
                response = await client.request(method, url, **kwargs)
                response.raise_for_status()
                return response.json()

        except Exception as e:
            instance.failed_requests += 1
            logger.error(f"Service call failed: {service_name} -> {e}")
            raise
        finally:
            instance.active_connections -= 1


class LoadBalancer:
    """
    Advanced load balancer with health checks and failover.

    Features:
    - Multiple backend support
    - Health monitoring
    - Automatic failover
    - Request retries
    """

    def __init__(
        self,
        service_name: str,
        discovery: ServiceDiscovery,
        strategy: LoadBalancingStrategy = LoadBalancingStrategy.ROUND_ROBIN,
    ):
        """
        Initialize load balancer.

        Args:
            service_name: Service name to balance
            discovery: Service discovery instance
            strategy: Load balancing strategy
        """
        self.service_name = service_name
        self._discovery = discovery
        self._strategy = strategy
        self._failed_instances: set[str] = set()
        self._failure_counts: dict[str, int] = {}
        self._max_failures = 3

    async def execute(
        self, endpoint: str, method: str = "GET", max_retries: int = 3, **kwargs
    ) -> Any:
        """
        Execute request with load balancing and retry.

        Args:
            endpoint: API endpoint
            method: HTTP method
            max_retries: Maximum retry attempts
            **kwargs: Additional request parameters

        Returns:
            Service response
        """
        last_error = None

        for attempt in range(max_retries):
            try:
                # Get an instance excluding failed ones
                instance = await self._get_instance()

                if not instance:
                    raise Exception(f"No available instances for {self.service_name}")

                # Call the service
                result = await self._discovery.call_service(
                    self.service_name, endpoint, method, self._strategy, **kwargs
                )

                # Reset failure count on success
                if instance.metadata.instance_id in self._failure_counts:
                    del self._failure_counts[instance.metadata.instance_id]

                return result

            except Exception as e:
                last_error = e

                if instance:
                    # Track failures
                    instance_id = instance.metadata.instance_id
                    self._failure_counts[instance_id] = (
                        self._failure_counts.get(instance_id, 0) + 1
                    )

                    # Mark as failed if threshold reached
                    if self._failure_counts[instance_id] >= self._max_failures:
                        self._failed_instances.add(instance_id)
                        logger.warning(f"Marked instance as failed: {instance_id}")

                if attempt < max_retries - 1:
                    await asyncio.sleep(2**attempt)  # Exponential backoff

        raise last_error

    async def _get_instance(self) -> ServiceInstance | None:
        """Get an instance excluding failed ones."""
        instances = await self._discovery._registry.get_healthy_instances(
            self.service_name
        )

        # Filter out failed instances
        available = [
            i for i in instances if i.metadata.instance_id not in self._failed_instances
        ]

        if not available:
            # If all failed, try to recover one
            if instances:
                # Reset the least recently failed
                instance_id = next(iter(self._failed_instances))
                self._failed_instances.remove(instance_id)
                self._failure_counts[instance_id] = 0
                logger.info(f"Attempting to recover failed instance: {instance_id}")
                available = instances[:1]

        return available[0] if available else None


# Global instances
_global_registry: ServiceRegistry | None = None
_global_discovery: ServiceDiscovery | None = None


async def initialize_service_registry(redis_client: redis.Redis | None = None) -> None:
    """Initialize global service registry."""
    global _global_registry, _global_discovery

    _global_registry = ServiceRegistry(redis_client)
    _global_discovery = ServiceDiscovery(_global_registry)

    logger.info("Service registry initialized")


def get_service_registry() -> ServiceRegistry:
    """Get global service registry."""
    if not _global_registry:
        raise RuntimeError("Service registry not initialized")
    return _global_registry


def get_service_discovery() -> ServiceDiscovery:
    """Get global service discovery."""
    if not _global_discovery:
        raise RuntimeError("Service discovery not initialized")
    return _global_discovery


__all__ = [
    "LoadBalancer",
    "LoadBalancingStrategy",
    "ServiceDiscovery",
    "ServiceInstance",
    "ServiceMetadata",
    "ServiceRegistry",
    "ServiceStatus",
    "get_service_discovery",
    "get_service_registry",
    "initialize_service_registry",
]
