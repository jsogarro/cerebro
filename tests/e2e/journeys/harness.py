"""Shared plumbing for the journey suite.

Journeys talk to a running stack over HTTP and read its database directly, so
an outcome is proved by a row or a response body, not by a status code alone.
"""

import base64
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
# DATABASE_URL is deliberately not read: tests/conftest.py points it at SQLite.
DATABASE_DSN = os.environ.get(
    "E2E_DATABASE_URL", "postgresql://research:research123@localhost:5432/research_db"
)
PASSWORD = "Xy9!zAbCdEfG"


@dataclass(frozen=True)
class Account:
    email: str
    user_id: str
    org_id: str
    headers: dict[str, str]
    token: str


def _jwt_claims(token: str) -> dict[str, Any]:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


async def register(client: httpx.AsyncClient, prefix: str) -> Account:
    """Register a fresh user; every registration gets its own organization."""
    suffix = uuid.uuid4().hex[:8]
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{prefix}_{suffix}@example.com",
            "username": f"{prefix}_{suffix}",
            "password": PASSWORD,
            "confirm_password": PASSWORD,
            "accept_terms": True,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    token = body["tokens"]["access_token"]
    org_id = _jwt_claims(token)["organization_id"]
    assert org_id, "registration issued a token without an organization claim"
    return Account(
        email=body["user"]["email"],
        user_id=body["user"]["id"],
        org_id=org_id,
        headers={"Authorization": f"Bearer {token}"},
        token=token,
    )


async def create_project(
    client: httpx.AsyncClient, account: Account, title: str
) -> str:
    response = await client.post(
        "/api/v1/research/projects",
        headers=account.headers,
        json={
            "title": title,
            "user_id": account.user_id,
            "query": {"text": f"{title}?", "domains": ["AI"], "depth_level": "survey"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def openapi(client: httpx.AsyncClient, account: Account) -> dict[str, Any]:
    response = await client.get("/openapi.json", headers=account.headers)
    assert response.status_code == 200, response.text
    return response.json()


async def require_operation(
    client: httpx.AsyncClient, account: Account, method: str, path: str
) -> None:
    """Assert the stack exposes ``method path``; the journey step is missing if not."""
    paths = (await openapi(client, account))["paths"]
    assert method.lower() in paths.get(path, {}), f"{method} {path} is not served"


async def require_path_containing(
    client: httpx.AsyncClient, account: Account, fragment: str
) -> None:
    paths = sorted((await openapi(client, account))["paths"])
    assert any(fragment in p for p in paths), f"no served path contains {fragment!r}"


async def seed_run_with_evidence(db: Any, org_id: str) -> tuple[str, str]:
    """Insert one run, task, snapshot artifact and evidence row for ``org_id``."""
    run_id, task_id, artifact_id, evidence_id = (
        f"p23-{kind}-{uuid.uuid4().hex[:12]}" for kind in ("run", "task", "art", "ev")
    )
    digest = uuid.uuid4().hex * 2
    async with db.transaction():
        await db.execute(
            """INSERT INTO agent_runs (id, run_id, tenant_id, organization_id,
                 workflow_definition_id, workflow_definition_version,
                 routing_policy_id, routing_policy_version, idempotency_key,
                 requested_by, status, created_at, updated_at)
               VALUES (gen_random_uuid(), $1, $2, $3::uuid, 'p23', '1', 'p23', '1',
                 $1, 'p23', 'succeeded', now(), now())""",
            run_id,
            org_id,
            org_id,
        )
        await db.execute(
            """INSERT INTO agent_run_tasks (id, task_id, run_id, organization_id,
                 task_key, task_type, objective, idempotency_key, dependency_ids,
                 input, status, created_at, updated_at)
               VALUES (gen_random_uuid(), $1, $2, $3::uuid, 'k', 't', 'o', $1,
                 '[]'::json, '{}'::json, 'succeeded', now(), now())""",
            task_id,
            run_id,
            org_id,
        )
        await db.execute(
            """INSERT INTO agent_artifacts (id, artifact_id, run_id, task_id,
                 organization_id, kind, media_type, storage_uri, content_sha256,
                 status, trust, producer, metadata, producer_kind, created_at,
                 updated_at)
               VALUES (gen_random_uuid(), $1, $2, $3, $4::uuid, 'source_snapshot',
                 'text/plain', 'p23://x', $5, 'final', 'external_untrusted', 'p23',
                 '{}'::json, 'system', now(), now())""",
            artifact_id,
            run_id,
            task_id,
            org_id,
            digest,
        )
        await db.execute(
            """INSERT INTO agent_evidence (id, evidence_id, run_id, task_id,
                 organization_id, source_type, source_uri, snapshot_artifact_id,
                 content_sha256, locator, locator_scheme, locator_start,
                 locator_end, trust, producer_kind, parent_evidence_ids,
                 acquired_at)
               VALUES (gen_random_uuid(), $1, $2, $3, $4::uuid, 'web', 'p23://x', $5,
                 $6, 'char:0-4', 'char', 0, 4, 'external_untrusted', 'system',
                 '[]'::json, now())""",
            evidence_id,
            run_id,
            task_id,
            org_id,
            artifact_id,
            digest,
        )
    return run_id, evidence_id
