"""
E2E regression tests for the research flow.

Each test documents a specific bug found during the E2E bug bash session
and ensures the fix holds. Tests use the async_client fixture from conftest.py
which provides an HTTPX AsyncClient connected to the FastAPI app via ASGITransport.
"""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.models.db.base import Base
from src.models.db.session import close_db, init_db

_TEST_DB_FILE = "/tmp/cerebro_test.db"
_TEST_DB_URL = f"sqlite+aiosqlite:///{_TEST_DB_FILE}"

# Six project-CRUD tests below now return 401 because group-012 multi-tenancy
# moved /api/v1/research/projects/* behind JWT + Postgres SET LOCAL for RLS.
# SQLite cannot exercise SET LOCAL, so those paths require a Postgres
# testcontainer instead of ASGITransport+sqlite. Tracked for the integration
# conftest at tests/integration/conftest.py — see SESSION-CHECKPOINT-2026-05-03.md.
_AUTH_RLS_SKIP_REASON = (
    "Requires JWT auth + Postgres SET LOCAL for RLS (group-012 multi-tenancy). "
    "SQLite-backed ASGITransport cannot exercise this path; port to "
    "tests/integration/conftest.py with testcontainers."
)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    """Create a test client with initialized database."""
    import pathlib

    # Clean up any previous test DB
    pathlib.Path(_TEST_DB_FILE).unlink(missing_ok=True)

    # Reset DB module state for clean initialization
    from src.models.db import session as db_session_mod

    db_session_mod._engine = None
    db_session_mod._async_session_factory = None

    # Override DATABASE_URL so the app sees our test DB
    os.environ["DATABASE_URL"] = _TEST_DB_URL

    # Reload settings to pick up the new DATABASE_URL
    from src.core import config as config_mod

    config_mod.settings = config_mod.Settings()  # type: ignore[misc]

    # Initialize DB
    await init_db(database_url=_TEST_DB_URL)

    from src.models.db.agent_task import AgentTask
    from src.models.db.research_project import ResearchProject as DBProject
    from src.models.db.research_result import ResearchResult
    from src.models.db.session import _engine
    from src.models.db.workflow_checkpoint import WorkflowCheckpoint

    tables = [
        Base.metadata.tables[t.__tablename__]
        for t in [DBProject, ResearchResult, AgentTask, WorkflowCheckpoint]
        if t.__tablename__ in Base.metadata.tables
    ]
    if _engine is not None and tables:
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=tables)

    from src.api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await close_db()
    pathlib.Path(_TEST_DB_FILE).unlink(missing_ok=True)


class TestAppBoot:
    """Tests for app boot issues found in Stage 1."""

    @pytest.mark.asyncio
    async def test_app_imports_without_fastmcp(self) -> None:
        """Bug: Import chain forced fastmcp load, crashing app when not installed.
        Fix: Made MCP imports lazy in __init__.py files and factory.py.
        """
        from src.api.main import app

        assert app is not None
        assert app.title == "Research Platform API"

    @pytest.mark.asyncio
    async def test_app_boots_with_valid_secret_key(self) -> None:
        """Bug: SECRET_KEY validator required >=32 chars but test conftest set 15 chars.
        Fix: Updated conftest.py to use a 32+ char test secret key.
        """
        import os

        secret = os.environ.get("SECRET_KEY", "")
        assert len(secret) >= 32, f"SECRET_KEY must be >= 32 chars, got {len(secret)}"

    @pytest.mark.asyncio
    async def test_agent_api_routes_load(self) -> None:
        """Bug: agent_api.py had Query params with complex types (list[dict])
        which caused AssertionError at import time.
        Fix: Changed to Body params.
        """
        from src.api.routes import agent_api

        assert agent_api.router is not None

    @pytest.mark.asyncio
    async def test_report_config_type_annotations(self) -> None:
        """Bug: report_config.py had invalid type annotation None[ReportSettings].
        Fix: Changed to ReportSettings | None.
        """
        import inspect

        from src.services import report_config

        # Verify the functions exist and have correct signatures
        for fn_name in ("create_template_config", "create_format_config", "create_quality_config"):
            fn = getattr(report_config, fn_name)
            sig = inspect.signature(fn)
            # The first param 'settings' should accept None
            param = next(iter(sig.parameters.values()))
            assert param.default is None, f"{fn_name} settings param should default to None"

    @pytest.mark.asyncio
    async def test_masr_imports_resolve(self) -> None:
        """Bug: src.ai_brain.models.masr didn't exist; 4 files imported from it.
        Fix: Redirected to src.ai_brain.config.model_schemas and router modules.
        """
        from src.ai_brain.config.model_schemas import ModelTier, RoutingStrategy
        from src.ai_brain.router.query_analyzer import ComplexityLevel, QueryDomain
        from src.ai_brain.router.routing_types import CollaborationMode

        assert ModelTier is not None
        assert RoutingStrategy is not None
        assert ComplexityLevel is not None
        assert QueryDomain is not None
        assert CollaborationMode is not None

    @pytest.mark.asyncio
    async def test_portable_uuid_type(self) -> None:
        """Bug: Models used postgresql.UUID which doesn't work with SQLite.
        Fix: Created PortableUUID type that uses CHAR(36) on non-Postgres.
        """
        import uuid

        from src.models.db.base import PortableUUID

        puuid = PortableUUID()
        assert puuid is not None

        # Test process_bind_param with a mock dialect
        class MockDialect:
            name = "sqlite"

        test_uuid = uuid.uuid4()
        result = puuid.process_bind_param(test_uuid, MockDialect())
        assert result == str(test_uuid)

        # Test round-trip
        restored = puuid.process_result_value(result, MockDialect())
        assert restored == test_uuid


class TestResearchFlow:
    """Tests for the research flow endpoints found in Stage 2."""

    @pytest.mark.asyncio
    async def test_health_endpoint(self, client: AsyncClient) -> None:
        """Verify /health returns 200 with expected payload."""
        r = await client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "healthy"

    @pytest.mark.skip(reason=_AUTH_RLS_SKIP_REASON)
    @pytest.mark.asyncio
    async def test_create_research_project(self, client: AsyncClient) -> None:
        """Bug: Multiple issues prevented project creation:
        - DB not initialized (lifespan missing init_db call)
        - QueuePool incompatible with SQLite
        - dict passed to Text column (query field)
        - User FK constraint with no users table
        Fix: Init DB in lifespan, NullPool for SQLite, JSON serialize query,
        plain string user_id.
        """
        payload = {
            "title": "AI Safety Research",
            "query": {
                "text": "What are the current approaches to AI alignment?",
                "domains": ["AI", "Ethics"],
            },
            "user_id": "test-user-001",
        }
        r = await client.post("/api/v1/research/projects", json=payload)
        assert r.status_code == 201
        data = r.json()
        assert data["title"] == "AI Safety Research"
        assert data["user_id"] == "test-user-001"
        assert "id" in data

    @pytest.mark.skip(reason=_AUTH_RLS_SKIP_REASON)
    @pytest.mark.asyncio
    async def test_get_research_project(self, client: AsyncClient) -> None:
        """Test retrieving a created project by ID."""
        # Create first
        payload = {
            "title": "Test Project",
            "query": {"text": "Test query for retrieval", "domains": ["CS"]},
            "user_id": "test-user-002",
        }
        create_r = await client.post("/api/v1/research/projects", json=payload)
        assert create_r.status_code == 201
        pid = create_r.json()["id"]

        # Get
        r = await client.get(f"/api/v1/research/projects/{pid}")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == pid
        assert data["title"] == "Test Project"

    @pytest.mark.skip(reason=_AUTH_RLS_SKIP_REASON)
    @pytest.mark.asyncio
    async def test_list_research_projects(self, client: AsyncClient) -> None:
        """Test listing projects returns created ones."""
        # Create a project
        payload = {
            "title": "Listed Project",
            "query": {"text": "Test query for listing", "domains": ["Math"]},
            "user_id": "test-user-003",
        }
        await client.post("/api/v1/research/projects", json=payload)

        # List
        r = await client.get("/api/v1/research/projects")
        assert r.status_code == 200
        projects = r.json()
        assert len(projects) >= 1
        titles = [p["title"] for p in projects]
        assert "Listed Project" in titles

    @pytest.mark.skip(reason=_AUTH_RLS_SKIP_REASON)
    @pytest.mark.asyncio
    async def test_get_research_progress(self, client: AsyncClient) -> None:
        """Test progress endpoint returns valid structure."""
        # Create
        payload = {
            "title": "Progress Project",
            "query": {"text": "Test query for progress", "domains": ["Physics"]},
            "user_id": "test-user-004",
        }
        create_r = await client.post("/api/v1/research/projects", json=payload)
        pid = create_r.json()["id"]

        # Progress
        r = await client.get(f"/api/v1/research/projects/{pid}/progress")
        assert r.status_code == 200
        data = r.json()
        assert "total_tasks" in data
        assert "progress_percentage" in data
        assert data["project_id"] == pid

    @pytest.mark.skip(reason=_AUTH_RLS_SKIP_REASON)
    @pytest.mark.asyncio
    async def test_get_results_returns_data_or_404(self, client: AsyncClient) -> None:
        """Bug: results endpoint crashed with selectinload on dynamic relationships.
        Fix: Changed lazy='dynamic' to lazy='selectin'.
        Results come from in-memory execution (200) or 404 if not yet available.
        """
        payload = {
            "title": "Results Project",
            "query": {"text": "Test query for results check", "domains": ["Bio"]},
            "user_id": "test-user-005",
        }
        create_r = await client.post("/api/v1/research/projects", json=payload)
        pid = create_r.json()["id"]

        r = await client.get(f"/api/v1/research/projects/{pid}/results")
        # Execution may complete fast (simulated) so accept 200 or 404
        assert r.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_intelligent_query_endpoint(self, client: AsyncClient) -> None:
        """Test the intelligent query routing endpoint."""
        r = await client.post(
            "/api/v1/query/research",
            json={"query": "AI safety research overview", "domains": ["AI"]},
        )
        assert r.status_code == 200
        data = r.json()
        assert "execution_id" in data
        assert data["status"] in ("pending", "completed", "running")

    @pytest.mark.skip(reason=_AUTH_RLS_SKIP_REASON)
    @pytest.mark.asyncio
    async def test_nonexistent_project_returns_404(self, client: AsyncClient) -> None:
        """Test that requesting a non-existent project returns 404."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        r = await client.get(f"/api/v1/research/projects/{fake_id}")
        assert r.status_code == 404
