"""
Main FastAPI application for Research Platform.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
from structlog import get_logger

from src.api.auth import router as auth_router
from src.api.routes import (
    agent_api,
    health,
    masr_api,
    query_api,
    reports,
    research,
    supervisor_api,
    websocket,
)
from src.api.services.event_publisher import event_publisher
from src.api.websocket.connection_manager import websocket_manager
from src.core.config import settings
from src.middleware.auth_middleware import AuthMiddleware

logger = get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle."""
    # Startup
    logger.info("Starting Research Platform API", environment=settings.ENVIRONMENT)

    # Initialize WebSocket services
    try:
        await event_publisher.initialize()
        logger.info("Event publisher initialized")
    except Exception as e:
        logger.warning(f"Failed to initialize event publisher: {e}")

    # Initialize database
    from src.models.db.session import init_db

    try:
        await init_db()
        logger.info("Database initialized")

        # Create tables if using SQLite (development)
        if "sqlite" in settings.DATABASE_URL:
            from src.models.db.agent_task import AgentTask
            from src.models.db.base import Base
            from src.models.db.research_project import ResearchProject as DBProject
            from src.models.db.research_result import ResearchResult
            from src.models.db.session import _engine
            from src.models.db.workflow_checkpoint import WorkflowCheckpoint

            tables = [
                Base.metadata.tables[t.__tablename__]
                for t in [DBProject, ResearchResult, AgentTask, WorkflowCheckpoint]
                if t.__tablename__ in Base.metadata.tables
            ]
            if _engine is not None and tables:
                async with _engine.begin() as conn:
                    await conn.run_sync(
                        Base.metadata.create_all, tables=tables
                    )
                logger.info("Database tables created")
    except Exception as e:
        logger.warning(f"Database initialization failed: {e}")

    # Initialize Gemini service
    gemini_service = None
    if settings.GEMINI_API_KEY:
        try:
            from src.services.gemini_service import GeminiService

            gemini_service = GeminiService(api_key=settings.GEMINI_API_KEY)
            logger.info("Gemini service initialized", model=gemini_service.model_name)
        except Exception as e:
            logger.warning(f"Gemini service initialization failed: {e}")
    else:
        logger.warning("GEMINI_API_KEY not set — agents will use fallback responses")

    # Pre-initialize the DirectExecutionService with Gemini
    from src.api.services.direct_execution_service import get_direct_execution_service

    get_direct_execution_service(gemini_service=gemini_service)

    yield

    # Shutdown
    logger.info("Shutting down Research Platform API")

    # Shutdown WebSocket services
    try:
        await event_publisher.shutdown()
        await websocket_manager.shutdown()
        logger.info("WebSocket services shut down")
    except Exception as e:
        logger.warning(f"Error shutting down WebSocket services: {e}")

    # await close_database()
    # await close_redis()
    # await close_temporal()


# Create FastAPI application
app = FastAPI(
    title="Research Platform API",
    description="Multi-Agent Graduate-Level Research Platform",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add Authentication middleware
app.add_middleware(AuthMiddleware)

# Mount Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# Include routers
app.include_router(health.router, tags=["health"])
app.include_router(auth_router, prefix="/api/v1")
app.include_router(research.router, prefix="/api/v1", tags=["research"])
app.include_router(reports.router, prefix="/api/v1", tags=["reports"])
# Agent Framework APIs (Research-Informed)
app.include_router(query_api.router, tags=["intelligent-query"])  # Primary API - MASR routed
app.include_router(agent_api.router, tags=["direct-agents"])     # Bypass API - Direct access
# MASR Dynamic Routing API
app.include_router(masr_api.router, tags=["masr-routing"])       # MASR routing intelligence
# Hierarchical Supervisor API (Week 3 - Talk Structurally, Act Hierarchically)
app.include_router(supervisor_api.router, tags=["supervisors"])  # Supervisor coordination
app.include_router(websocket.router, tags=["websocket"])


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global exception handler."""
    logger.error(
        "Unhandled exception",
        exc_info=exc,
        path=request.url.path,
        method=request.method,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )
