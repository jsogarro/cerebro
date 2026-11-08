"""
E2E tests for tenant isolation (multi-tenancy).

Verifies that users cannot access each other's projects.

Requires: docker compose up -d
Run with: pytest tests/integration/test_tenant_isolation_e2e.py --no-cov -v

Every registered account gets its own organization claim, so a 403 on an
own-org request is a failure, not a reason to skip. Cross-tenant reads and
writes return 404 (the repository filters by user and organization, so another
tenant's project is indistinguishable from a missing one).
"""

import uuid

import httpx
import pytest

BASE_URL = "http://localhost:8000"
VALID_PASSWORD = "Xy9!zAbCdEfG"


async def register_and_login(username_prefix: str) -> tuple[str, str]:
    """Helper: register a new user and return (token, user id)."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10.0) as client:
        email = f"{username_prefix}_{uuid.uuid4().hex[:8]}@example.com"
        register_response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "username": f"{username_prefix}_{uuid.uuid4().hex[:8]}",
                "password": VALID_PASSWORD,
                "confirm_password": VALID_PASSWORD,
                "accept_terms": True,
            },
        )
        assert register_response.status_code == 201
        data = register_response.json()
        return data["tokens"]["access_token"], data["user"]["id"]


@pytest.mark.asyncio
class TestTenantIsolationE2E:
    """End-to-end tenant isolation tests."""

    async def test_cross_tenant_project_access_denied(self):
        """
        Alice creates project P_A. Bob tries to access P_A -> denied.
        """
        alice_token, alice_user_id = await register_and_login("alice")
        bob_token, _ = await register_and_login("bob")

        alice_headers = {"Authorization": f"Bearer {alice_token}"}
        bob_headers = {"Authorization": f"Bearer {bob_token}"}

        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
            # Alice creates a project
            alice_create_response = await client.post(
                "/api/v1/research/projects",
                headers=alice_headers,
                json={
                    "title": "Alice's Research",
                    "user_id": alice_user_id,
                    "query": {"text": "Alice's question", "domains": ["AI"]},
                },
            )

            assert alice_create_response.status_code == 201
            alice_project = alice_create_response.json()
            alice_project_id = alice_project.get("id") or alice_project.get(
                "project", {}
            ).get("id")

            # Control: the project exists and Alice can read it
            alice_get = await client.get(
                f"/api/v1/research/projects/{alice_project_id}",
                headers=alice_headers,
            )
            assert alice_get.status_code == 200

            # Bob tries to access Alice's project
            bob_access_response = await client.get(
                f"/api/v1/research/projects/{alice_project_id}",
                headers=bob_headers,
            )

            assert bob_access_response.status_code == 404

    async def test_users_see_only_own_projects(self):
        """
        Alice lists projects -> sees only hers. Bob lists -> sees only his.
        """
        alice_token, alice_user_id = await register_and_login("alice2")
        bob_token, bob_user_id = await register_and_login("bob2")

        alice_headers = {"Authorization": f"Bearer {alice_token}"}
        bob_headers = {"Authorization": f"Bearer {bob_token}"}

        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
            created = {}
            for name, user_id, headers in (
                ("alice", alice_user_id, alice_headers),
                ("bob", bob_user_id, bob_headers),
            ):
                response = await client.post(
                    "/api/v1/research/projects",
                    headers=headers,
                    json={
                        "title": f"{name}'s listed project",
                        "user_id": user_id,
                        "query": {"text": f"{name}'s question", "domains": ["AI"]},
                    },
                )
                assert response.status_code == 201
                created[name] = response.json()["id"]

            # Alice lists her projects
            alice_list_response = await client.get(
                "/api/v1/research/projects",
                headers=alice_headers,
            )

            # Bob lists his projects
            bob_list_response = await client.get(
                "/api/v1/research/projects",
                headers=bob_headers,
            )

            assert alice_list_response.status_code == 200
            assert bob_list_response.status_code == 200

            alice_ids = {p["id"] for p in alice_list_response.json()}
            bob_ids = {p["id"] for p in bob_list_response.json()}
            assert created["alice"] in alice_ids
            assert created["bob"] not in alice_ids
            assert created["bob"] in bob_ids
            assert created["alice"] not in bob_ids

            # Asking for another user's listing is refused outright
            other = await client.get(
                "/api/v1/research/projects",
                params={"user_id": bob_user_id},
                headers=alice_headers,
            )
            assert other.status_code == 403

    async def test_cross_tenant_cancel_denied(self):
        """Bob cannot cancel Alice's project."""
        alice_token, alice_user_id = await register_and_login("alice3")
        bob_token, _ = await register_and_login("bob3")

        alice_headers = {"Authorization": f"Bearer {alice_token}"}
        bob_headers = {"Authorization": f"Bearer {bob_token}"}

        async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
            # Alice creates a project
            alice_create_response = await client.post(
                "/api/v1/research/projects",
                headers=alice_headers,
                json={
                    "title": "Alice's Research 3",
                    "user_id": alice_user_id,
                    "query": {"text": "Alice's question 3", "domains": ["AI"]},
                },
            )

            assert alice_create_response.status_code == 201
            alice_project = alice_create_response.json()
            alice_project_id = alice_project.get("id") or alice_project.get(
                "project", {}
            ).get("id")

            # Bob tries to cancel Alice's project
            bob_cancel_response = await client.post(
                f"/api/v1/research/projects/{alice_project_id}/cancel",
                headers=bob_headers,
            )

            assert bob_cancel_response.status_code == 404

            # The refused cancel must not have touched Alice's project
            alice_after = await client.get(
                f"/api/v1/research/projects/{alice_project_id}",
                headers=alice_headers,
            )
            assert alice_after.status_code == 200
            assert alice_after.json()["status"] != "cancelled"
