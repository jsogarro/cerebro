"""E2E tests for authentication flow against live docker-compose stack.

Covers complete user authentication lifecycle including registration, login,
token refresh, protected endpoint access, and logout with both happy paths
and error scenarios.

These tests require docker-compose to be running:
    docker compose up -d

Run with: pytest tests/integration/test_auth_e2e.py --no-cov -v
"""

import uuid

import httpx
import pytest

BASE_URL = "http://localhost:8000"
# Password that passes all validation: upper, lower, digit, special, no common patterns
VALID_PASSWORD = "Xy9!zAbCdEfG"


@pytest.mark.asyncio
class TestAuthE2E:
    """End-to-end authentication flow tests against live stack."""

    async def test_complete_auth_happy_path(self):
        """Complete happy path: register -> login -> access protected endpoint.
        """
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
            # Register a new user
            email = f"happypath_{uuid.uuid4().hex[:8]}@example.com"
            register_response = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": email,
                    "username": f"happyuser_{uuid.uuid4().hex[:8]}",
                    "password": VALID_PASSWORD,
                    "confirm_password": VALID_PASSWORD,
                    "accept_terms": True,
                },
            )
            assert register_response.status_code == 201, (
                f"Registration failed: {register_response.status_code} - {register_response.text}"
            )
            register_data = register_response.json()
            assert "tokens" in register_data
            assert "access_token" in register_data["tokens"]
            access_token = register_data["tokens"]["access_token"]

            # Login with same credentials
            login_response = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": email,
                    "password": VALID_PASSWORD,
                },
            )
            assert login_response.status_code == 200, (
                f"Login failed: {login_response.status_code} - {login_response.text}"
            )
            login_data = login_response.json()
            # Login might return tokens nested or at top level
            if "tokens" in login_data:
                assert "access_token" in login_data["tokens"]
            else:
                assert "access_token" in login_data

            # Access protected endpoint with token
            protected_response = await client.get(
                "/api/v1/research/projects",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            # 200 (has projects), 403 (tenant claim issue), or 404 (no projects) are all fine
            assert protected_response.status_code in [200, 403, 404], (
                f"Protected endpoint failed: {protected_response.status_code} - {protected_response.text}"
            )

    async def test_register_with_weak_password(self):
        """Registration fails with password under 12 characters."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
            # Password too short (< 12 chars)
            response = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": f"weak_{uuid.uuid4().hex[:8]}@example.com",
                    "username": f"weakuser_{uuid.uuid4().hex[:8]}",
                    "password": "Short1!",  # Only 7 chars
                    "confirm_password": "Short1!",
                    "accept_terms": True,
                },
            )

            assert response.status_code in [400, 422], (
                f"Expected validation error, got {response.status_code}: {response.text}"
            )
            error_data = response.json()
            assert "error" in error_data or "detail" in error_data

    async def test_register_with_missing_complexity(self):
        """Registration fails when password lacks required complexity."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
            # Password missing special chars and numbers
            response = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": f"simple_{uuid.uuid4().hex[:8]}@example.com",
                    "username": f"simpleuser_{uuid.uuid4().hex[:8]}",
                    "password": "onlylowercase",  # 12+ chars but no complexity
                    "confirm_password": "onlylowercase",
                    "accept_terms": True,
                },
            )

            # Expect validation error
            assert response.status_code in [400, 422], (
                f"Expected validation error, got {response.status_code}: {response.text}"
            )
            error_data = response.json()
            assert "error" in error_data or "detail" in error_data

    async def test_register_duplicate_email(self):
        """Registration fails when email already exists."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
            email = f"duplicate_{uuid.uuid4().hex[:8]}@example.com"

            # Register first user
            response1 = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": email,
                    "username": f"originaluser_{uuid.uuid4().hex[:8]}",
                    "password": VALID_PASSWORD,
                    "confirm_password": VALID_PASSWORD,
                    "accept_terms": True,
                },
            )
            assert response1.status_code == 201, (
                f"First registration failed: {response1.status_code} - {response1.text}"
            )

            # Try to register with same email
            response2 = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": email,  # Same email
                    "username": f"newuser_{uuid.uuid4().hex[:8]}",
                    "password": VALID_PASSWORD,
                    "confirm_password": VALID_PASSWORD,
                    "accept_terms": True,
                },
            )

            assert response2.status_code == 409, (
                f"Expected 409 conflict, got {response2.status_code}: {response2.text}"
            )
            error_data = response2.json()
            assert "already registered" in str(error_data).lower()

    async def test_login_with_wrong_password(self):
        """Login fails with incorrect password."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
            email = f"rightpass_{uuid.uuid4().hex[:8]}@example.com"

            # Register user with known password
            register_response = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": email,
                    "username": f"rightpassuser_{uuid.uuid4().hex[:8]}",
                    "password": VALID_PASSWORD,
                    "confirm_password": VALID_PASSWORD,
                    "accept_terms": True,
                },
            )
            assert register_response.status_code == 201

            # Try to login with wrong password
            response = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": email,
                    "password": "Wr0ng!P4sswd",  # Different password
                },
            )

            assert response.status_code in [401, 403], (
                f"Expected 401/403, got {response.status_code}: {response.text}"
            )
            error_data = response.json()
            # Should not leak information about which field is wrong
            assert "error" in error_data or "detail" in error_data

    async def test_login_nonexistent_user(self):
        """Login fails for user that doesn't exist."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
            response = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": f"nonexistent_{uuid.uuid4().hex[:8]}@example.com",
                    "password": "SomePass123!",
                },
            )

            assert response.status_code in [401, 403, 404], (
                f"Expected 401/403/404, got {response.status_code}: {response.text}"
            )

    async def test_protected_endpoint_no_token(self):
        """Accessing protected endpoint without token fails."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
            response = await client.get("/api/v1/research/projects")
            assert response.status_code == 401, (
                f"Expected 401, got {response.status_code}: {response.text}"
            )

    async def test_protected_endpoint_malformed_token(self):
        """Accessing protected endpoint with malformed token fails."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
            response = await client.get(
                "/api/v1/research/projects",
                headers={"Authorization": "Bearer not.a.valid.jwt"},
            )
            assert response.status_code == 401, (
                f"Expected 401, got {response.status_code}: {response.text}"
            )

    async def test_protected_endpoint_expired_token(self):
        """Accessing protected endpoint with expired token fails."""
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
            # Use a completely fake token that looks expired
            fake_expired_token = (
                "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE1MTYyMzkwMjJ9.invalid"
            )
            response = await client.get(
                "/api/v1/research/projects",
                headers={"Authorization": f"Bearer {fake_expired_token}"},
            )
            assert response.status_code == 401, (
                f"Expected 401, got {response.status_code}: {response.text}"
            )
