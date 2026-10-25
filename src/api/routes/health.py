"""
Health check endpoints for Research Platform.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import redis.asyncio as redis
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.core.config import settings
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
    client = redis.from_url(settings.REDIS_URL)
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _probe(name: str, check: Callable[[], Awaitable[None]]) -> str:
    try:
        await asyncio.wait_for(check(), timeout=_PROBE_TIMEOUT_SECONDS)
    except Exception:
        logger.warning("Readiness check failed: %s", name, exc_info=True)
        return "unavailable"
    return "ok"


@router.get("/ready", status_code=status.HTTP_200_OK)
async def readiness_check() -> JSONResponse:
    """Readiness check endpoint for Kubernetes: 503 unless database and Redis answer."""
    checks = {
        "database": await _probe("database", _check_database),
        "redis": await _probe("redis", _check_redis),
    }
    ready = all(v == "ok" for v in checks.values())
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
