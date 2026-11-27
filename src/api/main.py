"""
Main FastAPI application for Research Platform.
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
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
    admin_security,
    agent_api,
    critiques,
    documents,
    health,
    masr_api,
    query_api,
    reports,
    research,
    run_stream,
    similarity_checks,
    supervisor_api,
    talkhier_api,
    users,
    websocket,
    workflows,
)
from src.api.services.event_publisher import event_publisher
from src.api.services.workflow_catalog import get_workflow_catalog
from src.api.websocket.connection_manager import websocket_manager
from src.core.config import settings
from src.core.observability import configure_logging
from src.core.process_hardening import make_non_dumpable
from src.middleware.auth_middleware import AuthMiddleware
from src.security.audit_logger import AuditLogger
from src.security.audit_middleware import (
    AuditTrailMiddleware,
    log_rejected_requests,
)
from src.security.security_headers import SecurityHeadersMiddleware
from src.services.critique_service import CRITIQUE_PER_WORKER_CONCURRENCY
from src.services.document_ingestion import require_document_key, run_purge_loop
from src.services.document_jobs import cancel_all
from src.services.similarity_service import SIMILARITY_PER_WORKER_CONCURRENCY

logger = get_logger()

make_non_dumpable()
configure_logging(settings.LOG_LEVEL)


_RESOURCE_CLOSE_TIMEOUT_SECONDS = 10.0


async def _close_lifespan_resource(
    resource_name: str,
    close: Callable[[], Awaitable[None]],
) -> None:
    """Close one lifespan-owned resource without blocking later cleanup."""

    try:
        await asyncio.wait_for(close(), timeout=_RESOURCE_CLOSE_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning("lifespan_resource_shutdown_timed_out", resource=resource_name)
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


def _start_document_purge(
    resources: AsyncExitStack,
    app: FastAPI,
    session_factory: Callable[[], AsyncSession] | None,
) -> asyncio.Task[None] | None:
    """Run the retention sweep until shutdown, whatever the upload flag says.

    Without a database nothing can be stored or expired, and uploads are
    refused rather than kept past their retention period.
    """
    app.state.document_purge_active = session_factory is not None
    resources.callback(app.state._state.pop, "document_purge_active", None)
    if session_factory is None:
        return None
    task = asyncio.create_task(
        run_purge_loop(session_factory, settings.DOCUMENT_PURGE_INTERVAL_S)
    )

    def stopped(done: asyncio.Task[None]) -> None:
        if done.cancelled() or done.exception() is None:
            return
        logger.error("document_purge_stopped", error=type(done.exception()).__name__)
        app.state.document_purge_active = False

    task.add_done_callback(stopped)
    resources.callback(task.cancel)
    return task


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle."""
    # Startup
    logger.info("Starting Research Platform API", environment=settings.ENVIRONMENT)
    require_document_key(settings)
    # An invalid enabled catalogue entry must stop startup, not the first request.
    get_workflow_catalog()

    async with AsyncExitStack() as resources:
        from src.middleware.auth_middleware import close_jwt_service

        resources.push_async_callback(
            _close_lifespan_resource, "jwt_service", close_jwt_service
        )
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

        await event_publisher.initialize()
        logger.info("Event publisher initialized")
        resources.push_async_callback(
            _close_lifespan_resource,
            "event_publisher",
            event_publisher.shutdown,
        )

        from src.models.db import session as db_session
        from src.models.db.session import close_db, get_session_factory, init_db

        database_initialized = False
        try:
            opens_engine = db_session._engine is None
            await init_db()
            if opens_engine:
                # Pooled connections are bound to this event loop; a later
                # lifespan in another loop must not inherit them.
                resources.push_async_callback(
                    _close_lifespan_resource, "database", close_db
                )
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
        except (SQLAlchemyError, OSError, ImportError) as e:
            logger.warning(f"Database initialization failed: {e}")

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
            except RuntimeError as e:
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

            persisted_resolver = PersistedExecutionAuthorityResolver(
                session_factory=durable_session_factory
            )
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
            restored = await direct_execution_service.restore_active_executions()
            logger.info("Active executions restored from persistence", count=restored)

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

            if event_publisher.redis_subscriber is not None:
                from src.api.services.run_event_wakeup import RunEventWakeupSubscriber

                wakeups = RunEventWakeupSubscriber(
                    redis_client=event_publisher.redis_subscriber,
                    session_factory=durable_session_factory,
                )
                wakeups.start()
                resources.push_async_callback(
                    _close_lifespan_resource,
                    "run_event_wakeups",
                    wakeups.stop,
                )

        _start_document_purge(resources, app, durable_session_factory)

        app.state.document_jobs = set()
        app.state.critique_reserved = 0
        app.state.critique_semaphore = asyncio.Semaphore(
            CRITIQUE_PER_WORKER_CONCURRENCY
        )
        app.state.similarity_semaphore = asyncio.Semaphore(
            SIMILARITY_PER_WORKER_CONCURRENCY
        )
        app.state.similarity_reserved = 0
        resources.push_async_callback(cancel_all, app.state.document_jobs)

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
            import asyncio as _asyncio

            from src.core.tracing import get_langfuse_client as _get_lf_client

            await _asyncio.to_thread(_get_lf_client)
            logger.info("Langfuse client pre-initialized")

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

            # await close_redis()
            # await close_temporal()


app = FastAPI(
    title="Research Platform API",
    description="Multi-Agent Graduate-Level Research Platform",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
)

app.add_middleware(
    IdempotencyMiddleware,
    store=ResilientIdempotencyStore(RedisIdempotencyStore(settings.REDIS_URL)),
)

# Audit sits inside authentication and rate limiting, so a request they reject
# never costs a durable audit write. Registered here, before RateLimit, it is
# inner to both (Starlette runs the last-registered middleware outermost).
app.middleware("http")(AuditTrailMiddleware())

app.add_middleware(
    RateLimitMiddleware,
    store=ResilientRateLimitStore(RedisRateLimitStore(settings.REDIS_URL)),
    requests_per_minute=settings.MAX_REQUESTS_PER_MINUTE,
    enabled=settings.ENABLE_RATE_LIMITING,
)

app.add_middleware(LLMCostDriftMiddleware)

app.add_middleware(AuthMiddleware)

# Registered after authentication, so rejection logging and the response
# headers wrap the responses that authentication and rate limiting produce.
app.middleware("http")(log_rejected_requests)
app.middleware("http")(
    SecurityHeadersMiddleware(
        # The local development stack is served over HTTP. HSTS is enabled
        # for non-development deployments where HTTPS is the expected scheme.
        hsts_enabled=settings.ENVIRONMENT != "development"
    )
)

# CORS must be the outermost application middleware. It terminates genuine
# browser preflights before authentication, while AuthMiddleware remains the
# exact public-route boundary for every other request, including OPTIONS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# Include routers
app.include_router(health.router, tags=["health"])
app.include_router(auth_router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(admin_security.router, prefix="/api/v1")
app.include_router(research.router, prefix="/api/v1", tags=["research"])
app.include_router(reports.router, prefix="/api/v1", tags=["reports"])
app.include_router(workflows.router, prefix="/api/v1", tags=["workflows"])
app.include_router(documents.router, prefix="/api/v1")
app.include_router(critiques.router, prefix="/api/v1")
app.include_router(similarity_checks.router, prefix="/api/v1")
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
