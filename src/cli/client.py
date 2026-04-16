"""
API client wrapper for Research Platform CLI.
"""

import json
from typing import Any
from uuid import UUID

import httpx
from rich.console import Console
from tenacity import retry, stop_after_attempt, wait_exponential

from src.cli.config import config
from src.models.research_project import (
    ResearchProgress,
    ResearchProject,
)

console = Console()


class APIError(Exception):
    """API error exception."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"API Error ({status_code}): {detail}")


class ResearchAPIClient:
    """Client for interacting with Research Platform API."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: int | None = None,
        verbose: bool = False,
    ):
        """Initialize API client."""
        self.base_url = (base_url or config.api_url).rstrip("/")
        self.timeout = timeout or config.api_timeout
        self.verbose = verbose or config.verbose

        # Configure HTTP client
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

        # Add authentication if available
        if config.auth_token:
            self.client.headers["Authorization"] = f"Bearer {config.auth_token}"
        elif config.api_key:
            self.client.headers["X-API-Key"] = config.api_key

    async def __aenter__(self) -> "APIClient":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()

    async def close(self) -> None:
        """Close HTTP client."""
        await self.client.aclose()

    def _log(self, message: str):
        """Log message if verbose mode is enabled."""
        if self.verbose:
            console.print(f"[dim]{message}[/dim]")

    @retry(
        stop=stop_after_attempt(config.max_retries),
        wait=wait_exponential(multiplier=1, min=4, max=10),
    )
    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Make HTTP request with retry logic."""
        url = f"{self.base_url}{path}"
        self._log(f"{method} {url}")

        if json_data and self.verbose:
            self._log(f"Request body: {json.dumps(json_data, indent=2)}")

        response = await self.client.request(
            method=method,
            url=path,
            json=json_data,
            params=params,
        )

        self._log(f"Response status: {response.status_code}")

        if response.status_code >= 400:
            try:
                error_detail = response.json().get("detail", response.text)
            except Exception:
                error_detail = response.text

            raise APIError(response.status_code, error_detail)

        return response

    # Health endpoints

    async def health_check(self) -> dict[str, Any]:
        """Check API health."""
        response = await self._request("GET", "/health")
        return response.json()

    async def readiness_check(self) -> dict[str, Any]:
        """Check API readiness."""
        response = await self._request("GET", "/ready")
        return response.json()

    # Research project endpoints

    async def create_project(
        self,
        title: str,
        query_text: str,
        domains: list[str],
        user_id: str,
        depth_level: str = "comprehensive",
        scope: dict[str, Any] | None = None,
    ) -> ResearchProject:
        """Create a new research project."""
        request_data = {
            "title": title,
            "query": {
                "text": query_text,
                "domains": domains,
                "depth_level": depth_level,
            },
            "user_id": user_id,
        }

        if scope:
            request_data["scope"] = scope

        response = await self._request(
            "POST",
            "/api/v1/research/projects",
            json_data=request_data,
        )

        return ResearchProject(**response.json())

    async def get_project(self, project_id: UUID) -> ResearchProject:
        """Get project details."""
        response = await self._request(
            "GET",
            f"/api/v1/research/projects/{project_id}",
        )
        return ResearchProject(**response.json())

    async def list_projects(
        self,
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[ResearchProject]:
        """List research projects."""
        params = {
            "limit": limit,
            "offset": offset,
        }

        if user_id:
            params["user_id"] = user_id

        if status:
            params["status"] = status

        response = await self._request(
            "GET",
            "/api/v1/research/projects",
            params=params,
        )

        projects_data = response.json()
        return [ResearchProject(**p) for p in projects_data]

    async def get_project_progress(self, project_id: UUID) -> ResearchProgress:
        """Get project progress."""
        response = await self._request(
            "GET",
            f"/api/v1/research/projects/{project_id}/progress",
        )
        return ResearchProgress(**response.json())

    async def cancel_project(self, project_id: UUID) -> None:
        """Cancel a research project."""
        await self._request(
            "POST",
            f"/api/v1/research/projects/{project_id}/cancel",
        )

    async def refine_project_scope(
        self,
        project_id: UUID,
        scope: dict[str, Any],
    ) -> ResearchProject:
        """Refine project scope."""
        response = await self._request(
            "POST",
            f"/api/v1/research/projects/{project_id}/refine",
            json_data={"scope": scope},
        )
        return ResearchProject(**response.json())

    async def get_project_results(self, project_id: UUID) -> dict[str, Any]:
        """Get project results."""
        response = await self._request(
            "GET",
            f"/api/v1/research/projects/{project_id}/results",
        )
        return response.json()

    # Generic HTTP methods for agent framework

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Generic GET request."""
        response = await self._request("GET", path, params=params)
        return response.json()

    async def post(self, path: str, json_data: dict[str, Any]) -> dict[str, Any]:
        """Generic POST request."""
        response = await self._request("POST", path, json_data=json_data)
        return response.json()
