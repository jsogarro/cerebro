"""
Health check endpoints for Research Platform.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import redis.asyncio as redis
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from src.core.config import settings
from src.core.provider_readiness import observed_provider_state
from src.middleware.auth_middleware import get_jwt_service
from src.models.db.session import get_session_factory

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_SECONDS = 2.0

router = APIRouter()


@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check() -> JSONResponse:
    """Basic health check endpoint."""
    return JSONResponse(
        content={"status": "healthy", "service": "research-platform-api"}
    )


async def _check_database() -> None:
    async with get_session_factory()() as session:
        await session.execute(text("SELECT 1"))


async def _check_redis() -> None:
    async with redis.from_url(settings.REDIS_URL) as client:
        await client.ping()


async def _check_audit_logger(request: Request) -> None:
    task = getattr(request.app.state.audit_logger, "flush_task", None)
    if task is None or task.done():
        raise RuntimeError("audit logger flush task is not running")


async def _check_jwt_keys() -> None:
    await get_jwt_service()


async def _probe(name: str, check: Callable[[], Awaitable[None]]) -> str:
    try:
        await asyncio.wait_for(check(), timeout=_PROBE_TIMEOUT_SECONDS)
    except (SQLAlchemyError, RedisError, OSError, RuntimeError, AttributeError):
        logger.warning("Readiness check failed: %s", name, exc_info=True)
        return "unavailable"
    return "ok"


@router.get("/ready", status_code=status.HTTP_200_OK)
async def readiness_check(request: Request) -> JSONResponse:
    """Readiness check endpoint for Kubernetes: 503 unless every dependency answers."""
    checks = {
        "database": await _probe("database", _check_database),
        "redis": await _probe("redis", _check_redis),
        "audit_logger": await _probe(
            "audit_logger", lambda: _check_audit_logger(request)
        ),
        "jwt_keys": await _probe("jwt_keys", _check_jwt_keys),
    }
    ready = all(v == "ok" for v in checks.values())
    # Informational: readiness never probes a provider (a probe spends tokens) and
    # a provider outage must not take the pod out of rotation.
    checks["providers"] = observed_provider_state(
        getattr(request.app.state, "gemini_service", None)
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK
        if ready
        else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "ready" if ready else "not_ready",
            "service": "research-platform-api",
            "checks": checks,
        },
    )


@router.get("/live", status_code=status.HTTP_200_OK)
async def liveness_check() -> JSONResponse:
    """Liveness check endpoint for Kubernetes."""
    return JSONResponse(content={"status": "alive"})
