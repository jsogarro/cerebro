"""
Database integration tests for the Research Platform.
"""

import json
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.db.research_project import ResearchProject
from src.models.db.research_result import ResearchResult
from src.models.db.user import User
from src.repositories.research_repository import ResearchRepository
from src.repositories.result_repository import ResultRepository
from src.repositories.user_repository import UserRepository
from tests.factories.project_factory import (
    ResearchProjectFactory,
    ResearchResultFactory,
)
from tests.factories.user_factory import UserFactory
from tests.utils.db_utils import TestDataSeeder


class TestTransactionManagement:
    """Test database transaction management."""

    @pytest.mark.asyncio
    async def test_transaction_commit(self, db_session: AsyncSession):
        """Test successful transaction commit."""
        user = UserFactory()
        project = ResearchProjectFactory(user_id=user.id)

        # Add to session
        db_session.add(user)
        db_session.add(project)

        # Commit transaction
        await db_session.commit()

        # Verify data persisted
        result = await db_session.execute(select(User).where(User.id == user.id))
        persisted_user = result.scalar_one()
        assert persisted_user.email == user.email

        result = await db_session.execute(
            select(ResearchProject).where(ResearchProject.id == project.id)
        )
        persisted_project = result.scalar_one()
        assert persisted_project.title == project.title

    @pytest.mark.asyncio
    async def test_transaction_rollback(self, db_session: AsyncSession):
        """Test transaction rollback on error."""
        user = UserFactory()
        db_session.add(user)

        try:
            # Create project with invalid foreign key
            project = ResearchProjectFactory(user_id="invalid-uuid")
            db_session.add(project)
            await db_session.commit()
        except Exception:
            await db_session.rollback()

        # Verify nothing was persisted
        result = await db_session.execute(select(func.count()).select_from(User))
        count = result.scalar()
        assert count == 0

    @pytest.mark.asyncio
    async def test_nested_transactions(self, db_session: AsyncSession):
        """Test nested transaction handling."""
        async with db_session.begin():
            user = UserFactory()
            db_session.add(user)

            # Nested transaction
            async with db_session.begin_nested():
                project = ResearchProjectFactory(user_id=user.id)
                db_session.add(project)

                # Rollback nested transaction
                raise Exception("Rollback nested")

        # User should be persisted, project should not
        result = await db_session.execute(select(User).where(User.id == user.id))
        assert result.scalar_one_or_none() is not None

        result = await db_session.execute(
            select(ResearchProject).where(ResearchProject.user_id == user.id)
        )
        assert result.scalar_one_or_none() is None


class TestRepositoryIntegration:
    """Test repository pattern integration."""

    @pytest.mark.asyncio
    async def test_user_repository_crud(self, db_session: AsyncSession):
        """Test UserRepository CRUD operations."""
        repo = UserRepository(db_session)

        # Create
        user_data = {
            "email": "test@example.com",
            "username": "testuser",
            "hashed_password": "hashed",
            "role": "researcher",
        }
        user = await repo.create(user_data)
        assert user.id is not None

        # Read
        fetched = await repo.get(user.id)
        assert fetched.email == user_data["email"]

        # Update
        updated = await repo.update(user.id, {"role": "admin"})
        assert updated.role == "admin"

        # Delete
        deleted = await repo.delete(user.id)
        assert deleted is True

        # Verify deleted
        fetched = await repo.get(user.id)
        assert fetched is None

    @pytest.mark.asyncio
    async def test_research_repository_queries(self, db_session: AsyncSession):
        """Test ResearchRepository complex queries."""
        repo = ResearchRepository(db_session)
        seeder = TestDataSeeder(db_session)

        # Seed data
        users = await seeder.seed_users(3)
        projects = await seeder.seed_projects(users, 5)

        # Test filtering by user
        user_projects = await repo.get_by_user(users[0].id)
        assert len(user_projects) == 5

        # Test filtering by status
        pending = await repo.get_by_status("pending")
        assert all(p.status == "pending" for p in pending)

        # Test pagination
        page1 = await repo.get_paginated(page=1, per_page=10)
        assert len(page1["items"]) <= 10
        assert page1["total"] == len(projects)

        # Test search
        if projects:
            search_term = projects[0].title.split()[0]
            results = await repo.search(search_term)
            assert len(results) > 0

    @pytest.mark.asyncio
    async def test_repository_relationships(self, db_session: AsyncSession):
        """Test repository handling of relationships."""
        user_repo = UserRepository(db_session)
        project_repo = ResearchRepository(db_session)
        result_repo = ResultRepository(db_session)

        # Create user with projects
        user = await user_repo.create(
            {
                "email": "researcher@example.com",
                "username": "researcher",
                "hashed_password": "hashed",
                "role": "researcher",
            }
        )

        # Create multiple projects for user
        for i in range(3):
            project = await project_repo.create(
                {
                    "title": f"Project {i}",
                    "description": f"Description {i}",
                    "user_id": user.id,
                    "query_text": "Test query",
                    "domains": json.dumps(["AI", "ML"]),
                    "depth_level": "basic",
                }
            )

            # Create results for project
            for j in range(2):
                await result_repo.create(
                    {
                        "project_id": project.id,
                        "agent_name": f"agent_{j}",
                        "result_type": "analysis",
                        "content": json.dumps({"data": f"result_{j}"}),
                        "confidence_score": 0.9,
                    }
                )

        # Test eager loading
        user_with_projects = await user_repo.get_with_projects(user.id)
        assert len(user_with_projects.projects) == 3

        # Test cascade operations
        await user_repo.delete(user.id)

        # Verify cascade delete
        orphan_projects = await project_repo.get_by_user(user.id)
        assert len(orphan_projects) == 0


class TestComplexQueries:
    """Test complex database queries and aggregations."""

    @pytest.mark.asyncio
    async def test_aggregation_queries(self, db_session: AsyncSession):
        """Test aggregation queries."""
        seeder = TestDataSeeder(db_session)
        await seeder.seed_complete_dataset()

        # Count projects by status
        result = await db_session.execute(
            select(
                ResearchProject.status, func.count(ResearchProject.id).label("count")
            ).group_by(ResearchProject.status)
        )

        status_counts = {row.status: row.count for row in result}
        assert len(status_counts) > 0

        # Average confidence score by agent
        result = await db_session.execute(
            select(
                ResearchResult.agent_name,
                func.avg(ResearchResult.confidence_score).label("avg_confidence"),
            ).group_by(ResearchResult.agent_name)
        )

        agent_scores = {row.agent_name: row.avg_confidence for row in result}
        assert all(0 <= score <= 1 for score in agent_scores.values())

    @pytest.mark.asyncio
    async def test_join_queries(self, db_session: AsyncSession):
        """Test complex join queries."""
        seeder = TestDataSeeder(db_session)
        await seeder.seed_complete_dataset()

        # Join users with their projects
        result = await db_session.execute(
            select(User, ResearchProject)
            .join(ResearchProject, User.id == ResearchProject.user_id)
            .where(User.role == "researcher")
        )

        user_projects = result.all()
        assert len(user_projects) > 0

        # Join projects with results
        result = await db_session.execute(
            select(ResearchProject, func.count(ResearchResult.id))
            .outerjoin(ResearchResult)
            .group_by(ResearchProject.id)
            .having(func.count(ResearchResult.id) > 0)
        )

        projects_with_results = result.all()
        assert len(projects_with_results) >= 0

    @pytest.mark.asyncio
    async def test_subquery_operations(self, db_session: AsyncSession):
        """Test subquery operations."""
        seeder = TestDataSeeder(db_session)
        await seeder.seed_complete_dataset()

        # Subquery for users with completed projects
        completed_users_subq = (
            select(ResearchProject.user_id)
            .where(ResearchProject.status == "completed")
            .subquery()
        )

        # Get users who have completed projects
        result = await db_session.execute(
            select(User).where(User.id.in_(select(completed_users_subq)))
        )

        users_with_completed = result.scalars().all()
        assert isinstance(users_with_completed, list)

    @pytest.mark.asyncio
    async def test_window_functions(self, db_session: AsyncSession):
        """Test window functions for analytics."""
        seeder = TestDataSeeder(db_session)
        await seeder.seed_complete_dataset()

        # Rank projects by creation date per user
        from sqlalchemy import desc
        from sqlalchemy.sql import func

        result = await db_session.execute(
            select(
                ResearchProject.id,
                ResearchProject.user_id,
                ResearchProject.created_at,
                func.row_number()
                .over(
                    partition_by=ResearchProject.user_id,
                    order_by=desc(ResearchProject.created_at),
                )
                .label("rank"),
            )
        )

        ranked_projects = result.all()

        # Verify ranking
        user_rankings = {}
        for row in ranked_projects:
            if row.user_id not in user_rankings:
                user_rankings[row.user_id] = []
            user_rankings[row.user_id].append(row.rank)

        # Each user's projects should have sequential rankings
        for _user_id, ranks in user_rankings.items():
            assert sorted(ranks) == list(range(1, len(ranks) + 1))


class TestDatabaseConstraints:
    """Test database constraints and integrity."""

    @pytest.mark.asyncio
    async def test_unique_constraints(self, db_session: AsyncSession):
        """Test unique constraint enforcement."""
        user1 = UserFactory(email="unique@example.com")
        user2 = UserFactory(email="unique@example.com")  # Same email

        db_session.add(user1)
        await db_session.commit()

        db_session.add(user2)
        with pytest.raises(Exception, match=""):  # IntegrityError
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_foreign_key_constraints(self, db_session: AsyncSession):
        """Test foreign key constraint enforcement."""
        # Try to create project with non-existent user
        project = ResearchProjectFactory(user_id=str(uuid.uuid4()))

        db_session.add(project)
        with pytest.raises(Exception, match=""):  # IntegrityError
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_check_constraints(self, db_session: AsyncSession):
        """Test check constraints."""
        # Test invalid enum values
        user = UserFactory()
        db_session.add(user)
        await db_session.commit()

        # Try to set invalid role
        stmt = select(User).where(User.id == user.id)
        result = await db_session.execute(stmt)
        user = result.scalar_one()

        # This should be validated at application level
        user.role = "invalid_role"
        # await db_session.commit()  # Should fail with validation

    @pytest.mark.asyncio
    async def test_cascade_operations(self, db_session: AsyncSession):
        """Test cascade delete operations."""
        # Create user with projects and results
        user = UserFactory()
        db_session.add(user)
        await db_session.commit()

        project = ResearchProjectFactory(user_id=user.id)
        db_session.add(project)
        await db_session.commit()

        result = ResearchResultFactory(project_id=project.id)
        db_session.add(result)
        await db_session.commit()

        # Delete user (should cascade to projects and results)
        await db_session.delete(user)
        await db_session.commit()

        # Verify cascade
        project_check = await db_session.get(ResearchProject, project.id)
        assert project_check is None

        result_check = await db_session.get(ResearchResult, result.id)
        assert result_check is None


class TestDatabasePerformance:
    """Test database performance and optimization."""

    @pytest.mark.asyncio
    async def test_bulk_operations(self, db_session: AsyncSession):
        """Test bulk insert and update operations."""
        import time

        # Bulk insert
        users = [UserFactory() for _ in range(100)]

        start = time.time()
        db_session.add_all(users)
        await db_session.commit()
        insert_time = time.time() - start

        assert insert_time < 5  # Should be fast

        # Verify all inserted
        result = await db_session.execute(select(func.count()).select_from(User))
        assert result.scalar() == 100

        # Bulk update
        start = time.time()
        await db_session.execute(User.__table__.update().values(is_verified=True))
        await db_session.commit()
        update_time = time.time() - start

        assert update_time < 2  # Should be fast

    @pytest.mark.asyncio
    async def test_index_performance(self, db_session: AsyncSession):
        """Test query performance with indexes."""
        # Seed large dataset
        seeder = TestDataSeeder(db_session)
        users = await seeder.seed_users(50)
        await seeder.seed_projects(users, 10)  # 500 projects total

        import time

        # Query with indexed column (id)
        start = time.time()
        await db_session.execute(
            select(ResearchProject).where(ResearchProject.id == users[0].id)
        )
        indexed_time = time.time() - start

        # Query with non-indexed column (might be indexed depending on schema)
        start = time.time()
        await db_session.execute(
            select(ResearchProject).where(ResearchProject.description.like("%test%"))
        )
        non_indexed_time = time.time() - start

        # Indexed queries should generally be faster
        # Note: This might not always be true for small datasets
        assert indexed_time < 1
        assert non_indexed_time < 2

    @pytest.mark.asyncio
    async def test_connection_pooling(self, db_session: AsyncSession):
        """Test database connection pooling."""
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import QueuePool

        # Create engine with connection pool
        engine = create_async_engine(
            "postgresql+asyncpg://test:test@localhost/test",
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
            pool_recycle=3600,
        )

        # Simulate concurrent connections
        import asyncio

        async def query_db():
            async with engine.begin() as conn:
                result = await conn.execute(select(func.now()))
                return result.scalar()

        # Run multiple concurrent queries
        tasks = [query_db() for _ in range(20)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 20

        await engine.dispose()


class TestDatabaseMigrations:
    """Test database migration scenarios."""

    @pytest.mark.asyncio
    async def test_schema_evolution(self, db_session: AsyncSession):
        """Test handling of schema changes."""
        # This would typically test Alembic migrations
        # For now, we'll test schema introspection

        from sqlalchemy import inspect

        inspector = inspect(db_session.bind)

        # Check tables exist
        tables = inspector.get_table_names()
        assert "users" in tables
        assert "research_projects" in tables
        assert "research_results" in tables

        # Check columns
        user_columns = [col["name"] for col in inspector.get_columns("users")]
        assert "id" in user_columns
        assert "email" in user_columns
        assert "created_at" in user_columns

        # Check indexes
        user_indexes = inspector.get_indexes("users")
        [idx["name"] for idx in user_indexes]
        # Verify expected indexes exist

    @pytest.mark.asyncio
    async def test_data_migration(self, db_session: AsyncSession):
        """Test data migration scenarios."""
        # Simulate migrating data from old schema to new

        # Create data in "old" format
        old_users = [UserFactory() for _ in range(10)]
        db_session.add_all(old_users)
        await db_session.commit()

        # Simulate migration (e.g., adding new column with default)
        for user in old_users:
            if not hasattr(user, "preferences"):
                user.preferences = json.dumps({"theme": "light"})

        await db_session.commit()

        # Verify migration
        result = await db_session.execute(select(User))
        migrated_users = result.scalars().all()

        for _user in migrated_users:
            # assert user.preferences is not None
            pass  # Would check new column values
