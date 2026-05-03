"""
Research flow integration tests (Group A from issue #12).

Ported from tests/test_e2e_research_flow.py TestResearchFlow (skipped tests).
These require JWT auth + Postgres testcontainer + dependency injection.
"""


import pytest
from httpx import AsyncClient

from tests.integration.conftest import IntegrationTestConfig


class TestResearchFlowIntegration:
    """Test research CRUD flow with real Postgres + JWT."""

    @pytest.mark.asyncio
    async def test_create_research_project(
        self, authenticated_client: AsyncClient
    ) -> None:
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
            "user_id": IntegrationTestConfig.TEST_USER_ID,
        }
        r = await authenticated_client.post("/api/v1/research/projects", json=payload)
        assert r.status_code == 201
        data = r.json()
        assert data["title"] == "AI Safety Research"
        assert data["user_id"] == IntegrationTestConfig.TEST_USER_ID
        assert "id" in data

    @pytest.mark.asyncio
    async def test_get_research_project(
        self, authenticated_client: AsyncClient
    ) -> None:
        """Test retrieving a created project by ID."""
        # Create first
        payload = {
            "title": "Test Project",
            "query": {"text": "Test query for retrieval", "domains": ["CS"]},
            "user_id": IntegrationTestConfig.TEST_USER_ID,
        }
        create_r = await authenticated_client.post(
            "/api/v1/research/projects", json=payload
        )
        assert create_r.status_code == 201
        pid = create_r.json()["id"]

        # Get
        r = await authenticated_client.get(f"/api/v1/research/projects/{pid}")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == pid
        assert data["title"] == "Test Project"

    @pytest.mark.asyncio
    async def test_list_research_projects(
        self, authenticated_client: AsyncClient
    ) -> None:
        """Test listing projects returns created ones."""
        # Create a project
        payload = {
            "title": "Listed Project",
            "query": {"text": "Test query for listing", "domains": ["Math"]},
            "user_id": IntegrationTestConfig.TEST_USER_ID,
        }
        await authenticated_client.post("/api/v1/research/projects", json=payload)

        # List
        r = await authenticated_client.get("/api/v1/research/projects")
        assert r.status_code == 200
        projects = r.json()
        assert len(projects) >= 1
        titles = [p["title"] for p in projects]
        assert "Listed Project" in titles

    @pytest.mark.asyncio
    async def test_get_research_progress(
        self, authenticated_client: AsyncClient
    ) -> None:
        """Test progress endpoint returns valid structure."""
        # Create
        payload = {
            "title": "Progress Project",
            "query": {"text": "Test query for progress", "domains": ["Physics"]},
            "user_id": IntegrationTestConfig.TEST_USER_ID,
        }
        create_r = await authenticated_client.post(
            "/api/v1/research/projects", json=payload
        )
        pid = create_r.json()["id"]

        # Progress
        r = await authenticated_client.get(f"/api/v1/research/projects/{pid}/progress")
        assert r.status_code == 200
        data = r.json()
        assert "total_tasks" in data
        assert "progress_percentage" in data
        assert data["project_id"] == pid

    @pytest.mark.asyncio
    async def test_get_results_returns_data_or_404(
        self, authenticated_client: AsyncClient
    ) -> None:
        """Bug: results endpoint crashed with selectinload on dynamic relationships.
        Fix: Changed lazy='dynamic' to lazy='selectin'.
        Results come from in-memory execution (200) or 404 if not yet available.
        """
        payload = {
            "title": "Results Project",
            "query": {"text": "Test query for results check", "domains": ["Bio"]},
            "user_id": IntegrationTestConfig.TEST_USER_ID,
        }
        create_r = await authenticated_client.post(
            "/api/v1/research/projects", json=payload
        )
        pid = create_r.json()["id"]

        r = await authenticated_client.get(f"/api/v1/research/projects/{pid}/results")
        # Execution may complete fast (simulated) so accept 200 or 404
        assert r.status_code in (200, 404)

    @pytest.mark.asyncio
    async def test_nonexistent_project_returns_404(
        self, authenticated_client: AsyncClient
    ) -> None:
        """Test that requesting a non-existent project returns 404."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        r = await authenticated_client.get(f"/api/v1/research/projects/{fake_id}")
        assert r.status_code == 404
