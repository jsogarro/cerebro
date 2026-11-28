"""
Database session management.

Provides utilities for managing database connections and sessions.
"""

import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool
from structlog import get_logger

from src.core.config import settings

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None
_maintenance_engine: AsyncEngine | None = None
_maintenance_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_database_url() -> str:
    """
    Get database URL from settings.

    Returns:
        Database URL
    """
    return str(settings.DATABASE_URL)


def create_engine(
    database_url: str | None = None,
    pool_size: int = 20,
    max_overflow: int = 10,
    pool_timeout: int = 30,
    echo: bool = False,
    echo_pool: bool = False,
    use_null_pool: bool = False,
) -> AsyncEngine:
    """
    Create async database engine.

    Args:
        database_url: Database URL (uses settings if not provided)
        pool_size: Connection pool size
        max_overflow: Maximum overflow connections
        pool_timeout: Pool timeout in seconds
        echo: Echo SQL statements
        echo_pool: Echo pool events
        use_null_pool: Use NullPool instead of QueuePool

    Returns:
        Async engine
    """
    if not database_url:
        database_url = get_database_url()

    # Configure pool class
    # For async engines, use AsyncAdaptedQueuePool (default) or NullPool
    poolclass = NullPool if use_null_pool else AsyncAdaptedQueuePool

    engine_kwargs: dict[str, Any] = {
        "echo": echo,
        "echo_pool": echo_pool,
        "poolclass": poolclass,
    }
    if not use_null_pool:
        engine_kwargs["pool_size"] = pool_size
        engine_kwargs["max_overflow"] = max_overflow
        engine_kwargs["pool_timeout"] = pool_timeout
        engine_kwargs["pool_pre_ping"] = True
        engine_kwargs["pool_recycle"] = 3600

    engine = create_async_engine(database_url, **engine_kwargs)

    if echo_pool:

        @event.listens_for(engine.sync_engine, "connect")
        def receive_connect(dbapi_conn: Any, connection_record: Any) -> None:
            logger.debug("pool_connect", connection_record=str(connection_record))

        @event.listens_for(engine.sync_engine, "checkout")
        def receive_checkout(
            dbapi_conn: Any,
            connection_record: Any,
            connection_proxy: Any,
        ) -> None:
            logger.debug("pool_checkout", connection_record=str(connection_record))

    return engine


async def init_db(database_url: str | None = None, **engine_kwargs: Any) -> None:
    """
    Initialize database connection.

    Args:
        database_url: Database URL
        **engine_kwargs: Additional engine arguments
    """
    global _engine, _async_session_factory

    if _engine is not None:
        logger.warning("database_already_initialized")
        return

    logger.info("initializing_database_connection")

    # Use NullPool for SQLite (QueuePool is incompatible with aiosqlite)
    url = database_url or get_database_url()
    if "sqlite" in url:
        engine_kwargs.setdefault("use_null_pool", True)

    _engine = create_engine(database_url, **engine_kwargs)

    _async_session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    # Test connection
    try:
        async with _engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("database_connection_successful")
    except Exception as e:
        logger.error("database_connection_failed", error=str(e))
        await close_db()
        raise

    try:
        _init_maintenance_engine(url, engine_kwargs)
    except RuntimeError:
        await close_db()
        raise


def _init_maintenance_engine(database_url: str, engine_kwargs: dict[str, Any]) -> None:
    global _maintenance_engine, _maintenance_session_factory

    maintenance_url = settings.MAINTENANCE_DATABASE_URL
    if not maintenance_url:
        if "sqlite" in database_url:
            return
        raise RuntimeError(
            "MAINTENANCE_DATABASE_URL is required with a PostgreSQL database: "
            "the application role sees no rows without a tenant context, so the "
            "cross-tenant jobs cannot fall back to it."
        )
    _maintenance_engine = create_engine(
        maintenance_url, **{**engine_kwargs, "pool_size": 2, "max_overflow": 2}
    )
    _maintenance_session_factory = async_sessionmaker(
        _maintenance_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


async def close_db() -> None:
    """Close database connections."""
    global _engine, _async_session_factory
    global _maintenance_engine, _maintenance_session_factory

    if _maintenance_engine is not None:
        await _maintenance_engine.dispose()
        _maintenance_engine = None
        _maintenance_session_factory = None
    if _engine is not None:
        logger.info("closing_database_connections")
        await _engine.dispose()
        _engine = None
        _async_session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    Get async session factory.

    Returns:
        Session factory

    Raises:
        RuntimeError: If database not initialized
    """
    if _async_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _async_session_factory


def get_maintenance_session_factory() -> async_sessionmaker[AsyncSession]:
    """Session factory for the cross-tenant jobs (outbox relay, warm-up, purge).

    Connects as the maintenance role, whose per-table policies admit exactly
    those reads. SQLite has no roles, so there the application factory serves;
    ``init_db`` refuses to start against PostgreSQL without the URL.
    """
    if _maintenance_session_factory is not None:
        return _maintenance_session_factory
    return get_session_factory()


@asynccontextmanager
async def tenant_session(
    factory: Callable[[], AsyncSession], organization_id: str | uuid.UUID | None
) -> AsyncIterator[AsyncSession]:
    """Open a session whose every transaction is scoped to one organization.

    The setting is applied after each ``BEGIN`` and is transaction-local, so a
    commit mid-session cannot drop the tenant and the pooled connection carries
    nothing into its next borrower. ``None`` sets nothing: the row policies then
    admit no rows, which is the fail-closed answer for a missing tenant.
    """
    async with factory() as session:
        if organization_id is not None:
            scope_to_organization(session, organization_id)
        yield session


def scope_to_organization(
    session: AsyncSession, organization_id: str | uuid.UUID
) -> None:
    """Apply the tenant after every later ``BEGIN`` of an existing session."""
    event.listen(session.sync_session, "after_begin", _scope_to(str(organization_id)))


def _scope_to(organization_id: str) -> Callable[[Any, Any, Any], None]:
    def apply(_session: Any, _transaction: Any, connection: Any) -> None:
        if connection.dialect.name == "postgresql":
            connection.execute(
                text("SELECT set_config('app.current_org_id', :organization_id, true)"),
                {"organization_id": organization_id},
            )

    return apply


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Get database session.

    Yields:
        Database session
    """
    session_factory = get_session_factory()

    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# scope="function" runs get_session's commit before the response is sent: the
# default request scope commits in teardown, after the client already has its 2xx,
# and a failed commit would have nothing left to turn into an error.
SessionDep = Depends(get_session, scope="function")


__all__ = [
    "SessionDep",
    "close_db",
    "get_maintenance_session_factory",
    "get_session",
    "get_session_factory",
    "init_db",
    "scope_to_organization",
    "tenant_session",
]
