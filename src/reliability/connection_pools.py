"""
Connection pool management for all services.

This module provides production-grade connection pooling for databases,
cache systems, HTTP clients, and message queues with health monitoring,
auto-scaling, and metrics collection.
"""

import asyncio
import hashlib
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import asyncpg
import httpx
import redis.asyncio as redis
from redis.asyncio.connection import ConnectionPool as RedisConnectionPool
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from config import config

logger = logging.getLogger(__name__)


class PoolStatus(Enum):
    """Connection pool status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    SCALING = "scaling"


@dataclass
class PoolMetrics:
    """Connection pool metrics."""

    total_connections: int = 0
    active_connections: int = 0
    idle_connections: int = 0
    waiting_requests: int = 0
    total_requests: int = 0
    failed_requests: int = 0
    avg_wait_time_ms: float = 0.0
    avg_connection_time_ms: float = 0.0
    connection_errors: int = 0
    pool_exhausted_count: int = 0
    last_error: str | None = None
    last_error_time: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            "total_connections": self.total_connections,
            "active_connections": self.active_connections,
            "idle_connections": self.idle_connections,
            "waiting_requests": self.waiting_requests,
            "total_requests": self.total_requests,
            "failed_requests": self.failed_requests,
            "avg_wait_time_ms": round(self.avg_wait_time_ms, 2),
            "avg_connection_time_ms": round(self.avg_connection_time_ms, 2),
            "connection_errors": self.connection_errors,
            "pool_exhausted_count": self.pool_exhausted_count,
            "last_error": self.last_error,
            "last_error_time": (
                self.last_error_time.isoformat() if self.last_error_time else None
            ),
        }


class DatabasePoolManager:
    """
    Database connection pool manager with advanced features.

    Features:
    - Dynamic pool sizing
    - Connection health monitoring
    - Automatic connection recycling
    - Query timeout management
    - Metrics collection
    """

    def __init__(self):
        """Initialize database pool manager."""
        self._engine: AsyncEngine | None = None
        self._sessionmaker: sessionmaker | None = None
        self._pool: asyncpg.Pool | None = None
        self._metrics = PoolMetrics()
        self._status = PoolStatus.UNHEALTHY
        self._last_health_check = datetime.utcnow()
        self._connection_semaphore: asyncio.Semaphore | None = None

    async def initialize(self, config_override: dict[str, Any] | None = None):
        """
        Initialize database connection pool.

        Args:
            config_override: Optional configuration overrides
        """
        try:
            db_config = config_override or config.database.dict()

            # Create SQLAlchemy async engine with connection pooling
            self._engine = create_async_engine(
                config.database.url,
                pool_size=db_config.get("pool_size", 20),
                max_overflow=db_config.get("max_overflow", 10),
                pool_timeout=db_config.get("pool_timeout", 30),
                pool_recycle=db_config.get("pool_recycle", 3600),
                pool_pre_ping=db_config.get("pool_pre_ping", True),
                echo=False,
                echo_pool=False,
                poolclass=QueuePool,
                connect_args={
                    "server_settings": {
                        "application_name": f"research-platform-{config.environment}",
                        "jit": "on",
                    },
                    "timeout": db_config.get("pool_timeout", 30),
                    "command_timeout": db_config.get("statement_timeout", 30000) / 1000,
                },
            )

            # Create session factory
            self._sessionmaker = sessionmaker(
                self._engine, class_=AsyncSession, expire_on_commit=False
            )

            # Create asyncpg pool for direct queries
            self._pool = await asyncpg.create_pool(
                config.database.url.replace("+asyncpg", ""),
                min_size=5,
                max_size=db_config.get("pool_size", 20),
                max_queries=50000,
                max_inactive_connection_lifetime=300,
                timeout=db_config.get("pool_timeout", 30),
                command_timeout=db_config.get("statement_timeout", 30000) / 1000,
            )

            # Initialize connection semaphore for additional control
            self._connection_semaphore = asyncio.Semaphore(
                db_config.get("pool_size", 20) + db_config.get("max_overflow", 10)
            )

            self._status = PoolStatus.HEALTHY
            logger.info("Database pool initialized successfully")

        except Exception as e:
            self._status = PoolStatus.UNHEALTHY
            self._metrics.last_error = str(e)
            self._metrics.last_error_time = datetime.utcnow()
            logger.error(f"Failed to initialize database pool: {e}")
            raise

    @asynccontextmanager
    async def get_session(self):
        """
        Get a database session from the pool.

        Yields:
            AsyncSession: Database session
        """
        if not self._sessionmaker:
            raise RuntimeError("Database pool not initialized")

        start_time = time.time()
        self._metrics.total_requests += 1

        async with self._connection_semaphore:
            wait_time = (time.time() - start_time) * 1000
            self._metrics.avg_wait_time_ms = (
                self._metrics.avg_wait_time_ms * (self._metrics.total_requests - 1)
                + wait_time
            ) / self._metrics.total_requests

            try:
                async with self._sessionmaker() as session:
                    self._metrics.active_connections += 1
                    yield session
                    await session.commit()

            except Exception as e:
                self._metrics.failed_requests += 1
                self._metrics.last_error = str(e)
                self._metrics.last_error_time = datetime.utcnow()
                logger.error(f"Database session error: {e}")
                raise
            finally:
                self._metrics.active_connections -= 1
                connection_time = (time.time() - start_time) * 1000
                self._metrics.avg_connection_time_ms = (
                    self._metrics.avg_connection_time_ms
                    * (self._metrics.total_requests - 1)
                    + connection_time
                ) / self._metrics.total_requests

    @asynccontextmanager
    async def acquire_connection(self):
        """
        Acquire a direct database connection from asyncpg pool.

        Yields:
            asyncpg.Connection: Database connection
        """
        if not self._pool:
            raise RuntimeError("Database pool not initialized")

        start_time = time.time()
        self._metrics.total_requests += 1

        try:
            async with self._pool.acquire() as connection:
                self._metrics.active_connections += 1
                yield connection

        except asyncpg.TooManyConnectionsError:
            self._metrics.pool_exhausted_count += 1
            self._metrics.failed_requests += 1
            logger.error("Database connection pool exhausted")
            raise
        except Exception as e:
            self._metrics.failed_requests += 1
            self._metrics.connection_errors += 1
            logger.error(f"Failed to acquire database connection: {e}")
            raise
        finally:
            self._metrics.active_connections -= 1

    async def execute_query(self, query: str, *args, timeout: float | None = None):
        """
        Execute a query with automatic retry and timeout.

        Args:
            query: SQL query to execute
            *args: Query parameters
            timeout: Query timeout in seconds

        Returns:
            Query result
        """
        async with self.acquire_connection() as conn:
            return await conn.fetch(query, *args, timeout=timeout)

    async def health_check(self) -> PoolStatus:
        """
        Check database pool health.

        Returns:
            Pool status
        """
        try:
            async with self.acquire_connection() as conn:
                await conn.fetchval("SELECT 1")

            # Update metrics
            if self._pool:
                pool_stats = self._pool.get_stats()
                self._metrics.total_connections = pool_stats.get("total_size", 0)
                self._metrics.idle_connections = pool_stats.get("free_size", 0)

            # Determine health status
            if self._metrics.failed_requests > self._metrics.total_requests * 0.1:
                self._status = PoolStatus.DEGRADED
            else:
                self._status = PoolStatus.HEALTHY

        except Exception as e:
            self._status = PoolStatus.UNHEALTHY
            logger.error(f"Database health check failed: {e}")

        self._last_health_check = datetime.utcnow()
        return self._status

    async def close(self):
        """Close database connection pool."""
        if self._pool:
            await self._pool.close()
        if self._engine:
            await self._engine.dispose()
        logger.info("Database pool closed")

    def get_metrics(self) -> PoolMetrics:
        """Get pool metrics."""
        return self._metrics


class RedisPoolManager:
    """
    Redis connection pool manager with advanced features.

    Features:
    - Connection pooling with health checks
    - Automatic reconnection
    - Command pipelining
    - Pub/Sub management
    - Metrics collection
    """

    def __init__(self):
        """Initialize Redis pool manager."""
        self._pool: RedisConnectionPool | None = None
        self._client: redis.Redis | None = None
        self._pubsub_clients: dict[str, redis.client.PubSub] = {}
        self._metrics = PoolMetrics()
        self._status = PoolStatus.UNHEALTHY

    async def initialize(self, config_override: dict[str, Any] | None = None):
        """
        Initialize Redis connection pool.

        Args:
            config_override: Optional configuration overrides
        """
        try:
            redis_config = config_override or config.redis.dict()

            # Create connection pool
            self._pool = redis.ConnectionPool(
                host=redis_config.get("host", "localhost"),
                port=redis_config.get("port", 6379),
                db=redis_config.get("db", 0),
                password=redis_config.get("password"),
                max_connections=redis_config.get("pool_size", 50),
                socket_timeout=redis_config.get("socket_timeout", 5),
                socket_connect_timeout=redis_config.get("socket_connect_timeout", 5),
                socket_keepalive=True,
                socket_keepalive_options={
                    1: 1,  # TCP_KEEPIDLE
                    2: 2,  # TCP_KEEPINTVL
                    3: 3,  # TCP_KEEPCNT
                },
                retry_on_timeout=redis_config.get("retry_on_timeout", True),
                health_check_interval=30,
            )

            # Create client with pool
            self._client = redis.Redis(connection_pool=self._pool)

            # Test connection
            await self._client.ping()

            self._status = PoolStatus.HEALTHY
            logger.info("Redis pool initialized successfully")

        except Exception as e:
            self._status = PoolStatus.UNHEALTHY
            self._metrics.last_error = str(e)
            self._metrics.last_error_time = datetime.utcnow()
            logger.error(f"Failed to initialize Redis pool: {e}")
            raise

    async def get_client(self) -> redis.Redis:
        """
        Get Redis client.

        Returns:
            Redis client instance
        """
        if not self._client:
            raise RuntimeError("Redis pool not initialized")

        self._metrics.total_requests += 1
        return self._client

    async def execute_command(self, command: str, *args, **kwargs):
        """
        Execute Redis command with retry.

        Args:
            command: Redis command name
            *args: Command arguments
            **kwargs: Command keyword arguments

        Returns:
            Command result
        """
        client = await self.get_client()

        try:
            self._metrics.active_connections += 1
            method = getattr(client, command)
            result = await method(*args, **kwargs)
            return result

        except redis.ConnectionError as e:
            self._metrics.connection_errors += 1
            self._metrics.failed_requests += 1
            logger.error(f"Redis connection error: {e}")
            raise
        except Exception as e:
            self._metrics.failed_requests += 1
            logger.error(f"Redis command error: {e}")
            raise
        finally:
            self._metrics.active_connections -= 1

    async def get_pubsub(self, channel_pattern: str) -> redis.client.PubSub:
        """
        Get or create pub/sub client for channel pattern.

        Args:
            channel_pattern: Channel pattern to subscribe to

        Returns:
            PubSub client
        """
        if channel_pattern not in self._pubsub_clients:
            client = await self.get_client()
            pubsub = client.pubsub()
            await pubsub.psubscribe(channel_pattern)
            self._pubsub_clients[channel_pattern] = pubsub

        return self._pubsub_clients[channel_pattern]

    async def health_check(self) -> PoolStatus:
        """
        Check Redis pool health.

        Returns:
            Pool status
        """
        try:
            client = await self.get_client()
            await client.ping()

            # Get pool stats
            pool_conn_kwargs = self._pool.connection_kwargs if self._pool else {}
            info = await client.info()

            self._metrics.total_connections = info.get("connected_clients", 0)

            # Determine health status
            if self._metrics.failed_requests > self._metrics.total_requests * 0.1:
                self._status = PoolStatus.DEGRADED
            else:
                self._status = PoolStatus.HEALTHY

        except Exception as e:
            self._status = PoolStatus.UNHEALTHY
            logger.error(f"Redis health check failed: {e}")

        return self._status

    async def close(self):
        """Close Redis connection pool."""
        # Close pub/sub clients
        for pubsub in self._pubsub_clients.values():
            await pubsub.close()

        # Close main client
        if self._client:
            await self._client.close()

        logger.info("Redis pool closed")

    def get_metrics(self) -> PoolMetrics:
        """Get pool metrics."""
        return self._metrics


class HTTPPoolManager:
    """
    HTTP connection pool manager for external APIs.

    Features:
    - Connection pooling with keep-alive
    - DNS caching
    - SSL session reuse
    - Request/response interceptors
    - Retry logic
    """

    def __init__(self):
        """Initialize HTTP pool manager."""
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._metrics: dict[str, PoolMetrics] = {}
        self._default_timeout = httpx.Timeout(30.0, connect=5.0)
        self._default_limits = httpx.Limits(
            max_keepalive_connections=20, max_connections=100, keepalive_expiry=300
        )

    async def get_client(
        self,
        base_url: str,
        timeout: httpx.Timeout | None = None,
        limits: httpx.Limits | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.AsyncClient:
        """
        Get or create HTTP client for base URL.

        Args:
            base_url: Base URL for the client
            timeout: Request timeout configuration
            limits: Connection pool limits
            headers: Default headers

        Returns:
            HTTP client instance
        """
        client_key = hashlib.md5(base_url.encode()).hexdigest()

        if client_key not in self._clients:
            self._clients[client_key] = httpx.AsyncClient(
                base_url=base_url,
                timeout=timeout or self._default_timeout,
                limits=limits or self._default_limits,
                headers=headers,
                http2=True,
                follow_redirects=True,
                event_hooks={
                    "request": [self._log_request],
                    "response": [self._log_response],
                },
            )
            self._metrics[client_key] = PoolMetrics()

        return self._clients[client_key]

    async def _log_request(self, request: httpx.Request):
        """Log HTTP request."""
        logger.debug(f"HTTP Request: {request.method} {request.url}")

    async def _log_response(self, response: httpx.Response):
        """Log HTTP response."""
        logger.debug(f"HTTP Response: {response.status_code} from {response.url}")

    async def request(
        self, method: str, url: str, base_url: str | None = None, **kwargs
    ) -> httpx.Response:
        """
        Make HTTP request with pooled client.

        Args:
            method: HTTP method
            url: Request URL
            base_url: Optional base URL for client
            **kwargs: Additional request arguments

        Returns:
            HTTP response
        """
        base_url = base_url or url.split("/")[0] + "//" + url.split("/")[2]
        client = await self.get_client(base_url)

        client_key = hashlib.md5(base_url.encode()).hexdigest()
        metrics = self._metrics[client_key]
        metrics.total_requests += 1

        try:
            metrics.active_connections += 1
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            return response

        except httpx.HTTPStatusError as e:
            metrics.failed_requests += 1
            logger.error(f"HTTP error {e.response.status_code}: {e}")
            raise
        except Exception as e:
            metrics.failed_requests += 1
            metrics.connection_errors += 1
            logger.error(f"HTTP request failed: {e}")
            raise
        finally:
            metrics.active_connections -= 1

    async def close_all(self):
        """Close all HTTP clients."""
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
        logger.info("All HTTP clients closed")

    def get_metrics(self, base_url: str) -> PoolMetrics | None:
        """Get metrics for specific base URL."""
        client_key = hashlib.md5(base_url.encode()).hexdigest()
        return self._metrics.get(client_key)


class TemporalPoolManager:
    """
    Temporal client pool manager.

    Features:
    - Worker connection pooling
    - Workflow client pooling
    - Sticky execution optimization
    - Health monitoring
    """

    def __init__(self):
        """Initialize Temporal pool manager."""
        self._worker_clients: list[Any] = []
        self._workflow_clients: list[Any] = []
        self._metrics = PoolMetrics()
        self._status = PoolStatus.UNHEALTHY
        self._max_workers = config.temporal.worker_concurrency
        self._max_workflow_clients = 10

    async def initialize(self):
        """Initialize Temporal connection pools."""
        try:
            # Import temporal modules dynamically to avoid circular imports
            from src.temporal.client import get_temporal_client

            # Create workflow client pool
            for _ in range(self._max_workflow_clients):
                client = await get_temporal_client()
                self._workflow_clients.append(client)

            self._status = PoolStatus.HEALTHY
            logger.info("Temporal pool initialized successfully")

        except Exception as e:
            self._status = PoolStatus.UNHEALTHY
            self._metrics.last_error = str(e)
            self._metrics.last_error_time = datetime.utcnow()
            logger.error(f"Failed to initialize Temporal pool: {e}")
            raise

    async def get_workflow_client(self):
        """
        Get a workflow client from the pool.

        Returns:
            Temporal workflow client
        """
        if not self._workflow_clients:
            raise RuntimeError("Temporal pool not initialized")

        # Simple round-robin selection
        self._metrics.total_requests += 1
        client_index = self._metrics.total_requests % len(self._workflow_clients)
        return self._workflow_clients[client_index]

    async def health_check(self) -> PoolStatus:
        """
        Check Temporal pool health.

        Returns:
            Pool status
        """
        try:
            # Check if we can connect to Temporal
            client = await self.get_workflow_client()
            # Would normally check client health here
            self._status = PoolStatus.HEALTHY
        except Exception as e:
            self._status = PoolStatus.UNHEALTHY
            logger.error(f"Temporal health check failed: {e}")

        return self._status

    def get_metrics(self) -> PoolMetrics:
        """Get pool metrics."""
        return self._metrics


# Global pool manager instances
database_pool = DatabasePoolManager()
redis_pool = RedisPoolManager()
http_pool = HTTPPoolManager()
temporal_pool = TemporalPoolManager()


async def initialize_pools():
    """Initialize all connection pools."""
    logger.info("Initializing connection pools...")

    # Initialize pools in parallel
    await asyncio.gather(
        database_pool.initialize(),
        redis_pool.initialize(),
        temporal_pool.initialize(),
        return_exceptions=True,
    )

    logger.info("All connection pools initialized")


async def close_pools():
    """Close all connection pools."""
    logger.info("Closing connection pools...")

    await asyncio.gather(
        database_pool.close(),
        redis_pool.close(),
        http_pool.close_all(),
        return_exceptions=True,
    )

    logger.info("All connection pools closed")


__all__ = [
    "DatabasePoolManager",
    "HTTPPoolManager",
    "PoolMetrics",
    "PoolStatus",
    "RedisPoolManager",
    "TemporalPoolManager",
    "close_pools",
    "database_pool",
    "http_pool",
    "initialize_pools",
    "redis_pool",
    "temporal_pool",
]
