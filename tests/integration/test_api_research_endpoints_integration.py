"""
Research API endpoints integration tests (Group B from issue #12).

Ported from tests/test_api.py TestResearchEndpoints (skipped tests).
These require JWT auth + Postgres testcontainer + dependency injection.
"""

import pytest
from httpx import AsyncClient
from starlette import status


class TestResearchEndpointsIntegration:
    """Test research API endpoints with real Postgres + JWT."""

    @pytest.mark.asyncio
    async def test_create_research_project(
        self, authenticated_client: AsyncClient
    ) -> None:
        """Test creating a new research project."""
        payload = {
            "title": "Test Research Project",
            "query": {
                "text": "What are the implications of artificial general intelligence on society?",
                "domains": ["AI", "Ethics", "Sociology"],
                "depth_level": "comprehensive",
            },
            "user_id": "test-user-123",
        }

        response = await authenticated_client.post(
            "/api/v1/research/projects", json=payload
        )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["title"] == "Test Research Project"
        assert data["user_id"] == "test-user-123"
        assert "id" in data
        assert data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_get_research_project_not_found(
        self, authenticated_client: AsyncClient
    ) -> None:
        """Test getting a non-existent research project."""
        project_id = "550e8400-e29b-41d4-a716-446655440000"
        response = await authenticated_client.get(
            f"/api/v1/research/projects/{project_id}"
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert "not found" in data["detail"]

    @pytest.mark.asyncio
    async def test_list_research_projects(
        self, authenticated_client: AsyncClient
    ) -> None:
        """Test listing research projects."""
        response = await authenticated_client.get("/api/v1/research/projects")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_get_research_progress(
        self, authenticated_client: AsyncClient
    ) -> None:
        """Test getting research project progress."""
        project_id = "550e8400-e29b-41d4-a716-446655440000"
        response = await authenticated_client.get(
            f"/api/v1/research/projects/{project_id}/progress"
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "project_id" in data
        assert "progress_percentage" in data
        assert data["progress_percentage"] >= 0
        assert data["progress_percentage"] <= 100

    @pytest.mark.asyncio
    async def test_cancel_research_project(
        self, authenticated_client: AsyncClient
    ) -> None:
        """Test cancelling a research project."""
        project_id = "550e8400-e29b-41d4-a716-446655440000"
        response = await authenticated_client.post(
            f"/api/v1/research/projects/{project_id}/cancel"
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT

    @pytest.mark.asyncio
    async def test_metrics_endpoint(self, authenticated_client: AsyncClient) -> None:
        """Test Prometheus metrics endpoint."""
        response = await authenticated_client.get("/metrics/")

        assert response.status_code == status.HTTP_200_OK
        # Prometheus metrics return text/plain content type
        assert response.text is not None
        assert len(response.text) > 0
