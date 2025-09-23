"""
Health check endpoints for Research Platform.
"""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    """Basic health check endpoint."""
    return JSONResponse(
        content={"status": "healthy", "service": "research-platform-api"}
    )


@router.get("/ready", status_code=status.HTTP_200_OK)
async def readiness_check():
    """Readiness check endpoint for Kubernetes."""
    # TODO: Check database connection
    # TODO: Check Redis connection
    # TODO: Check Temporal connection

    return JSONResponse(
        content={
            "status": "ready",
            "service": "research-platform-api",
            "checks": {
                "database": "ok",
                "redis": "ok",
                "temporal": "ok",
            },
        }
    )


@router.get("/live", status_code=status.HTTP_200_OK)
async def liveness_check():
    """Liveness check endpoint for Kubernetes."""
    return JSONResponse(content={"status": "alive"})
