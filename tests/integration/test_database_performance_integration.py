"""
Database performance and migration integration tests.
"""

import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db.research_project import ResearchProject
from src.models.db.user import User
from tests.factories.user_factory import UserFactory
from tests.utils.db_utils import TestDataSeeder


class TestDatabasePerformance:
    """Test database performance and optimization."""

    @pytest.mark.asyncio
    async def test_bulk_operations(self, db_session: AsyncSession) -> None:
        """Test bulk insert and update operations."""
        import time

        users = [UserFactory() for _ in range(100)]

        start = time.time()
        db_session.add_all(users)
        await db_session.commit()
        insert_time = time.time() - start

        assert insert_time < 5

        result = await db_session.execute(select(func.count()).select_from(User))
        assert result.scalar() == 100

        start = time.time()
        await db_session.execute(User.__table__.update().values(is_verified=True))
        await db_session.commit()
        update_time = time.time() - start

        assert update_time < 2

    @pytest.mark.asyncio
    async def test_index_performance(self, db_session: AsyncSession) -> None:
        """Test query performance with indexes."""
        import time

        seeder = TestDataSeeder(db_session)
        users = await seeder.seed_users(50)
        await seeder.seed_projects(users, 10)

        start = time.time()
        await db_session.execute(
            select(ResearchProject).where(ResearchProject.user_id == str(users[0].id))
        )
        indexed_time = time.time() - start

        start = time.time()
        await db_session.execute(
            select(ResearchProject).where(ResearchProject.title.like("%test%"))
        )
        non_indexed_time = time.time() - start

        assert indexed_time < 1
        assert non_indexed_time < 2

    @pytest.mark.asyncio
    async def test_connection_pooling(self, test_engine) -> None:
        """Pool serves concurrent queries without deadlock and returns connections."""
        import asyncio

        from sqlalchemy import text

        # test_engine is configured with pool_size=10, max_overflow=20; 15
        # concurrent connections exercises both the base pool and the overflow.
        concurrency = 15

        async def _query_backend_pid() -> int:
            async with test_engine.connect() as conn:
                result = await conn.execute(text("SELECT pg_backend_pid()"))
                return int(result.scalar_one())

        pids = await asyncio.wait_for(
            asyncio.gather(*(_query_backend_pid() for _ in range(concurrency))),
            timeout=10,
        )

        assert len(pids) == concurrency
        # Concurrent connections must come from distinct backends — proves the
        # pool actually issued parallel connections rather than serializing.
        assert len(set(pids)) >= 2

        # All connections must be returned to the pool.
        assert test_engine.pool.checkedout() == 0


class TestDatabaseMigrations:
    """Test database migration scenarios."""

    @pytest.mark.asyncio
    async def test_schema_evolution(self, db_session: AsyncSession) -> None:
        """Test handling of schema changes."""
        from sqlalchemy import inspect

        def _introspect(sync_conn) -> dict[str, list[str]]:
            inspector = inspect(sync_conn)
            return {
                "tables": inspector.get_table_names(),
                "user_columns": [col["name"] for col in inspector.get_columns("users")],
            }

        conn = await db_session.connection()
        schema = await conn.run_sync(_introspect)

        assert "users" in schema["tables"]
        assert "research_projects" in schema["tables"]
        assert "research_results" in schema["tables"]

        assert "id" in schema["user_columns"]
        assert "email" in schema["user_columns"]
        assert "created_at" in schema["user_columns"]

    @pytest.mark.asyncio
    async def test_data_migration(self, db_session: AsyncSession) -> None:
        """Test data migration scenarios."""
        old_users = [UserFactory() for _ in range(10)]
        db_session.add_all(old_users)
        await db_session.commit()

        for user in old_users:
            if not hasattr(user, "preferences"):
                user.preferences = json.dumps({"theme": "light"})

        await db_session.commit()

        result = await db_session.execute(select(User))
        migrated_users = result.scalars().all()

        for _user in migrated_users:
            pass
