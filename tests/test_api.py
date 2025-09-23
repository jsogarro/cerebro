"""
Tests for FastAPI endpoints following TDD principles.
"""

import pytest
from fastapi import status
from httpx import AsyncClient


class TestHealthEndpoints:
    """Test health check endpoints."""

    @pytest.mark.asyncio
    async def test_health_check(self, async_client: AsyncClient):
        """Test basic health check endpoint."""
        response = await async_client.get("/health")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "research-platform-api"

    @pytest.mark.asyncio
    async def test_readiness_check(self, async_client: AsyncClient):
        """Test readiness check endpoint."""
        response = await async_client.get("/ready")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "ready"
        assert "checks" in data
        assert data["checks"]["database"] == "ok"

    @pytest.mark.asyncio
    async def test_liveness_check(self, async_client: AsyncClient):
        """Test liveness check endpoint."""
        response = await async_client.get("/live")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "alive"


class TestResearchEndpoints:
    """Test research API endpoints."""

    @pytest.mark.asyncio
    async def test_create_research_project(
        self, async_client: AsyncClient, sample_research_query
    ):
        """Test creating a new research project."""
        payload = {
            "title": "Test Research Project",
            "query": sample_research_query,
            "user_id": "test-user-123",
        }

        response = await async_client.post("/api/v1/research/projects", json=payload)

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["title"] == "Test Research Project"
        assert data["user_id"] == "test-user-123"
        assert "id" in data
        assert data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_get_research_project_not_found(self, async_client: AsyncClient):
        """Test getting a non-existent research project."""
        project_id = "550e8400-e29b-41d4-a716-446655440000"
        response = await async_client.get(f"/api/v1/research/projects/{project_id}")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert "not found" in data["detail"]

    @pytest.mark.asyncio
    async def test_list_research_projects(self, async_client: AsyncClient):
        """Test listing research projects."""
        response = await async_client.get("/api/v1/research/projects")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_get_research_progress(self, async_client: AsyncClient):
        """Test getting research project progress."""
        project_id = "550e8400-e29b-41d4-a716-446655440000"
        response = await async_client.get(
            f"/api/v1/research/projects/{project_id}/progress"
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "project_id" in data
        assert "progress_percentage" in data
        assert data["progress_percentage"] >= 0
        assert data["progress_percentage"] <= 100

    @pytest.mark.asyncio
    async def test_cancel_research_project(self, async_client: AsyncClient):
        """Test cancelling a research project."""
        project_id = "550e8400-e29b-41d4-a716-446655440000"
        response = await async_client.post(
            f"/api/v1/research/projects/{project_id}/cancel"
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT

    @pytest.mark.asyncio
    async def test_metrics_endpoint(self, async_client: AsyncClient):
        """Test Prometheus metrics endpoint."""
        response = await async_client.get("/metrics/")

        assert response.status_code == status.HTTP_200_OK
        # Prometheus metrics return text/plain content type
        assert response.text is not None
        assert len(response.text) > 0
