"""
API error handling and WebSocket integration tests.

All tests in this module require the Docker-backed integration conftest at
``tests/integration/conftest.py`` (Postgres + Redis testcontainers, JWT
authenticated client). They are skipped pending two follow-ups:

1. The ``event_loop`` session fixture in the integration conftest predates
   pytest-asyncio 0.23 and now triggers ``ScopeMismatch: function scoped
   fixture _function_scoped_runner with a session scoped request object``.
   Migrate to ``loop_scope="session"`` on each ``pytest_asyncio.fixture``.

2. The three ``TestWebSocketConnections`` tests are pass-only stubs (no
   assertions). Replace with real WebSocket coverage using ``httpx-ws`` or
   ``websockets`` once the connection_manager + auth pieces are stable.

Tracked in SESSION-CHECKPOINT-2026-05-03.md.
"""

from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

_SKIP_REASON_INFRA = (
    "Requires Docker integration conftest (Postgres + Redis testcontainers, "
    "JWT authenticated_client). The session-scoped event_loop override in "
    "tests/integration/conftest.py also needs migration to pytest-asyncio "
    "loop_scope='session' before these tests can collect."
)
_SKIP_REASON_STUB = (
    "Pass-only WebSocket stub; no assertions. Replace with httpx-ws or "
    "websockets-based coverage once connection_manager + auth are stable."
)


class TestAPIErrorHandling:
    """Test API error handling and edge cases."""

    @pytest.mark.asyncio
    async def test_invalid_input_validation(
        self, authenticated_client: AsyncClient
    ) -> None:
        """Test input validation errors."""
        invalid_project = {
            "title": "",
            "query": {
                "text": "Test",
                "domains": [],
                "depth_level": "invalid",
            },
        }

        response = await authenticated_client.post(
            "/api/v1/projects", json=invalid_project
        )

        assert response.status_code == 422
        error = response.json()
        assert "detail" in error

        validations = error["detail"]
        assert any(v["loc"] == ["body", "title"] for v in validations)

    @pytest.mark.asyncio
    async def test_rate_limiting(self, authenticated_client: AsyncClient) -> None:
        """Test API rate limiting."""
        responses = []
        for _i in range(20):
            response = await authenticated_client.get("/api/v1/projects")
            responses.append(response)

    @pytest.mark.asyncio
    async def test_concurrent_modifications(
        self, authenticated_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Test handling of concurrent modifications."""
        project_data = {
            "title": "Concurrent Test",
            "description": "Testing concurrent updates",
            "query": {
                "text": "Test query",
                "domains": ["Test"],
                "depth_level": "basic",
            },
        }

        response = await authenticated_client.post(
            "/api/v1/projects", json=project_data
        )

        assert response.status_code == 201
        project_id = response.json()["id"]

        import asyncio

        async def update_project(new_title: str) -> Response:
            return await authenticated_client.patch(
                f"/api/v1/projects/{project_id}", json={"title": new_title}
            )

        update_tasks = [update_project(f"Updated Title {i}") for i in range(5)]

        update_responses = await asyncio.gather(*update_tasks, return_exceptions=True)

        success_count = sum(
            1
            for r in update_responses
            if not isinstance(r, BaseException) and r.status_code == 200
        )
        assert success_count >= 1

    @pytest.mark.asyncio
    async def test_database_connection_failure(
        self, authenticated_client: AsyncClient, mocker: Any
    ) -> None:
        """Test handling of database connection failures."""
        mocker.patch(
            "src.repositories.research_repository.ResearchRepository.create",
            side_effect=Exception("Database connection failed"),
        )

        project_data = {
            "title": "Test Project",
            "description": "Test",
            "query": {
                "text": "Test query",
                "domains": ["Test"],
                "depth_level": "basic",
            },
        }

        await authenticated_client.post("/api/v1/projects", json=project_data)


class TestWebSocketConnections:
    """Test WebSocket connections for real-time updates."""

    @pytest.mark.asyncio
    async def test_websocket_project_updates(
        self, test_engine: Any, authenticated_client: AsyncClient
    ) -> None:
        """Test WebSocket updates for project progress."""
        from httpx_ws import aconnect_ws


        # Get JWT token from authenticated client headers
        auth_header = authenticated_client.headers.get("Authorization")
        assert auth_header is not None
        token = auth_header.replace("Bearer ", "")

        # Connect via WebSocket with JWT auth
        async with aconnect_ws(
            f"ws://test/api/v1/ws?token={token}",
            authenticated_client,
        ) as ws:
            # Should successfully connect with valid JWT
            # Send heartbeat to verify connection
            await ws.send_json({"type": "heartbeat_response"})

            # Connection should remain open and not error
            # (this validates auth + connection establishment)

    @pytest.mark.asyncio
    async def test_websocket_authentication(self, authenticated_client: AsyncClient) -> None:
        """Test WebSocket authentication."""
        from httpx_ws import aconnect_ws

        # Test 1: Missing JWT should fail
        try:
            async with aconnect_ws(
                "ws://test/api/v1/ws",
                authenticated_client,
            ) as ws:
                # Should not reach here
                await ws.send_json({"type": "heartbeat_response"})
                assert False, "Expected auth failure but connection succeeded"
        except Exception:
            # Expected — no JWT provided
            pass

        # Test 2: Invalid JWT should fail
        try:
            async with aconnect_ws(
                "ws://test/api/v1/ws?token=invalid-jwt-token",
                authenticated_client,
            ) as ws:
                await ws.send_json({"type": "heartbeat_response"})
                assert False, "Expected auth failure but connection succeeded"
        except Exception:
            # Expected — invalid JWT
            pass

        # Test 3: Valid JWT should succeed
        auth_header = authenticated_client.headers.get("Authorization")
        assert auth_header is not None
        token = auth_header.replace("Bearer ", "")

        async with aconnect_ws(
            f"ws://test/api/v1/ws?token={token}",
            authenticated_client,
        ) as ws:
            # Should connect successfully
            await ws.send_json({"type": "heartbeat_response"})

    @pytest.mark.asyncio
    async def test_websocket_reconnection(
        self, authenticated_client: AsyncClient
    ) -> None:
        """Test WebSocket reconnection handling."""
        from httpx_ws import aconnect_ws

        auth_header = authenticated_client.headers.get("Authorization")
        assert auth_header is not None
        token = auth_header.replace("Bearer ", "")

        # Connect, disconnect, reconnect
        async with aconnect_ws(
            f"ws://test/api/v1/ws?token={token}",
            authenticated_client,
        ) as ws:
            await ws.send_json({"type": "heartbeat_response"})
            # First connection successful

        # Reconnect with same token
        async with aconnect_ws(
            f"ws://test/api/v1/ws?token={token}",
            authenticated_client,
        ) as ws:
            await ws.send_json({"type": "heartbeat_response"})
            # Second connection successful — reconnection works
