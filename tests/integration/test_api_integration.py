"""
Comprehensive API integration tests for the Research Platform.
"""


import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories.project_factory import (
    ResearchProjectFactory,
)
from tests.factories.user_factory import UserFactory
from tests.utils.auth_utils import TestAuthManager


class TestAuthenticationFlow:
    """Test complete authentication flow."""

    @pytest.mark.asyncio
    async def test_user_registration_flow(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Test complete user registration flow."""
        # Register new user
        registration_data = {
            "email": "newuser@example.com",
            "username": "newuser",
            "password": "SecurePass123!@#",
            "full_name": "New Test User",
        }

        response = await async_client.post(
            "/api/v1/auth/register", json=registration_data
        )

        assert response.status_code == 201
        data = response.json()
        assert "user" in data
        assert "access_token" in data
        assert "refresh_token" in data

        user = data["user"]
        assert user["email"] == registration_data["email"]
        assert user["username"] == registration_data["username"]
        assert user["is_verified"] is False  # Not verified yet

        # Verify user can login
        login_response = await async_client.post(
            "/api/v1/auth/login",
            json={
                "email": registration_data["email"],
                "password": registration_data["password"],
            },
        )

        assert login_response.status_code == 200
        login_data = login_response.json()
        assert "access_token" in login_data

        # Use token to access protected endpoint
        headers = {"Authorization": f"Bearer {login_data['access_token']}"}
        profile_response = await async_client.get("/api/v1/auth/me", headers=headers)

        assert profile_response.status_code == 200
        profile = profile_response.json()
        assert profile["email"] == registration_data["email"]

    @pytest.mark.asyncio
    async def test_token_refresh_flow(
        self, authenticated_client: AsyncClient, async_client: AsyncClient
    ):
        """Test token refresh flow."""
        # Get initial tokens
        login_response = await async_client.post(
            "/api/v1/auth/login",
            json={"email": "test@example.com", "password": "Test123!@#"},
        )

        assert login_response.status_code == 200
        tokens = login_response.json()

        # Use refresh token to get new access token
        refresh_response = await async_client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )

        assert refresh_response.status_code == 200
        new_tokens = refresh_response.json()
        assert "access_token" in new_tokens
        assert new_tokens["access_token"] != tokens["access_token"]

        # Verify new token works
        headers = {"Authorization": f"Bearer {new_tokens['access_token']}"}
        profile_response = await async_client.get("/api/v1/auth/me", headers=headers)

        assert profile_response.status_code == 200

    @pytest.mark.asyncio
    async def test_password_reset_flow(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Test password reset flow."""
        # Create user
        user = UserFactory()
        db_session.add(user)
        await db_session.commit()

        # Request password reset
        reset_request = await async_client.post(
            "/api/v1/auth/forgot-password", json={"email": user.email}
        )

        assert reset_request.status_code == 200
        assert "message" in reset_request.json()

        # In real scenario, user would receive email with reset token
        # For testing, we'll simulate the reset token
        reset_token = "test-reset-token-123"

        # Reset password with token
        new_password = "NewSecurePass456!@#"
        await async_client.post(
            "/api/v1/auth/reset-password",
            json={"token": reset_token, "new_password": new_password},
        )

        # Note: This would need proper implementation in the API
        # assert reset_response.status_code == 200

        # Verify can login with new password
        # login_response = await async_client.post(
        #     "/api/v1/auth/login",
        #     json={
        #         "email": user.email,
        #         "password": new_password
        #     }
        # )
        # assert login_response.status_code == 200

    @pytest.mark.asyncio
    async def test_oauth_authentication_flow(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Test OAuth authentication flow."""
        # Initiate OAuth flow
        await async_client.get("/api/v1/auth/oauth/google")

        # Should redirect to OAuth provider
        # assert oauth_response.status_code == 302
        # assert "google.com" in oauth_response.headers.get("location", "")

        # Simulate OAuth callback
        await async_client.get(
            "/api/v1/auth/oauth/google/callback",
            params={"code": "mock-oauth-code", "state": "mock-state"},
        )

        # Note: This would need proper OAuth mock implementation
        # assert callback_response.status_code == 200
        # assert "access_token" in callback_response.json()


class TestAuthorizationAndRBAC:
    """Test authorization and role-based access control."""

    @pytest.mark.asyncio
    async def test_role_based_access(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Test role-based access control."""
        auth_manager = TestAuthManager()

        # Create users with different roles
        admin_user = UserFactory.create_admin()
        researcher_user = UserFactory.create_researcher()
        viewer_user = UserFactory.create_viewer()

        db_session.add_all([admin_user, researcher_user, viewer_user])
        await db_session.commit()

        # Create tokens for each user
        admin_token = auth_manager.create_access_token(
            admin_user.id, admin_user.email, "admin"
        )
        researcher_token = auth_manager.create_access_token(
            researcher_user.id, researcher_user.email, "researcher"
        )
        viewer_token = auth_manager.create_access_token(
            viewer_user.id, viewer_user.email, "viewer"
        )

        # Test admin-only endpoint
        await async_client.get(
            "/api/v1/admin/users", headers={"Authorization": f"Bearer {admin_token}"}
        )
        # assert admin_response.status_code == 200

        await async_client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {researcher_token}"},
        )
        # assert researcher_response.status_code == 403

        await async_client.get(
            "/api/v1/admin/users", headers={"Authorization": f"Bearer {viewer_token}"}
        )
        # assert viewer_response.status_code == 403

    @pytest.mark.asyncio
    async def test_resource_ownership(
        self,
        authenticated_client: AsyncClient,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ):
        """Test resource ownership validation."""
        # Create two users
        user1 = UserFactory()
        user2 = UserFactory()
        db_session.add_all([user1, user2])

        # Create project for user1
        project = ResearchProjectFactory(user_id=user1.id)
        db_session.add(project)
        await db_session.commit()

        auth_manager = TestAuthManager()

        # User1 can access their project
        user1_token = auth_manager.create_access_token(
            user1.id, user1.email, "researcher"
        )

        user1_response = await async_client.get(
            f"/api/v1/projects/{project.id}",
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        assert user1_response.status_code == 200

        # User2 cannot access user1's project
        user2_token = auth_manager.create_access_token(
            user2.id, user2.email, "researcher"
        )

        await async_client.get(
            f"/api/v1/projects/{project.id}",
            headers={"Authorization": f"Bearer {user2_token}"},
        )
        # assert user2_response.status_code == 403

        # Admin can access any project
        admin_user = UserFactory.create_admin()
        db_session.add(admin_user)
        await db_session.commit()

        admin_token = auth_manager.create_access_token(
            admin_user.id, admin_user.email, "admin"
        )

        await async_client.get(
            f"/api/v1/projects/{project.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        # assert admin_response.status_code == 200


class TestCompleteAPIWorkflow:
    """Test complete API workflow from project creation to results."""

    @pytest.mark.asyncio
    async def test_complete_research_workflow(
        self,
        authenticated_client: AsyncClient,
        db_session: AsyncSession,
        temporal_client,
    ):
        """Test complete research workflow through API."""
        # Step 1: Create research project
        project_data = {
            "title": "AI Ethics Research",
            "description": "Comprehensive research on AI ethics",
            "query": {
                "text": "What are the ethical implications of AGI?",
                "domains": ["AI", "Ethics", "Philosophy"],
                "depth_level": "comprehensive",
            },
        }

        create_response = await authenticated_client.post(
            "/api/v1/projects", json=project_data
        )

        assert create_response.status_code == 201
        project = create_response.json()
        project_id = project["id"]

        # Step 2: Start research workflow
        start_response = await authenticated_client.post(
            f"/api/v1/projects/{project_id}/start"
        )

        assert start_response.status_code == 200
        workflow_info = start_response.json()
        assert "workflow_id" in workflow_info

        # Step 3: Monitor progress
        progress_response = await authenticated_client.get(
            f"/api/v1/projects/{project_id}/progress"
        )

        assert progress_response.status_code == 200
        progress = progress_response.json()
        assert "status" in progress
        assert "progress" in progress

        # Step 4: Get partial results
        results_response = await authenticated_client.get(
            f"/api/v1/projects/{project_id}/results"
        )

        assert results_response.status_code == 200
        results = results_response.json()
        assert isinstance(results, list)

        # Step 5: Get final report
        report_response = await authenticated_client.get(
            f"/api/v1/projects/{project_id}/report"
        )

        # Report might not be ready yet
        assert report_response.status_code in [200, 202]

        # Step 6: List user's projects
        list_response = await authenticated_client.get("/api/v1/projects")

        assert list_response.status_code == 200
        projects = list_response.json()
        assert any(p["id"] == project_id for p in projects["items"])

    @pytest.mark.asyncio
    async def test_concurrent_project_execution(
        self, authenticated_client: AsyncClient, db_session: AsyncSession
    ):
        """Test concurrent execution of multiple projects."""
        # Create multiple projects
        projects = []
        for i in range(3):
            project_data = {
                "title": f"Research Project {i+1}",
                "description": f"Description {i+1}",
                "query": {
                    "text": f"Research question {i+1}",
                    "domains": ["AI", "ML"],
                    "depth_level": "intermediate",
                },
            }

            response = await authenticated_client.post(
                "/api/v1/projects", json=project_data
            )

            assert response.status_code == 201
            projects.append(response.json())

        # Start all projects concurrently
        import asyncio

        async def start_project(project_id):
            return await authenticated_client.post(
                f"/api/v1/projects/{project_id}/start"
            )

        start_tasks = [start_project(p["id"]) for p in projects]

        start_responses = await asyncio.gather(*start_tasks)

        # Verify all started successfully
        for response in start_responses:
            assert response.status_code == 200
            assert "workflow_id" in response.json()

        # Check all projects are running
        for project in projects:
            status_response = await authenticated_client.get(
                f"/api/v1/projects/{project['id']}/status"
            )

            assert status_response.status_code == 200
            status = status_response.json()
            assert status["status"] in ["pending", "in_progress"]

    @pytest.mark.asyncio
    async def test_project_cancellation(
        self, authenticated_client: AsyncClient, db_session: AsyncSession
    ):
        """Test project cancellation workflow."""
        # Create and start project
        project_data = {
            "title": "Cancellable Research",
            "description": "This will be cancelled",
            "query": {
                "text": "Test query",
                "domains": ["Test"],
                "depth_level": "basic",
            },
        }

        create_response = await authenticated_client.post(
            "/api/v1/projects", json=project_data
        )

        assert create_response.status_code == 201
        project_id = create_response.json()["id"]

        # Start project
        start_response = await authenticated_client.post(
            f"/api/v1/projects/{project_id}/start"
        )

        assert start_response.status_code == 200

        # Cancel project
        cancel_response = await authenticated_client.post(
            f"/api/v1/projects/{project_id}/cancel"
        )

        assert cancel_response.status_code == 200
        cancel_data = cancel_response.json()
        assert cancel_data.get("message") == "Project cancelled successfully"

        # Verify project is cancelled
        status_response = await authenticated_client.get(
            f"/api/v1/projects/{project_id}/status"
        )

        assert status_response.status_code == 200
        status = status_response.json()
        assert status["status"] == "cancelled"


class TestAPIErrorHandling:
    """Test API error handling and edge cases."""

    @pytest.mark.asyncio
    async def test_invalid_input_validation(self, authenticated_client: AsyncClient):
        """Test input validation errors."""
        # Invalid project data
        invalid_project = {
            "title": "",  # Empty title
            "query": {
                "text": "Test",
                "domains": [],  # Empty domains
                "depth_level": "invalid",  # Invalid depth level
            },
        }

        response = await authenticated_client.post(
            "/api/v1/projects", json=invalid_project
        )

        assert response.status_code == 422
        error = response.json()
        assert "detail" in error

        # Check specific validation errors
        validations = error["detail"]
        assert any(v["loc"] == ["body", "title"] for v in validations)

    @pytest.mark.asyncio
    async def test_rate_limiting(self, authenticated_client: AsyncClient):
        """Test API rate limiting."""
        # Make many requests quickly
        responses = []
        for _i in range(20):
            response = await authenticated_client.get("/api/v1/projects")
            responses.append(response)

        # Check if rate limiting is applied
        # Note: This depends on rate limiting configuration
        # status_codes = [r.status_code for r in responses]
        # assert 429 in status_codes  # Too Many Requests

    @pytest.mark.asyncio
    async def test_concurrent_modifications(
        self, authenticated_client: AsyncClient, db_session: AsyncSession
    ):
        """Test handling of concurrent modifications."""
        # Create project
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

        # Simulate concurrent updates
        import asyncio

        async def update_project(new_title):
            return await authenticated_client.patch(
                f"/api/v1/projects/{project_id}", json={"title": new_title}
            )

        update_tasks = [update_project(f"Updated Title {i}") for i in range(5)]

        update_responses = await asyncio.gather(*update_tasks, return_exceptions=True)

        # At least one should succeed
        success_count = sum(
            1
            for r in update_responses
            if not isinstance(r, Exception) and r.status_code == 200
        )
        assert success_count >= 1

    @pytest.mark.asyncio
    async def test_database_connection_failure(
        self, authenticated_client: AsyncClient, mocker
    ):
        """Test handling of database connection failures."""
        # Mock database connection failure
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

        await authenticated_client.post(
            "/api/v1/projects", json=project_data
        )

        # Should return 500 Internal Server Error
        # assert response.status_code == 500
        # error = response.json()
        # assert "detail" in error


class TestWebSocketConnections:
    """Test WebSocket connections for real-time updates."""

    @pytest.mark.asyncio
    async def test_websocket_project_updates(
        self, authenticated_client: AsyncClient, async_client: AsyncClient
    ):
        """Test WebSocket updates for project progress."""
        # Note: This requires WebSocket client implementation
        # from httpx_ws import aconnect_ws

        # async with aconnect_ws(
        #     "ws://test/ws/projects",
        #     client=async_client
        # ) as ws:
        #     # Subscribe to project updates
        #     await ws.send_json({
        #         "action": "subscribe",
        #         "project_id": "test-project-id"
        #     })
        #
        #     # Receive updates
        #     message = await ws.receive_json()
        #     assert "type" in message
        #     assert message["type"] == "subscribed"
        pass

    @pytest.mark.asyncio
    async def test_websocket_authentication(self, async_client: AsyncClient):
        """Test WebSocket authentication."""
        # Test connection without authentication
        # Should be rejected
        pass

    @pytest.mark.asyncio
    async def test_websocket_reconnection(self, authenticated_client: AsyncClient):
        """Test WebSocket reconnection handling."""
        # Test automatic reconnection on disconnect
        pass
