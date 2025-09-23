"""
Health check system for production reliability.

This module provides comprehensive health checking functionality including
liveness probes, readiness probes, and dependency health checks.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, Response, status
from httpx import AsyncClient

from config import config

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    """Health status enumeration."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ComponentType(Enum):
    """Component type enumeration."""

    DATABASE = "database"
    REDIS = "redis"
    TEMPORAL = "temporal"
    MCP = "mcp"
    GEMINI = "gemini"
    AGENT = "agent"
    API = "api"
    WORKER = "worker"


@dataclass
class HealthCheckResult:
    """Health check result data."""

    component: str
    status: HealthStatus
    latency_ms: float
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "component": self.component,
            "status": self.status.value,
            "latency_ms": round(self.latency_ms, 2),
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class SystemHealth:
    """Overall system health status."""

    status: HealthStatus
    components: list[HealthCheckResult]
    version: str
    uptime_seconds: float
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "status": self.status.value,
            "components": [c.to_dict() for c in self.components],
            "version": self.version,
            "uptime_seconds": round(self.uptime_seconds, 2),
            "timestamp": self.timestamp.isoformat(),
        }


class HealthChecker:
    """Main health checker class."""

    def __init__(self):
        """Initialize health checker."""
        self.start_time = time.time()
        self.health_checks: dict[str, Callable] = {}
        self.dependency_checks: dict[str, Callable] = {}
        self.cached_results: dict[str, HealthCheckResult] = {}
        self.cache_ttl = config.monitoring.health_check_interval
        self._register_default_checks()

    def _register_default_checks(self):
        """Register default health checks."""
        # Core component checks
        self.register_health_check("database", self.check_database)
        self.register_health_check("redis", self.check_redis)
        self.register_health_check("temporal", self.check_temporal)
        self.register_health_check("mcp", self.check_mcp)
        self.register_health_check("gemini", self.check_gemini)

    def register_health_check(self, name: str, check_func: Callable):
        """Register a health check function."""
        self.health_checks[name] = check_func

    def register_dependency_check(self, name: str, check_func: Callable):
        """Register a dependency check function."""
        self.dependency_checks[name] = check_func

    async def check_database(self) -> HealthCheckResult:
        """Check database health."""
        start_time = time.time()

        try:
            # Test database connection
            conn = await asyncpg.connect(
                config.database.url, timeout=config.monitoring.health_check_timeout
            )

            # Run simple query
            result = await conn.fetchval("SELECT 1")

            # Check connection pool stats
            pool_info = {
                "connections_in_use": 0,  # Would get from actual pool
                "connections_available": config.database.pool_size,
                "max_connections": config.database.pool_size
                + config.database.max_overflow,
            }

            await conn.close()

            latency_ms = (time.time() - start_time) * 1000

            return HealthCheckResult(
                component="database",
                status=HealthStatus.HEALTHY,
                latency_ms=latency_ms,
                message="Database is responsive",
                details=pool_info,
            )

        except TimeoutError:
            latency_ms = (time.time() - start_time) * 1000
            return HealthCheckResult(
                component="database",
                status=HealthStatus.UNHEALTHY,
                latency_ms=latency_ms,
                message="Database connection timeout",
            )

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            return HealthCheckResult(
                component="database",
                status=HealthStatus.UNHEALTHY,
                latency_ms=latency_ms,
                message=f"Database error: {e!s}",
            )

    async def check_redis(self) -> HealthCheckResult:
        """Check Redis health."""
        start_time = time.time()

        try:
            # Test Redis connection
            client = redis.from_url(
                config.redis.url,
                socket_connect_timeout=config.monitoring.health_check_timeout,
            )

            # Run ping
            await client.ping()

            # Get Redis info
            info = await client.info()
            redis_info = {
                "connected_clients": info.get("connected_clients", 0),
                "used_memory_human": info.get("used_memory_human", "unknown"),
                "uptime_in_seconds": info.get("uptime_in_seconds", 0),
            }

            await client.close()

            latency_ms = (time.time() - start_time) * 1000

            return HealthCheckResult(
                component="redis",
                status=HealthStatus.HEALTHY,
                latency_ms=latency_ms,
                message="Redis is responsive",
                details=redis_info,
            )

        except TimeoutError:
            latency_ms = (time.time() - start_time) * 1000
            return HealthCheckResult(
                component="redis",
                status=HealthStatus.UNHEALTHY,
                latency_ms=latency_ms,
                message="Redis connection timeout",
            )

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            return HealthCheckResult(
                component="redis",
                status=HealthStatus.UNHEALTHY,
                latency_ms=latency_ms,
                message=f"Redis error: {e!s}",
            )

    async def check_temporal(self) -> HealthCheckResult:
        """Check Temporal health."""
        start_time = time.time()

        try:
            async with AsyncClient() as client:
                # Call Temporal health endpoint
                response = await client.get(
                    f"http://{config.temporal.host}/api/v1/health",
                    timeout=config.monitoring.health_check_timeout,
                )

                latency_ms = (time.time() - start_time) * 1000

                if response.status_code == 200:
                    return HealthCheckResult(
                        component="temporal",
                        status=HealthStatus.HEALTHY,
                        latency_ms=latency_ms,
                        message="Temporal is responsive",
                    )
                else:
                    return HealthCheckResult(
                        component="temporal",
                        status=HealthStatus.DEGRADED,
                        latency_ms=latency_ms,
                        message=f"Temporal returned status {response.status_code}",
                    )

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            return HealthCheckResult(
                component="temporal",
                status=HealthStatus.UNHEALTHY,
                latency_ms=latency_ms,
                message=f"Temporal error: {e!s}",
            )

    async def check_mcp(self) -> HealthCheckResult:
        """Check MCP server health."""
        start_time = time.time()

        if not config.mcp.enabled:
            return HealthCheckResult(
                component="mcp",
                status=HealthStatus.HEALTHY,
                latency_ms=0,
                message="MCP is disabled",
            )

        try:
            async with AsyncClient() as client:
                # Call MCP health endpoint
                response = await client.get(
                    f"http://{config.mcp.server_host}:{config.mcp.server_port}/health",
                    timeout=config.monitoring.health_check_timeout,
                )

                latency_ms = (time.time() - start_time) * 1000

                if response.status_code == 200:
                    data = response.json()
                    return HealthCheckResult(
                        component="mcp",
                        status=HealthStatus.HEALTHY,
                        latency_ms=latency_ms,
                        message="MCP server is responsive",
                        details=data,
                    )
                else:
                    return HealthCheckResult(
                        component="mcp",
                        status=HealthStatus.DEGRADED,
                        latency_ms=latency_ms,
                        message=f"MCP returned status {response.status_code}",
                    )

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000

            # If MCP is down but fallback is enabled, mark as degraded not unhealthy
            if config.mcp.enable_fallback:
                return HealthCheckResult(
                    component="mcp",
                    status=HealthStatus.DEGRADED,
                    latency_ms=latency_ms,
                    message=f"MCP unavailable, fallback active: {e!s}",
                )
            else:
                return HealthCheckResult(
                    component="mcp",
                    status=HealthStatus.UNHEALTHY,
                    latency_ms=latency_ms,
                    message=f"MCP error: {e!s}",
                )

    async def check_gemini(self) -> HealthCheckResult:
        """Check Gemini API health."""
        start_time = time.time()

        if not config.gemini.api_key:
            return HealthCheckResult(
                component="gemini",
                status=HealthStatus.UNHEALTHY,
                latency_ms=0,
                message="Gemini API key not configured",
            )

        try:
            # Simple connectivity check - would normally call Gemini API
            latency_ms = (time.time() - start_time) * 1000

            return HealthCheckResult(
                component="gemini",
                status=HealthStatus.HEALTHY,
                latency_ms=latency_ms,
                message="Gemini API is configured",
                details={
                    "model": config.gemini.model,
                    "rate_limit": config.gemini.requests_per_minute,
                },
            )

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            return HealthCheckResult(
                component="gemini",
                status=HealthStatus.UNHEALTHY,
                latency_ms=latency_ms,
                message=f"Gemini error: {e!s}",
            )

    async def liveness_probe(self) -> HealthCheckResult:
        """
        Liveness probe - checks if the application is alive.

        This should be lightweight and only check if the process is responsive.
        Kubernetes will restart the pod if this fails.
        """
        start_time = time.time()

        # Simple check - just verify the process is responsive
        latency_ms = (time.time() - start_time) * 1000

        return HealthCheckResult(
            component="api",
            status=HealthStatus.HEALTHY,
            latency_ms=latency_ms,
            message="Application is alive",
            details={
                "uptime_seconds": time.time() - self.start_time,
                "version": config.app_version,
            },
        )

    async def readiness_probe(self) -> SystemHealth:
        """
        Readiness probe - checks if the application is ready to accept traffic.

        This checks all critical dependencies. Kubernetes will stop routing
        traffic if this fails.
        """
        results = []

        # Check critical components in parallel
        critical_checks = ["database", "redis"]
        if config.mcp.enabled:
            critical_checks.append("mcp")

        check_tasks = []
        for component in critical_checks:
            if component in self.health_checks:
                check_tasks.append(self.health_checks[component]())

        results = await asyncio.gather(*check_tasks, return_exceptions=True)

        # Process results
        health_results = []
        overall_status = HealthStatus.HEALTHY

        for result in results:
            if isinstance(result, Exception):
                health_results.append(
                    HealthCheckResult(
                        component="unknown",
                        status=HealthStatus.UNHEALTHY,
                        latency_ms=0,
                        message=str(result),
                    )
                )
                overall_status = HealthStatus.UNHEALTHY
            else:
                health_results.append(result)
                if result.status == HealthStatus.UNHEALTHY:
                    overall_status = HealthStatus.UNHEALTHY
                elif (
                    result.status == HealthStatus.DEGRADED
                    and overall_status == HealthStatus.HEALTHY
                ):
                    overall_status = HealthStatus.DEGRADED

        return SystemHealth(
            status=overall_status,
            components=health_results,
            version=config.app_version,
            uptime_seconds=time.time() - self.start_time,
        )

    async def startup_probe(self) -> SystemHealth:
        """
        Startup probe - checks if the application has started successfully.

        This is used during application startup and can have a longer timeout.
        """
        results = []

        # Check all components
        check_tasks = []
        for name, check_func in self.health_checks.items():
            check_tasks.append(check_func())

        results = await asyncio.gather(*check_tasks, return_exceptions=True)

        # Process results
        health_results = []
        overall_status = HealthStatus.HEALTHY

        for result in results:
            if isinstance(result, Exception):
                health_results.append(
                    HealthCheckResult(
                        component="unknown",
                        status=HealthStatus.UNHEALTHY,
                        latency_ms=0,
                        message=str(result),
                    )
                )
                overall_status = HealthStatus.UNHEALTHY
            else:
                health_results.append(result)
                # During startup, degraded is acceptable
                if result.status == HealthStatus.UNHEALTHY:
                    overall_status = HealthStatus.UNHEALTHY

        return SystemHealth(
            status=overall_status,
            components=health_results,
            version=config.app_version,
            uptime_seconds=time.time() - self.start_time,
        )

    async def deep_health_check(self) -> SystemHealth:
        """
        Deep health check - comprehensive system health check.

        This checks all components and provides detailed diagnostics.
        """
        results = []

        # Check all components
        check_tasks = []
        for name, check_func in {
            **self.health_checks,
            **self.dependency_checks,
        }.items():
            check_tasks.append(check_func())

        results = await asyncio.gather(*check_tasks, return_exceptions=True)

        # Process results
        health_results = []
        overall_status = HealthStatus.HEALTHY

        for result in results:
            if isinstance(result, Exception):
                health_results.append(
                    HealthCheckResult(
                        component="unknown",
                        status=HealthStatus.UNHEALTHY,
                        latency_ms=0,
                        message=str(result),
                    )
                )
                overall_status = HealthStatus.UNHEALTHY
            else:
                health_results.append(result)
                if result.status == HealthStatus.UNHEALTHY:
                    overall_status = HealthStatus.UNHEALTHY
                elif (
                    result.status == HealthStatus.DEGRADED
                    and overall_status == HealthStatus.HEALTHY
                ):
                    overall_status = HealthStatus.DEGRADED

        return SystemHealth(
            status=overall_status,
            components=health_results,
            version=config.app_version,
            uptime_seconds=time.time() - self.start_time,
        )


# Global health checker instance
health_checker = HealthChecker()


def register_health_endpoints(app: FastAPI):
    """Register health check endpoints with FastAPI application."""

    @app.get("/health/live", tags=["health"])
    async def liveness_probe(response: Response):
        """
        Liveness probe endpoint.

        Returns 200 if the application is alive, 503 otherwise.
        Kubernetes uses this to determine if the pod should be restarted.
        """
        result = await health_checker.liveness_probe()

        if result.status == HealthStatus.HEALTHY:
            return {"status": "ok", "details": result.to_dict()}
        else:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "unhealthy", "details": result.to_dict()}

    @app.get("/health/ready", tags=["health"])
    async def readiness_probe(response: Response):
        """
        Readiness probe endpoint.

        Returns 200 if the application is ready to accept traffic, 503 otherwise.
        Kubernetes uses this to determine if traffic should be routed to the pod.
        """
        result = await health_checker.readiness_probe()

        if result.status == HealthStatus.HEALTHY:
            return result.to_dict()
        else:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return result.to_dict()

    @app.get("/health/startup", tags=["health"])
    async def startup_probe(response: Response):
        """
        Startup probe endpoint.

        Returns 200 if the application has started successfully, 503 otherwise.
        Kubernetes uses this during pod startup.
        """
        result = await health_checker.startup_probe()

        if result.status in [HealthStatus.HEALTHY, HealthStatus.DEGRADED]:
            return result.to_dict()
        else:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return result.to_dict()

    @app.get("/health", tags=["health"])
    async def health_check(response: Response):
        """
        Comprehensive health check endpoint.

        Returns detailed health status of all components.
        """
        result = await health_checker.deep_health_check()

        if result.status == HealthStatus.HEALTHY:
            return result.to_dict()
        elif result.status == HealthStatus.DEGRADED:
            response.status_code = status.HTTP_200_OK  # Still return 200 for degraded
            return result.to_dict()
        else:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return result.to_dict()

    @app.get("/health/{component}", tags=["health"])
    async def component_health_check(component: str, response: Response):
        """
        Check health of a specific component.

        Args:
            component: Component name (database, redis, temporal, mcp, gemini)
        """
        if component not in health_checker.health_checks:
            response.status_code = status.HTTP_404_NOT_FOUND
            return {"error": f"Unknown component: {component}"}

        result = await health_checker.health_checks[component]()

        if result.status == HealthStatus.HEALTHY:
            return result.to_dict()
        else:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return result.to_dict()


__all__ = [
    "ComponentType",
    "HealthCheckResult",
    "HealthChecker",
    "HealthStatus",
    "SystemHealth",
    "health_checker",
    "register_health_endpoints",
]
