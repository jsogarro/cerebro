"""
Pytest configuration and fixtures for Research Platform tests.
"""

import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Set test environment
os.environ["CEREBRO_DISABLE_DOTENV"] = "true"
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-at-least-32-characters-long"
os.environ["ENABLE_RATE_LIMITING"] = "false"


class _SuccessfulDurableAuditLogger:
    """In-memory durable double for clients that bypass application lifespan."""

    def __init__(self) -> None:
        self.pending_events: list[dict[str, Any]] = []
        self.persisted_events: list[dict[str, Any]] = []

    async def log_event(self, **event: Any) -> str:
        """Buffer an event and return a deterministic test identifier."""
        self.pending_events.append(event)
        return (
            f"test-audit-event-{len(self.persisted_events) + len(self.pending_events)}"
        )

    async def flush_buffer(self) -> None:
        """Model a successful durable flush for the mounted middleware."""
        self.persisted_events.extend(self.pending_events)
        self.pending_events.clear()


@pytest.fixture(autouse=True)
def install_test_audit_logger() -> Generator[None, None, None]:
    """Provide audit state for raw ASGI clients without weakening production.

    FastAPI's lifespan installs the production logger, but many test clients
    intentionally bypass lifespan. Install a fresh successful double for each
    test so those clients exercise the real audit middleware. Tests covering
    missing or failing audit stores can replace or remove ``app.state`` during
    the test; the previous state is restored during fixture teardown.
    """
    from src.api.main import app

    missing = object()
    previous = getattr(app.state, "audit_logger", missing)
    app.state.audit_logger = _SuccessfulDurableAuditLogger()
    try:
        yield
    finally:
        if previous is missing:
            if hasattr(app.state, "audit_logger"):
                delattr(app.state, "audit_logger")
        else:
            app.state.audit_logger = previous


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create an instance of the default event loop for the test session."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP client for testing FastAPI endpoints.

    ASGITransport does not fire FastAPI's lifespan startup/shutdown hooks,
    so the production init_db() that normally runs on app boot never runs
    during tests. We explicitly initialize the test DB and create the
    schema here, mirroring the pattern in test_e2e_research_flow.py's
    `client` fixture but reusable across all conftest consumers.
    """
    import pathlib

    from httpx import ASGITransport

    from src.models.db import session as db_session_mod
    from src.models.db.base import Base
    from src.models.db.session import close_db, init_db

    # Use a per-fixture sqlite file so concurrent tests don't share state.
    test_db_file = pathlib.Path("/tmp/cerebro_async_client_test.db")
    test_db_file.unlink(missing_ok=True)
    test_db_url = f"sqlite+aiosqlite:///{test_db_file}"

    # Reset module-level engine/session state so init_db starts clean.
    db_session_mod._engine = None
    db_session_mod._async_session_factory = None
    os.environ["DATABASE_URL"] = test_db_url

    await init_db(database_url=test_db_url)

    # Register the SQLite-compatible DB models. Other tables (e.g. mfa_settings,
    # which uses a Postgres-only ARRAY column) are intentionally excluded from
    # test schema creation since SQLite cannot compile them.
    from src.models.db.agent_task import AgentTask
    from src.models.db.research_project import ResearchProject as DBProject
    from src.models.db.research_result import ResearchResult
    from src.models.db.workflow_checkpoint import WorkflowCheckpoint

    sqlite_compatible_tables = [
        Base.metadata.tables[t.__tablename__]
        for t in [DBProject, ResearchResult, AgentTask, WorkflowCheckpoint]
        if t.__tablename__ in Base.metadata.tables
    ]
    if db_session_mod._engine is not None and sqlite_compatible_tables:
        async with db_session_mod._engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all, tables=sqlite_compatible_tables
            )

    # Import app AFTER DB init so any module-level dependencies see ready state.
    from src.api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await close_db()
    test_db_file.unlink(missing_ok=True)


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
