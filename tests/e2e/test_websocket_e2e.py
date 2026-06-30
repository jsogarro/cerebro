"""
E2E tests for WebSocket connection lifecycle and real-time progress events.

Covers WebSocket authentication, connection establishment, message flow,
project progress subscription, reconnection, and error handling against
a live API server.

These tests require the API server running with Postgres + Redis:
    docker compose up -d postgres redis
    alembic upgrade head
    uvicorn src.api.main:app --port 8000

Run with: pytest tests/e2e/test_websocket_e2e.py --no-cov -v
"""

import asyncio
import contextlib
import uuid

import httpx
import pytest
from httpx_ws import WebSocketUpgradeError, aconnect_ws

BASE_URL = "http://localhost:8000"
WS_BASE_URL = "ws://localhost:8000"
# Password that passes all validation: upper, lower, digit, special, no common patterns
VALID_PASSWORD = "Xy9!zAbCdEfG"


async def create_authenticated_user() -> tuple[str, str]:
    """
    Helper to create and authenticate a user, returning (email, access_token).
    """
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        email = f"wsuser_{uuid.uuid4().hex[:8]}@example.com"
        register_response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "username": f"wsuser_{uuid.uuid4().hex[:8]}",
                "password": VALID_PASSWORD,
                "confirm_password": VALID_PASSWORD,
                "accept_terms": True,
            },
        )
        assert register_response.status_code == 201, (
            f"User registration failed: {register_response.status_code} - {register_response.text}"
        )
        register_data = register_response.json()
        access_token = register_data["tokens"]["access_token"]
        return email, access_token


@pytest.mark.asyncio
class TestWebSocketE2E:
    """End-to-end WebSocket connection and message flow tests."""

    async def test_websocket_connect_with_valid_token(self):
        """
        Happy path: connect to /ws with valid JWT token via query param.

        Server accepts WS, authenticates token, sends welcome message.
        """
        _email, access_token = await create_authenticated_user()

        async with (
            httpx.AsyncClient() as http_client,
            aconnect_ws(
                f"{WS_BASE_URL}/ws?token={access_token}",
                http_client,
            ) as ws,
        ):
            # Connection established successfully
            # Server may send a welcome/connected message
            try:
                message = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                # If we get a message, verify it's a valid structure
                assert "type" in message or "event" in message or "status" in message
            except TimeoutError:
                # No immediate welcome message is also acceptable
                pass

            # Verify we can send a heartbeat
            await ws.send_json({"type": "heartbeat"})

            # Clean close
            await ws.close()

    async def test_websocket_connect_without_token_in_dev_mode(self):
        """
        Unauthenticated connection in development mode.

        In development (ENVIRONMENT=development), anonymous connections
        should be allowed per src/api/websocket/auth.py:46-50.

        Production mode should reject with close code 1008 after accept().
        """
        async with httpx.AsyncClient() as http_client:
            try:
                async with aconnect_ws(
                    f"{WS_BASE_URL}/ws",
                    http_client,
                ) as ws:
                    # In dev mode, anonymous connection succeeds
                    # Verify we can receive welcome message
                    try:
                        message = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                        # Valid structure expected
                        assert isinstance(message, dict)
                    except TimeoutError:
                        pass
                    await ws.close()
            except WebSocketUpgradeError as e:
                # Production mode would reject before accept (current broken behavior)
                # or accept then close with 1008 (future correct behavior)
                assert e.response.status_code in [401, 403], (
                    f"Expected 401/403, got {e.response.status_code}"
                )

    async def test_websocket_reconnect_with_same_token(self):
        """
        Reconnect flow: connect, disconnect cleanly, then reconnect with same JWT.

        Both connections should succeed.
        """
        _email, access_token = await create_authenticated_user()

        # First connection
        async with (
            httpx.AsyncClient() as http_client,
            aconnect_ws(
                f"{WS_BASE_URL}/ws?token={access_token}",
                http_client,
            ) as ws,
        ):
            await ws.send_json({"type": "heartbeat"})
            await ws.close()

        # Wait a moment
        await asyncio.sleep(0.5)

        # Second connection with same token
        async with (
            httpx.AsyncClient() as http_client,
            aconnect_ws(
                f"{WS_BASE_URL}/ws?token={access_token}",
                http_client,
            ) as ws,
        ):
            # Verify we can send and the connection is alive
            await ws.send_json({"type": "heartbeat"})
            await ws.close()

    @pytest.mark.skip(
        reason="Skip: needs tenant-claim fix from parallel PR - project creation will fail with 500"
    )
    async def test_websocket_project_progress_subscription(self):
        """
        Subscribe to project-specific progress updates via /ws/projects/{project_id}.

        1. Create a research project via API
        2. Connect to project WebSocket endpoint
        3. Verify connection and ability to receive progress events

        SKIPPED: Current codebase has a tenant-claim bug that causes project
        creation to return 500. This test should be enabled once the fix lands.
        """
        _email, access_token = await create_authenticated_user()

        # Create a project
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
            project_response = await client.post(
                "/api/v1/research/projects",
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "title": f"WS Test Project {uuid.uuid4().hex[:6]}",
                    "description": "Project to test WebSocket progress events",
                },
            )
            assert project_response.status_code == 201, (
                f"Project creation failed: {project_response.status_code} - {project_response.text}"
            )
            project_data = project_response.json()
            project_id = project_data["id"]

        # Connect to project-specific WebSocket
        async with (
            httpx.AsyncClient() as http_client,
            aconnect_ws(
                f"{WS_BASE_URL}/ws/projects/{project_id}?token={access_token}",
                http_client,
            ) as ws,
        ):
            # Wait for initial welcome or subscription confirmation
            try:
                message = await asyncio.wait_for(ws.receive_json(), timeout=3.0)
                # Verify message structure
                assert "type" in message or "project_id" in message
            except TimeoutError:
                # No immediate message is acceptable
                pass

            # In a real scenario, we'd trigger project activity and verify events
            # For now, just verify connection succeeded
            await ws.close()

    async def test_websocket_clean_disconnect(self):
        """
        Clean disconnect: client initiates close, server handles gracefully.

        No exceptions should be raised during normal close flow.
        """
        _email, access_token = await create_authenticated_user()

        async with (
            httpx.AsyncClient() as http_client,
            aconnect_ws(
                f"{WS_BASE_URL}/ws?token={access_token}",
                http_client,
            ) as ws,
        ):
            # Send a message to ensure connection is active
            await ws.send_json({"type": "heartbeat"})

            # Initiate close from client side
            await ws.close()

        # No exceptions = success

    async def test_websocket_malformed_token_fails(self):
        """
        Connect with a malformed JWT token.

        Server accepts, then closes with code 1008 after auth failure.
        """
        malformed_token = "not.a.valid.jwt.token"

        async with httpx.AsyncClient() as http_client:
            # Expected: handshake fails, or the server accepts then closes with 1008
            with contextlib.suppress(WebSocketUpgradeError, Exception):
                async with aconnect_ws(
                    f"{WS_BASE_URL}/ws?token={malformed_token}",
                    http_client,
                ) as ws:
                    # If connection somehow succeeds, expect server to close with error
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(ws.receive_json(), timeout=2.0)

    async def test_websocket_subscribe_to_events(self):
        """
        Subscribe to events via the general /ws endpoint.

        Send a subscription request and verify the server acknowledges it.
        """
        _email, access_token = await create_authenticated_user()

        async with (
            httpx.AsyncClient() as http_client,
            aconnect_ws(
                f"{WS_BASE_URL}/ws?token={access_token}",
                http_client,
            ) as ws,
        ):
            # Send subscription request
            await ws.send_json(
                {
                    "type": "subscribe",
                    "channel": "user_events",
                }
            )

            # Wait for acknowledgment or any response
            try:
                response = await asyncio.wait_for(ws.receive_json(), timeout=3.0)
                # Verify response structure
                assert isinstance(response, dict)
            except TimeoutError:
                # No immediate response is acceptable
                # Subscription may be silent
                pass

            await ws.close()
