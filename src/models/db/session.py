"""
Database session management.

Provides utilities for managing database connections and sessions.
"""

from collections.abc import AsyncGenerator
from typing import Any

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


async def close_db() -> None:
    """Close database connections."""
    global _engine, _async_session_factory

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


__all__ = [
    "close_db",
    "get_session",
    "get_session_factory",
    "init_db",
]
