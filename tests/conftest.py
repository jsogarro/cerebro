"""
Pytest configuration and fixtures for Research Platform tests.
"""

import asyncio
import os
from collections.abc import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Set test environment
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-at-least-32-characters-long"


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create an instance of the default event loop for the test session."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP client for testing FastAPI endpoints."""
    from httpx import ASGITransport

    from src.api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    # Create test engine
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        future=True,
    )

    # Create session factory
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Create tables - import models to register them
    import src.models.db.agent_task
    import src.models.db.research_project
    import src.models.db.research_result
    import src.models.db.workflow_checkpoint  # noqa: F401
    from src.models.db.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Provide session
    async with async_session() as session:
        yield session

    # Cleanup
    await engine.dispose()


@pytest.fixture
def mock_gemini_client(mocker):
    """Mock Gemini API client for testing."""
    mock_client = mocker.Mock()
    mock_client.generate_content.return_value = mocker.Mock(
        text="Mocked Gemini response"
    )
    return mock_client


@pytest.fixture
def mock_temporal_client(mocker):
    """Mock Temporal client for testing."""
    mock_client = mocker.AsyncMock()
    mock_client.start_workflow.return_value = mocker.Mock(
        id="test-workflow-id",
        run_id="test-run-id",
    )
    return mock_client


@pytest.fixture
def sample_research_query():
    """Provide a sample research query for testing."""
    return {
        "text": "What are the implications of artificial general intelligence on society?",
        "domains": ["AI", "Ethics", "Sociology"],
        "depth_level": "comprehensive",
    }


@pytest.fixture
def sample_research_project():
    """Provide a sample research project for testing."""
    return {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "title": "AGI and Society Research",
        "user_id": "test-user-123",
        "status": "pending",
        "query": {
            "text": "What are the implications of artificial general intelligence on society?",
            "domains": ["AI", "Ethics", "Sociology"],
            "depth_level": "comprehensive",
        },
    }
