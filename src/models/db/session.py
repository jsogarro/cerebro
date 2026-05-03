"""
Database session management.

Provides utilities for managing database connections and sessions.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, QueuePool
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
    poolclass = NullPool if use_null_pool else QueuePool

    # Build engine kwargs
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

    # Create engine
    engine = create_async_engine(database_url, **engine_kwargs)

    # Add event listeners for debugging
    if echo_pool:

        @event.listens_for(engine.sync_engine, "connect")  # type: ignore[untyped-decorator]
        def receive_connect(dbapi_conn: Any, connection_record: Any) -> None:
            logger.debug("pool_connect", connection_record=str(connection_record))

        @event.listens_for(engine.sync_engine, "checkout")  # type: ignore[untyped-decorator]
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

    # Create engine
    _engine = create_engine(database_url, **engine_kwargs)

    # Create session factory
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


@asynccontextmanager
async def get_transaction() -> AsyncGenerator[AsyncSession, None]:
    """
    Get database session with transaction.

    Yields:
        Database session with active transaction
    """
    session_factory = get_session_factory()

    async with session_factory() as session, session.begin():
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def execute_in_transaction(func: Any, *args: Any, **kwargs: Any) -> Any:
    """
    Execute function in transaction.

    Args:
        func: Async function to execute
        *args: Function arguments
        **kwargs: Function keyword arguments

    Returns:
        Function result
    """
    async with get_transaction() as session:
        return await func(session, *args, **kwargs)


class DatabaseManager:
    """
    Database manager for advanced operations.

    Provides utilities for health checks, statistics, and maintenance.
    """

    def __init__(self, engine: AsyncEngine | None = None):
        """
        Initialize database manager.

        Args:
            engine: Database engine (uses global if not provided)
        """
        resolved_engine = engine or _engine

        if resolved_engine is None:
            raise RuntimeError("Database not initialized")

        self.engine: AsyncEngine = resolved_engine

    async def check_connection(self) -> bool:
        """
        Check database connection.

        Returns:
            True if connected
        """
        try:
            async with self.engine.begin() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error("database_connection_check_failed", error=str(e))
            return False

    async def get_pool_status(self) -> dict[str, Any]:
        """
        Get connection pool status.

        Returns:
            Pool status information
        """
        pool = self.engine.pool

        return {
            "size": pool.size() if hasattr(pool, "size") else None,
            "checked_in": pool.checkedin() if hasattr(pool, "checkedin") else None,
            "checked_out": pool.checkedout() if hasattr(pool, "checkedout") else None,
            "overflow": pool.overflow() if hasattr(pool, "overflow") else None,
            "total": pool.total() if hasattr(pool, "total") else None,
        }

    async def get_table_sizes(self) -> dict[str, Any]:
        """
        Get table sizes.

        Returns:
            Dictionary of table sizes
        """
        query = """
        SELECT 
            schemaname,
            tablename,
            pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size,
            pg_total_relation_size(schemaname||'.'||tablename) AS size_bytes
        FROM pg_tables
        WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
        """

        async with self.engine.begin() as conn:
            result = await conn.execute(text(query))
            rows = result.fetchall()

        return {
            row.tablename: {"size": row.size, "size_bytes": row.size_bytes}
            for row in rows
        }

    async def get_slow_queries(self, min_duration_ms: int = 1000) -> list[dict[str, Any]]:
        """
        Get slow queries.

        Args:
            min_duration_ms: Minimum duration in milliseconds

        Returns:
            List of slow queries
        """
        query = f"""
        SELECT 
            query,
            calls,
            total_time,
            mean_time,
            max_time,
            min_time
        FROM pg_stat_statements
        WHERE mean_time > {min_duration_ms}
        ORDER BY mean_time DESC
        LIMIT 20
        """

        try:
            async with self.engine.begin() as conn:
                result = await conn.execute(text(query))
                rows = result.fetchall()

            return [
                {
                    "query": row.query[:200],  # Truncate long queries
                    "calls": row.calls,
                    "total_time": row.total_time,
                    "mean_time": row.mean_time,
                    "max_time": row.max_time,
                    "min_time": row.min_time,
                }
                for row in rows
            ]
        except Exception as e:
            logger.warning("slow_queries_unavailable", error=str(e))
            return []

    async def analyze_indexes(self) -> dict[str, Any]:
        """
        Analyze index usage.

        Returns:
            Index analysis results
        """
        # Get unused indexes
        unused_query = """
        SELECT
            schemaname,
            tablename,
            indexname,
            idx_scan,
            idx_tup_read,
            idx_tup_fetch,
            pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
        FROM pg_stat_user_indexes
        WHERE idx_scan = 0
        AND indexrelname NOT LIKE 'pg_toast%'
        ORDER BY pg_relation_size(indexrelid) DESC
        """

        # Get index hit rate
        hit_rate_query = """
        SELECT 
            sum(idx_blks_hit) / NULLIF(sum(idx_blks_hit + idx_blks_read), 0)::float AS index_hit_rate
        FROM pg_statio_user_indexes
        """

        async with self.engine.begin() as conn:
            # Get unused indexes
            result = await conn.execute(text(unused_query))
            unused = [
                {"table": row.tablename, "index": row.indexname, "size": row.index_size}
                for row in result.fetchall()
            ]

            # Get hit rate
            result = await conn.execute(text(hit_rate_query))
            hit_rate = result.scalar()

        return {"unused_indexes": unused, "index_hit_rate": hit_rate}

    async def vacuum_analyze(self, table_name: str | None = None) -> None:
        """
        Run VACUUM ANALYZE.

        Args:
            table_name: Specific table or None for all
        """
        query = f"VACUUM ANALYZE {table_name}" if table_name else "VACUUM ANALYZE"

        async with self.engine.begin() as conn:
            await conn.execute(text(query))

        logger.info("vacuum_analyze_completed", table_name=table_name or "all tables")


# Convenience functions
async def get_db_manager() -> DatabaseManager:
    """Get database manager instance."""
    return DatabaseManager()


__all__ = [
    "DatabaseManager",
    "close_db",
    "execute_in_transaction",
    "get_db_manager",
    "get_session",
    "get_session_factory",
    "get_transaction",
    "init_db",
]
