"""
Enhanced test configuration for integration tests.

This module provides comprehensive fixtures and utilities for integration testing
with real PostgreSQL, Redis, and Temporal instances using Docker containers.
"""

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
import redis.asyncio as redis
from faker import Faker
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from testcontainers.compose import DockerCompose
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from src.api.main import app
from src.auth.jwt_service import JWTService
from src.auth.password_service import PasswordService
from src.core.config import Settings
from src.models.base import Base
from src.models.db.research_project import ResearchProject
from src.models.db.user import User

# Initialize Faker for test data generation
fake = Faker()


class IntegrationTestConfig:
    """Configuration for integration tests."""

    # Test database configuration
    TEST_DB_USER = "test_user"
    TEST_DB_PASSWORD = "test_password"
    TEST_DB_NAME = "test_research_db"

    # Test Redis configuration
    TEST_REDIS_DB = 15

    # Test user roles
    TEST_ROLES = ["admin", "researcher", "viewer"]

    # Test timeouts
    DEFAULT_TIMEOUT = 30  # seconds
    WORKFLOW_TIMEOUT = 60  # seconds


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def docker_compose_file():
    """Path to docker-compose file for integration tests."""
    return os.path.join(os.path.dirname(__file__), "docker-compose.test.yml")


@pytest.fixture(scope="session")
def docker_compose(docker_compose_file):
    """Start Docker Compose services for integration tests."""
    compose = DockerCompose(
        filepath=os.path.dirname(docker_compose_file),
        compose_file_name=os.path.basename(docker_compose_file),
        pull=True,
    )
    compose.start()
    compose.wait_for("postgres")
    compose.wait_for("redis")
    yield compose
    compose.stop()


@pytest_asyncio.fixture(scope="session")
async def postgres_container():
    """Start PostgreSQL container for integration tests."""
    container = PostgresContainer(
        image="postgres:16-alpine",
        user=IntegrationTestConfig.TEST_DB_USER,
        password=IntegrationTestConfig.TEST_DB_PASSWORD,
        dbname=IntegrationTestConfig.TEST_DB_NAME,
    )
    container.start()
    yield container
    container.stop()


@pytest_asyncio.fixture(scope="session")
async def redis_container():
    """Start Redis container for integration tests."""
    container = RedisContainer(image="redis:7-alpine")
    container.start()
    yield container
    container.stop()


@pytest_asyncio.fixture
async def test_engine(postgres_container) -> AsyncEngine:
    """Create test database engine."""
    connection_url = postgres_container.get_connection_url()
    # Convert to async URL
    async_url = connection_url.replace("postgresql://", "postgresql+asyncpg://")

    engine = create_async_engine(
        async_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # Drop all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    async_session = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def redis_client(redis_container) -> AsyncGenerator:
    """Create Redis client for testing."""
    client = await redis.from_url(
        redis_container.get_connection_url(),
        decode_responses=True,
        db=IntegrationTestConfig.TEST_REDIS_DB,
    )
    yield client
    await client.flushdb()
    await client.close()


@pytest_asyncio.fixture
async def authenticated_client(
    db_session: AsyncSession,
) -> AsyncGenerator[AsyncClient, None]:
    """Create authenticated HTTP client for testing."""
    # Create test user
    password_service = PasswordService()
    jwt_service = JWTService()

    test_user = User(
        id=str(uuid.uuid4()),
        email="test@example.com",
        username="testuser",
        hashed_password=password_service.hash_password("Test123!@#"),
        is_active=True,
        is_verified=True,
        role="researcher",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    db_session.add(test_user)
    await db_session.commit()

    # Generate JWT token
    token = jwt_service.create_access_token(
        data={"sub": test_user.id, "email": test_user.email, "role": test_user.role}
    )

    # Create client with auth header
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


@pytest_asyncio.fixture
async def admin_client(
    db_session: AsyncSession,
) -> AsyncGenerator[AsyncClient, None]:
    """Create admin HTTP client for testing."""
    # Create admin user
    password_service = PasswordService()
    jwt_service = JWTService()

    admin_user = User(
        id=str(uuid.uuid4()),
        email="admin@example.com",
        username="admin",
        hashed_password=password_service.hash_password("Admin123!@#"),
        is_active=True,
        is_verified=True,
        role="admin",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    db_session.add(admin_user)
    await db_session.commit()

    # Generate JWT token
    token = jwt_service.create_access_token(
        data={"sub": admin_user.id, "email": admin_user.email, "role": admin_user.role}
    )

    # Create client with auth header
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        yield client


@pytest.fixture
def test_settings() -> Settings:
    """Create test settings."""
    return Settings(
        ENVIRONMENT="test",
        DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test_db",
        REDIS_URL="redis://localhost:6379/15",
        SECRET_KEY="test-secret-key-for-integration-tests",
        GEMINI_API_KEY="test-gemini-key",
        TEMPORAL_HOST="localhost:7233",
    )


@pytest.fixture
def sample_user_data() -> dict[str, Any]:
    """Generate sample user data."""
    return {
        "email": fake.email(),
        "username": fake.user_name(),
        "password": "SecurePass123!@#",
        "full_name": fake.name(),
        "role": fake.random_element(IntegrationTestConfig.TEST_ROLES),
    }


@pytest.fixture
def sample_project_data() -> dict[str, Any]:
    """Generate sample research project data."""
    return {
        "title": f"Research: {fake.catch_phrase()}",
        "description": fake.text(max_nb_chars=200),
        "query": {
            "text": fake.paragraph(nb_sentences=3),
            "domains": fake.random_elements(
                elements=["AI", "ML", "Ethics", "Biology", "Physics", "Economics"],
                length=3,
                unique=True,
            ),
            "depth_level": fake.random_element(
                ["basic", "intermediate", "comprehensive"]
            ),
        },
        "user_id": str(uuid.uuid4()),
        "status": "pending",
    }


@pytest.fixture
def sample_agent_response() -> dict[str, Any]:
    """Generate sample agent response data."""
    return {
        "agent_name": fake.random_element(
            [
                "literature_review",
                "comparative_analysis",
                "methodology",
                "synthesis",
                "citation",
            ]
        ),
        "status": "completed",
        "result": {
            "summary": fake.paragraph(nb_sentences=5),
            "findings": [fake.sentence() for _ in range(3)],
            "recommendations": [fake.sentence() for _ in range(2)],
            "confidence_score": fake.random.uniform(0.7, 1.0),
        },
        "execution_time": fake.random.uniform(1.0, 10.0),
        "metadata": {
            "sources_analyzed": fake.random_int(10, 100),
            "citations_found": fake.random_int(5, 50),
        },
    }


@pytest_asyncio.fixture
async def seed_test_data(db_session: AsyncSession):
    """Seed database with test data."""
    password_service = PasswordService()

    # Create test users
    users = []
    for i in range(5):
        user = User(
            id=str(uuid.uuid4()),
            email=f"user{i}@example.com",
            username=f"user{i}",
            hashed_password=password_service.hash_password("Password123!"),
            is_active=True,
            is_verified=i % 2 == 0,  # Half verified, half not
            role=IntegrationTestConfig.TEST_ROLES[i % 3],
            created_at=datetime.utcnow() - timedelta(days=i),
            updated_at=datetime.utcnow(),
        )
        users.append(user)
        db_session.add(user)

    # Create test projects
    projects = []
    for i in range(10):
        project = ResearchProject(
            id=str(uuid.uuid4()),
            title=f"Research Project {i}",
            description=f"Description for project {i}",
            user_id=users[i % 5].id,
            status=["pending", "in_progress", "completed"][i % 3],
            query_text=f"Research query {i}",
            domains=["AI", "ML", "Ethics"],
            depth_level="comprehensive",
            created_at=datetime.utcnow() - timedelta(days=i),
            updated_at=datetime.utcnow(),
        )
        projects.append(project)
        db_session.add(project)

    await db_session.commit()

    return {
        "users": users,
        "projects": projects,
    }


@pytest.fixture
def mock_gemini_service(mocker):
    """Mock Gemini service for integration tests."""
    mock = mocker.Mock()
    mock.generate_content.return_value = mocker.Mock(
        text=fake.paragraph(nb_sentences=10)
    )
    mock.is_available.return_value = True
    return mock


@pytest.fixture
def mock_mcp_client(mocker):
    """Mock MCP client for integration tests."""
    mock = mocker.Mock()
    mock.execute_tool.return_value = {
        "success": True,
        "result": fake.paragraph(nb_sentences=5),
        "metadata": {
            "tool": "academic_search",
            "execution_time": fake.random.uniform(0.1, 2.0),
        },
    }
    return mock


class TestDataFactory:
    """Factory for generating test data."""

    @staticmethod
    def create_user(**kwargs) -> User:
        """Create a test user."""
        password_service = PasswordService()
        defaults = {
            "id": str(uuid.uuid4()),
            "email": fake.email(),
            "username": fake.user_name(),
            "hashed_password": password_service.hash_password("Password123!"),
            "is_active": True,
            "is_verified": True,
            "role": "researcher",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        defaults.update(kwargs)
        return User(**defaults)

    @staticmethod
    def create_project(**kwargs) -> ResearchProject:
        """Create a test research project."""
        defaults = {
            "id": str(uuid.uuid4()),
            "title": f"Research: {fake.catch_phrase()}",
            "description": fake.text(max_nb_chars=200),
            "user_id": str(uuid.uuid4()),
            "status": "pending",
            "query_text": fake.paragraph(nb_sentences=3),
            "domains": ["AI", "ML", "Ethics"],
            "depth_level": "comprehensive",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        defaults.update(kwargs)
        return ResearchProject(**defaults)


@pytest.fixture
def test_data_factory():
    """Provide test data factory."""
    return TestDataFactory()


# Cleanup fixtures
@pytest_asyncio.fixture(autouse=True)
async def cleanup_database(db_session: AsyncSession):
    """Clean up database after each test."""
    yield
    # Rollback any pending transactions
    await db_session.rollback()


@pytest_asyncio.fixture(autouse=True)
async def cleanup_redis(redis_client):
    """Clean up Redis after each test."""
    yield
    # Clear all keys in test database
    await redis_client.flushdb()
