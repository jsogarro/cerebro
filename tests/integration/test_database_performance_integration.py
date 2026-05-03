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
            select(ResearchProject).where(ResearchProject.id == users[0].id)
        )
        indexed_time = time.time() - start

        start = time.time()
        await db_session.execute(
            select(ResearchProject).where(ResearchProject.description.like("%test%"))
        )
        non_indexed_time = time.time() - start

        assert indexed_time < 1
        assert non_indexed_time < 2

    @pytest.mark.asyncio
    async def test_connection_pooling(self, db_session: AsyncSession) -> None:
        """Test database connection pooling."""
        import asyncio

        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import QueuePool

        engine = create_async_engine(
            "postgresql+asyncpg://test:test@localhost/test",
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
            pool_recycle=3600,
        )

        async def query_db() -> object:
            async with engine.begin() as conn:
                result = await conn.execute(select(func.now()))
                return result.scalar()

        tasks = [query_db() for _ in range(20)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 20

        await engine.dispose()


class TestDatabaseMigrations:
    """Test database migration scenarios."""

    @pytest.mark.asyncio
    async def test_schema_evolution(self, db_session: AsyncSession) -> None:
        """Test handling of schema changes."""
        from sqlalchemy import inspect

        inspector = inspect(db_session.bind)

        tables = inspector.get_table_names()
        assert "users" in tables
        assert "research_projects" in tables
        assert "research_results" in tables

        user_columns = [col["name"] for col in inspector.get_columns("users")]
        assert "id" in user_columns
        assert "email" in user_columns
        assert "created_at" in user_columns

        inspector.get_indexes("users")

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
