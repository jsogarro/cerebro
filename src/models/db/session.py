"""
Database session management.

Provides utilities for managing database connections and sessions.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, QueuePool

from src.core.config import get_settings

logger = logging.getLogger(__name__)

# Global engine and session factory
_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker | None = None


def get_database_url() -> str:
    """
    Get database URL from settings.

    Returns:
        Database URL
    """
    settings = get_settings()
    return settings.DATABASE_URL


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

    # Create engine
    engine = create_async_engine(
        database_url,
        echo=echo,
        echo_pool=echo_pool,
        poolclass=poolclass,
        pool_size=pool_size if not use_null_pool else None,
        max_overflow=max_overflow if not use_null_pool else None,
        pool_timeout=pool_timeout if not use_null_pool else None,
        pool_pre_ping=True,  # Verify connections before using
        pool_recycle=3600,  # Recycle connections after 1 hour
    )

    # Add event listeners for debugging
    if echo_pool:

        @event.listens_for(engine.sync_engine, "connect")
        def receive_connect(dbapi_conn, connection_record):
            logger.debug(f"Pool connect: {connection_record}")

        @event.listens_for(engine.sync_engine, "checkout")
        def receive_checkout(dbapi_conn, connection_record, connection_proxy):
            logger.debug(f"Pool checkout: {connection_record}")

    return engine


async def init_db(database_url: str | None = None, **engine_kwargs) -> None:
    """
    Initialize database connection.

    Args:
        database_url: Database URL
        **engine_kwargs: Additional engine arguments
    """
    global _engine, _async_session_factory

    if _engine is not None:
        logger.warning("Database already initialized")
        return

    logger.info("Initializing database connection")

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
            await conn.execute("SELECT 1")
        logger.info("Database connection successful")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        await close_db()
        raise


async def close_db() -> None:
    """Close database connections."""
    global _engine, _async_session_factory

    if _engine is not None:
        logger.info("Closing database connections")
        await _engine.dispose()
        _engine = None
        _async_session_factory = None


def get_session_factory() -> async_sessionmaker:
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

    async with session_factory() as session:
        async with session.begin():
            try:
                yield session
            except Exception:
                await session.rollback()
                raise


async def execute_in_transaction(func, *args, **kwargs) -> Any:
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
        self.engine = engine or _engine

        if self.engine is None:
            raise RuntimeError("Database not initialized")

    async def check_connection(self) -> bool:
        """
        Check database connection.

        Returns:
            True if connected
        """
        try:
            async with self.engine.begin() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"Database connection check failed: {e}")
            return False

    async def get_pool_status(self) -> dict:
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

    async def get_table_sizes(self) -> dict:
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
            result = await conn.execute(query)
            rows = result.fetchall()

        return {
            row.tablename: {"size": row.size, "size_bytes": row.size_bytes}
            for row in rows
        }

    async def get_slow_queries(self, min_duration_ms: int = 1000) -> list:
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
                result = await conn.execute(query)
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
            logger.warning(
                f"Could not get slow queries (pg_stat_statements may not be enabled): {e}"
            )
            return []

    async def analyze_indexes(self) -> dict:
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
            result = await conn.execute(unused_query)
            unused = [
                {"table": row.tablename, "index": row.indexname, "size": row.index_size}
                for row in result.fetchall()
            ]

            # Get hit rate
            result = await conn.execute(hit_rate_query)
            hit_rate = result.scalar()

        return {"unused_indexes": unused, "index_hit_rate": hit_rate}

    async def vacuum_analyze(self, table_name: str | None = None) -> None:
        """
        Run VACUUM ANALYZE.

        Args:
            table_name: Specific table or None for all
        """
        if table_name:
            query = f"VACUUM ANALYZE {table_name}"
        else:
            query = "VACUUM ANALYZE"

        async with self.engine.begin() as conn:
            await conn.execute(query)

        logger.info(f"VACUUM ANALYZE completed for {table_name or 'all tables'}")


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
