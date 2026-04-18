"""
Database utilities for integration testing.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src.models.base import Base
from src.models.db.research_project import ResearchProject
from src.models.db.research_result import ResearchResult
from src.models.db.user import User

logger = logging.getLogger(__name__)


class TestDatabaseManager:
    """Manage test database operations."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.engine: AsyncEngine | None = None
        self.session_factory: async_sessionmaker | None = None

    async def initialize(self):
        """Initialize database connection."""
        # Use NullPool for testing to avoid connection issues
        self.engine = create_async_engine(
            self.database_url,
            echo=False,
            poolclass=NullPool,
        )

        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        logger.info("Database initialized")

    async def create_tables(self):
        """Create all database tables."""
        if not self.engine:
            raise RuntimeError("Database not initialized")

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("Database tables created")

    async def drop_tables(self):
        """Drop all database tables."""
        if not self.engine:
            raise RuntimeError("Database not initialized")

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

        logger.info("Database tables dropped")

    async def truncate_tables(self):
        """Truncate all tables (faster than drop/create for tests)."""
        if not self.engine:
            raise RuntimeError("Database not initialized")

        async with self.engine.begin() as conn:
            # Get all table names
            inspector = inspect(self.engine.sync_engine)
            tables = inspector.get_table_names()

            # Disable foreign key checks temporarily
            await conn.execute(text("SET CONSTRAINTS ALL DEFERRED"))

            # Truncate each table
            for table in tables:
                if table != "alembic_version":  # Skip migration table
                    await conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))

            # Re-enable foreign key checks
            await conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

        logger.info("Database tables truncated")

    async def seed_data(self, data: dict[str, list[Any]]):
        """Seed database with test data."""
        if not self.session_factory:
            raise RuntimeError("Database not initialized")

        async with self.session_factory() as session:
            for _model_name, records in data.items():
                for record in records:
                    session.add(record)

            await session.commit()

        logger.info(
            f"Seeded database with {sum(len(r) for r in data.values())} records"
        )

    async def execute_sql(self, sql: str, params: dict | None = None):
        """Execute raw SQL query."""
        if not self.engine:
            raise RuntimeError("Database not initialized")

        async with self.engine.begin() as conn:
            result = await conn.execute(text(sql), params or {})
            return result.fetchall()

    async def get_session(self) -> AsyncSession:
        """Get a database session."""
        if not self.session_factory:
            raise RuntimeError("Database not initialized")

        return self.session_factory()

    async def cleanup(self):
        """Clean up database connections."""
        if self.engine:
            await self.engine.dispose()
            logger.info("Database connections closed")

    @asynccontextmanager
    async def transaction(self):
        """Context manager for database transaction."""
        async with self.session_factory() as session, session.begin():
            yield session


class TestDataSeeder:
    """Seed test data into database."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def seed_users(self, count: int = 5) -> list[User]:
        """Seed test users."""
        from tests.factories.user_factory import UserFactory

        users = []
        for i in range(count):
            user = UserFactory(email=f"user{i}@test.com", username=f"testuser{i}")
            self.session.add(user)
            users.append(user)

        await self.session.commit()
        return users

    async def seed_projects(
        self, users: list[User], projects_per_user: int = 3
    ) -> list[ResearchProject]:
        """Seed test research projects."""
        from tests.factories.project_factory import ResearchProjectFactory

        projects = []
        for user in users:
            for i in range(projects_per_user):
                project = ResearchProjectFactory(
                    user_id=user.id, title=f"{user.username} Project {i+1}"
                )
                self.session.add(project)
                projects.append(project)

        await self.session.commit()
        return projects

    async def seed_results(
        self, projects: list[ResearchProject]
    ) -> list[ResearchResult]:
        """Seed test research results."""
        from tests.factories.project_factory import ResearchResultFactory

        results = []
        agents = [
            "literature_review",
            "comparative_analysis",
            "methodology",
            "synthesis",
            "citation",
        ]

        for project in projects:
            if project.status == "completed":
                for agent in agents:
                    result = ResearchResultFactory(
                        project_id=project.id, agent_name=agent
                    )
                    self.session.add(result)
                    results.append(result)

        await self.session.commit()
        return results

    async def seed_complete_dataset(self) -> dict[str, Any]:
        """Seed a complete test dataset."""
        users = await self.seed_users(5)
        projects = await self.seed_projects(users, 3)
        results = await self.seed_results(projects)

        return {
            "users": users,
            "projects": projects,
            "results": results,
        }


class DatabaseAssertion:
    """Database assertion helpers for testing."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def assert_record_exists(self, model: Any, **filters) -> Any:
        """Assert that a record exists in the database."""
        from sqlalchemy import select

        stmt = select(model).filter_by(**filters)
        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()

        if not record:
            raise AssertionError(
                f"Record not found in {model.__name__} with filters: {filters}"
            )

        return record

    async def assert_record_count(self, model: Any, expected_count: int, **filters):
        """Assert the count of records in the database."""
        from sqlalchemy import func, select

        stmt = select(func.count()).select_from(model)
        if filters:
            stmt = stmt.filter_by(**filters)

        result = await self.session.execute(stmt)
        actual_count = result.scalar()

        if actual_count != expected_count:
            raise AssertionError(
                f"Expected {expected_count} records in {model.__name__}, "
                f"but found {actual_count}"
            )

    async def assert_field_value(
        self, model: Any, record_id: str, field_name: str, expected_value: Any
    ):
        """Assert a field value for a record."""
        from sqlalchemy import select

        stmt = select(model).filter_by(id=record_id)
        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()

        if not record:
            raise AssertionError(f"Record with id {record_id} not found")

        actual_value = getattr(record, field_name)
        if actual_value != expected_value:
            raise AssertionError(
                f"Expected {field_name}={expected_value}, "
                f"but got {field_name}={actual_value}"
            )

    async def assert_relationship_exists(
        self,
        parent_model: Any,
        parent_id: str,
        relationship_name: str,
        expected_count: int | None = None,
    ):
        """Assert that a relationship exists and optionally check count."""
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        stmt = (
            select(parent_model)
            .filter_by(id=parent_id)
            .options(selectinload(getattr(parent_model, relationship_name)))
        )
        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()

        if not record:
            raise AssertionError(f"Parent record with id {parent_id} not found")

        relationship = getattr(record, relationship_name)

        if relationship is None:
            raise AssertionError(f"Relationship {relationship_name} is None")

        if expected_count is not None:
            actual_count = len(relationship) if hasattr(relationship, "__len__") else 1
            if actual_count != expected_count:
                raise AssertionError(
                    f"Expected {expected_count} related records, "
                    f"but found {actual_count}"
                )


@asynccontextmanager
async def test_database(database_url: str):
    """Context manager for test database."""
    manager = TestDatabaseManager(database_url)

    try:
        await manager.initialize()
        await manager.create_tables()
        yield manager
    finally:
        await manager.drop_tables()
        await manager.cleanup()


class DatabaseSnapshot:
    """Create and restore database snapshots for testing."""

    def __init__(self, engine: AsyncEngine):
        self.engine = engine
        self.snapshots = {}

    async def create_snapshot(self, name: str):
        """Create a named snapshot of the current database state."""
        snapshot_data = {}

        async with self.engine.begin() as conn:
            # Get all tables
            inspector = inspect(self.engine.sync_engine)
            tables = inspector.get_table_names()

            for table in tables:
                if table != "alembic_version":
                    result = await conn.execute(text(f"SELECT * FROM {table}"))
                    snapshot_data[table] = result.fetchall()

        self.snapshots[name] = snapshot_data
        logger.info(f"Created database snapshot: {name}")

    async def restore_snapshot(self, name: str):
        """Restore a named database snapshot."""
        if name not in self.snapshots:
            raise ValueError(f"Snapshot {name} not found")

        snapshot_data = self.snapshots[name]

        async with self.engine.begin() as conn:
            # Disable foreign key checks
            await conn.execute(text("SET CONSTRAINTS ALL DEFERRED"))

            # Clear all tables
            inspector = inspect(self.engine.sync_engine)
            tables = inspector.get_table_names()

            for table in tables:
                if table != "alembic_version":
                    await conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))

            # Restore data
            for table, rows in snapshot_data.items():
                if rows:
                    # Build insert statement
                    columns = rows[0].keys()
                    placeholders = ", ".join(f":{col}" for col in columns)
                    insert_sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"

                    for row in rows:
                        await conn.execute(text(insert_sql), dict(row))

            # Re-enable foreign key checks
            await conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

        logger.info(f"Restored database snapshot: {name}")
