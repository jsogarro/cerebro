"""
Main FastAPI application for Research Platform.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
from starlette.responses import Response
from structlog import get_logger

from src.ai_brain.router.factory import MASRRuntime
from src.api.auth import router as auth_router
from src.api.middleware.cost_drift import LLMCostDriftMiddleware
from src.api.middleware.error_envelope import (
    build_error_payload,
    http_exception_handler,
    validation_exception_handler,
)
from src.api.middleware.idempotency import (
    IdempotencyMiddleware,
    RedisIdempotencyStore,
    ResilientIdempotencyStore,
)
from src.api.middleware.rate_limiter import (
    RateLimitMiddleware,
    RedisRateLimitStore,
    ResilientRateLimitStore,
)
from src.api.routes import (
    agent_api,
    health,
    masr_api,
    query_api,
    reports,
    research,
    run_stream,
    supervisor_api,
    talkhier_api,
    users,
    websocket,
)
from src.api.services.event_publisher import event_publisher
from src.api.websocket.connection_manager import websocket_manager
from src.core.config import settings
from src.middleware.auth_middleware import AuthMiddleware
from src.security.audit_logger import AuditLogger
from src.security.audit_middleware import AuditTrailMiddleware
from src.security.security_headers import SecurityHeadersMiddleware

logger = get_logger()


async def _close_lifespan_resource(
    resource_name: str,
    close: Callable[[], Awaitable[None]],
) -> None:
    """Close one lifespan-owned resource without blocking later cleanup."""

    try:
        await close()
    except Exception as exc:
        logger.warning(
            "lifespan_resource_shutdown_failed",
            resource=resource_name,
            error=type(exc).__name__,
        )


def _remove_app_state_if_owned(app: FastAPI, name: str, owned: object) -> None:
    """Remove state only when it still references this lifespan's instance."""

    if getattr(app.state, name, None) is owned:
        delattr(app.state, name)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle."""
    # Startup
    logger.info("Starting Research Platform API", environment=settings.ENVIRONMENT)

    async with AsyncExitStack() as resources:
        masr_runtime = await MASRRuntime.create(settings)
        resources.push_async_callback(
            _close_lifespan_resource,
            "masr_runtime",
            masr_runtime.close,
        )
        app.state.masr_runtime = masr_runtime
        resources.callback(
            _remove_app_state_if_owned,
            app,
            "masr_runtime",
            masr_runtime,
        )

        # Initialize WebSocket services
        try:
            await event_publisher.initialize()
            logger.info("Event publisher initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize event publisher: {e}")
        resources.push_async_callback(
            _close_lifespan_resource,
            "event_publisher",
            event_publisher.shutdown,
        )

        # Initialize database
        from src.models.db.session import get_session_factory, init_db

        database_initialized = False
        try:
            await init_db()
            database_initialized = True
            logger.info("Database initialized")

            # Create tables if using SQLite (development). The durable run
            # lifecycle tables (agent_runs/agent_run_tasks/agent_task_attempts/
            # agent_run_config_snapshots/agent_run_events/
            # agent_run_event_outbox) are included here so the SQLite dev path
            # is genuinely durable across a process restart, not merely
            # in-memory-with-extra-steps. They use only portable column types
            # (``PortableUUID``, ``JSON``, ``DateTime``, ``String``,
            # ``BigInteger``) already used elsewhere in this table list.
            # SQLite gets the ORM-level append-only guard on the event and
            # config-snapshot tables (``src/models/db/append_only.py``) but
            # not the Postgres trigger layer the alembic migration installs —
            # this matches the frozen schema's own documented expectation
            # that a ``Base.metadata.create_all`` path gets ORM enforcement
            # only.
            if "sqlite" in settings.DATABASE_URL:
                from src.models.db.agent_task import AgentTask
                from src.models.db.base import Base
                from src.models.db.research_project import ResearchProject as DBProject
                from src.models.db.research_result import ResearchResult
                from src.models.db.run_config_snapshot import AgentRunConfigSnapshot
                from src.models.db.run_event import AgentRunEvent, AgentRunEventOutbox
                from src.models.db.run_lifecycle import (
                    AgentRun,
                    AgentRunTask,
                    AgentTaskAttempt,
                )
                from src.models.db.session import _engine
                from src.models.db.workflow_checkpoint import WorkflowCheckpoint

                tables = [
                    Base.metadata.tables[t.__tablename__]
                    for t in [
                        DBProject,
                        ResearchResult,
                        AgentTask,
                        WorkflowCheckpoint,
                        AgentRun,
                        AgentRunTask,
                        AgentTaskAttempt,
                        AgentRunConfigSnapshot,
                        AgentRunEvent,
                        AgentRunEventOutbox,
                    ]
                    if t.__tablename__ in Base.metadata.tables
                ]
                if _engine is not None and tables:
                    async with _engine.begin() as conn:
                        await conn.run_sync(Base.metadata.create_all, tables=tables)
                    logger.info("Database tables created")
        except Exception as e:
            logger.warning(f"Database initialization failed: {e}")

        # Initialize Gemini service
        gemini_service = None
        if settings.GEMINI_API_KEY:
            try:
                from src.services.gemini_service import GeminiService

                gemini_service = GeminiService(api_key=settings.GEMINI_API_KEY)
                logger.info(
                    "Gemini service initialized", model=gemini_service.model_name
                )
            except Exception as e:
                logger.warning(f"Gemini service initialization failed: {e}")
        else:
            logger.warning(
                "GEMINI_API_KEY not set — agents will use fallback responses"
            )

        # Initialize all active MASR consumers from the application-owned runtime.
        from src.api.services.agent_execution_service import (
            configure_agent_execution_service,
        )
        from src.api.services.component_catalog import (
            build_application_component_registry,
        )
        from src.api.services.direct_execution_service import (
            configure_direct_execution_service,
        )
        from src.api.services.masr_routing_service import MASRRoutingService
        from src.api.services.research_kernel import compose_application_research_kernel
        from src.api.services.supervisor_coordination_service import (
            SupervisorCoordinationService,
        )
        from src.api.services.talkhier_session_service import TalkHierSessionService

        component_registry = build_application_component_registry()
        # ``database_initialized`` only means ``init_db()`` did not raise —
        # it can be True with no real session factory behind it (e.g. a test
        # that monkeypatches ``init_db`` to a no-op mock without ever setting
        # the module-level engine/session-factory globals). Guard the lookup
        # itself rather than trusting the flag, so that mismatch degrades to
        # "no durable persistence" instead of crashing the whole lifespan.
        durable_session_factory = None
        if database_initialized:
            try:
                durable_session_factory = get_session_factory()
            except Exception as e:
                logger.warning(f"Session factory unavailable: {e}")
        direct_execution_service = configure_direct_execution_service(
            gemini_service=gemini_service,
            masr_router=masr_runtime.router,
            component_registry=component_registry,
            session_factory=durable_session_factory,
            event_publisher=event_publisher,
        )
        # Tests may explicitly place a trusted resolver on application state
        # before entering this lifespan, which takes priority. Otherwise,
        # production wires the durable, DB-backed resolver and warms its
        # cache from every persisted config snapshot before the app starts
        # serving — required because ``PersistedExecutionAuthorityResolver
        # .resolve()`` is synchronous and only ever reads that in-process
        # cache; without warming, every authority admitted before this
        # restart would appear unavailable even though its row still exists.
        existing_resolver = getattr(app.state, "execution_authority_resolver", None)
        if existing_resolver is not None:
            direct_execution_service.execution_authority_resolver = existing_resolver
        elif durable_session_factory is not None:
            from src.api.services.execution_authority_resolver import (
                PersistedExecutionAuthorityResolver,
            )

            persisted_resolver = PersistedExecutionAuthorityResolver()
            try:
                async with durable_session_factory() as warm_session:
                    warmed = await persisted_resolver.warm_from_snapshots(warm_session)
                logger.info(
                    "Execution authority resolver warmed from snapshots",
                    count=warmed,
                )
            except Exception as e:
                logger.warning(f"Failed to warm execution authority resolver: {e}")
            direct_execution_service.execution_authority_resolver = persisted_resolver
        else:
            direct_execution_service.execution_authority_resolver = None

        if durable_session_factory is not None:
            try:
                restored = await direct_execution_service.restore_active_executions()
                logger.info(
                    "Active executions restored from persistence", count=restored
                )
            except Exception as e:
                logger.warning(f"Failed to restore active executions: {e}")

            from src.api.services.outbox_relay import OutboxRelay

            outbox_relay = OutboxRelay(
                session_factory=durable_session_factory,
                event_publisher=event_publisher,
            )
            outbox_relay.start()
            resources.push_async_callback(
                _close_lifespan_resource,
                "outbox_relay",
                outbox_relay.stop,
            )

        audit_logger = AuditLogger(session_factory=durable_session_factory)
        await audit_logger.start()
        resources.push_async_callback(
            _close_lifespan_resource,
            "audit_logger",
            audit_logger.stop,
        )
        app.state.audit_logger = audit_logger
        resources.callback(
            _remove_app_state_if_owned,
            app,
            "audit_logger",
            audit_logger,
        )

        resources.push_async_callback(
            _close_lifespan_resource,
            "direct_execution_service",
            direct_execution_service.close,
        )
        app.state.direct_execution_service = direct_execution_service
        resources.callback(
            _remove_app_state_if_owned,
            app,
            "direct_execution_service",
            direct_execution_service,
        )

        agent_execution_service = configure_agent_execution_service(
            component_registry=component_registry,
        )
        app.state.agent_execution_service = agent_execution_service
        resources.callback(
            _remove_app_state_if_owned,
            app,
            "agent_execution_service",
            agent_execution_service,
        )

        research_kernel = compose_application_research_kernel(
            direct_execution_service,
            agent_execution_service,
        )
        app.state.research_kernel = research_kernel
        resources.callback(
            _remove_app_state_if_owned,
            app,
            "research_kernel",
            research_kernel,
        )

        masr_routing_service = MASRRoutingService(
            router=masr_runtime.router,
            component_registry=component_registry,
        )
        resources.push_async_callback(
            _close_lifespan_resource,
            "masr_routing_service",
            masr_routing_service.close,
        )
        app.state.masr_routing_service = masr_routing_service
        resources.callback(
            _remove_app_state_if_owned,
            app,
            "masr_routing_service",
            masr_routing_service,
        )

        talkhier_session_service = TalkHierSessionService(
            masr_router=masr_runtime.router,
            component_registry=component_registry,
        )
        resources.push_async_callback(
            _close_lifespan_resource,
            "talkhier_session_service",
            talkhier_session_service.close,
        )
        app.state.talkhier_session_service = talkhier_session_service
        resources.callback(
            _remove_app_state_if_owned,
            app,
            "talkhier_session_service",
            talkhier_session_service,
        )

        supervisor_coordination_service = SupervisorCoordinationService(
            component_registry=component_registry,
        )
        resources.push_async_callback(
            _close_lifespan_resource,
            "supervisor_coordination_service",
            supervisor_coordination_service.close,
        )
        app.state.supervisor_coordination_service = supervisor_coordination_service
        resources.callback(
            _remove_app_state_if_owned,
            app,
            "supervisor_coordination_service",
            supervisor_coordination_service,
        )

        # FIX 4: Eagerly initialize the Langfuse client at startup (when enabled) so
        # the first real request does not pay the SDK-import + client-construction cost
        # synchronously on the event loop.
        if settings.LANGFUSE_ENABLED:
            try:
                import asyncio as _asyncio

                from src.core.tracing import get_langfuse_client as _get_lf_client

                await _asyncio.to_thread(_get_lf_client)
                logger.info("Langfuse client pre-initialized")
            except Exception as _lf_init_err:
                logger.warning(
                    f"Langfuse pre-initialization failed (non-fatal): {_lf_init_err}"
                )

        try:
            yield
        finally:
            logger.info("Shutting down Research Platform API")

            await _close_lifespan_resource(
                "websocket_manager",
                websocket_manager.shutdown,
            )

            # Flush + stop Langfuse background export threads (no-op when disabled).
            try:
                import asyncio

                from src.core.tracing import shutdown_langfuse

                await asyncio.wait_for(
                    asyncio.to_thread(shutdown_langfuse),
                    timeout=5.0,
                )
                logger.info("Langfuse tracing shut down")
            except TimeoutError:
                logger.warning(
                    "Langfuse shutdown timed out after 5 s — background export threads may still be running"
                )
            except Exception as e:
                logger.warning(f"Error shutting down Langfuse: {e}")

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

app.add_middleware(
    IdempotencyMiddleware,
    store=ResilientIdempotencyStore(RedisIdempotencyStore(settings.REDIS_URL)),
)

app.add_middleware(
    RateLimitMiddleware,
    store=ResilientRateLimitStore(RedisRateLimitStore(settings.REDIS_URL)),
    requests_per_minute=settings.MAX_REQUESTS_PER_MINUTE,
    enabled=settings.ENABLE_RATE_LIMITING,
)

app.add_middleware(LLMCostDriftMiddleware)

# Add Authentication middleware
app.add_middleware(AuthMiddleware)

# Security controls are registered after the existing middleware so they are
# outermost at runtime: audit sees responses produced by rate limiting and
# authentication, while headers are applied to those responses as well.
app.middleware("http")(AuditTrailMiddleware())
app.middleware("http")(
    SecurityHeadersMiddleware(
        # The local development stack is served over HTTP. HSTS is enabled
        # for non-development deployments where HTTPS is the expected scheme.
        hsts_enabled=settings.ENVIRONMENT != "development"
    )
)

# Mount Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# Include routers
app.include_router(health.router, tags=["health"])
app.include_router(auth_router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(research.router, prefix="/api/v1", tags=["research"])
app.include_router(reports.router, prefix="/api/v1", tags=["reports"])
# Agent Framework APIs (Research-Informed)
app.include_router(
    query_api.router, tags=["intelligent-query"]
)  # Primary API - MASR routed
app.include_router(
    agent_api.router, tags=["direct-agents"]
)  # Bypass API - Direct access
# MASR Dynamic Routing API
app.include_router(masr_api.router, tags=["masr-routing"])  # MASR routing intelligence
# Hierarchical Supervisor API (Week 3 - Talk Structurally, Act Hierarchically)
app.include_router(
    supervisor_api.router, tags=["supervisors"]
)  # Supervisor coordination
app.include_router(talkhier_api.router, tags=["talkhier"])
app.include_router(websocket.router, tags=["websocket"])
app.include_router(run_stream.router, prefix="/api/v1", tags=["run-stream"])


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
        content=build_error_payload(
            code="INTERNAL_SERVER_ERROR",
            message="Internal server error",
        ),
    )


app.add_exception_handler(
    HTTPException,
    cast(
        Callable[[Request, Exception], Response | Awaitable[Response]],
        http_exception_handler,
    ),
)
app.add_exception_handler(
    RequestValidationError,
    cast(
        Callable[[Request, Exception], Response | Awaitable[Response]],
        validation_exception_handler,
    ),
)
app.add_exception_handler(
    Exception,
    cast(
        Callable[[Request, Exception], Response | Awaitable[Response]],
        global_exception_handler,
    ),
)
