"""J1: sign in, find a workflow, start a dry run, inspect it, produce a report.

Steps that main cannot serve yet are strict defect records, one per step, each
failing on the assertion that checks the missing feature.
"""

import asyncio

import pytest

from tests.e2e.journeys.harness import (
    create_project,
    openapi,
    require_operation,
)

pytestmark = pytest.mark.e2e


def _missing(packet: str, what: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(
        strict=True, raises=AssertionError, reason=f"{packet}: {what}"
    )


async def test_project_roundtrip_is_persisted_for_the_callers_organization(
    client, db, new_account
):
    alice = await new_account("alice")
    project_id = await create_project(client, alice, "golden")

    row = await db.fetchrow(
        "SELECT user_id::text, organization_id::text, status::text FROM research_projects WHERE id = $1::uuid",
        project_id,
    )
    assert (row["user_id"], row["organization_id"]) == (alice.user_id, alice.org_id)
    assert row["status"].lower() == "draft"

    fetched = await client.get(
        f"/api/v1/research/projects/{project_id}", headers=alice.headers
    )
    assert fetched.status_code == 200
    assert fetched.json()["id"] == project_id
    listed = await client.get("/api/v1/research/projects", headers=alice.headers)
    assert project_id in [p["id"] for p in listed.json()]


async def test_report_request_creates_a_tenant_owned_row(client, db, new_account):
    alice = await new_account("alice")
    project_id = await create_project(client, alice, "report source")
    response = await client.post(
        "/api/v1/reports/generate",
        headers=alice.headers,
        json={
            "title": "J1",
            "query": "q",
            "project_id": project_id,
            "user_id": alice.user_id,
        },
    )
    assert response.status_code == 202, response.text

    row = None
    for _ in range(10):
        row = await db.fetchrow(
            "SELECT organization_id::text, user_id::text FROM generated_reports WHERE id = $1::uuid",
            response.json()["id"],
        )
        if row:
            break
        await asyncio.sleep(0.5)
    assert row is not None, "the 202 named a report that was never committed"
    assert (row["organization_id"], row["user_id"]) == (alice.org_id, alice.user_id)


@_missing("P02", "GET /api/v1/workflows missing (static trusted catalogue)")
async def test_workflow_catalogue_is_served(client, new_account):
    await require_operation(
        client, await new_account("alice"), "GET", "/api/v1/workflows"
    )
    pytest.fail("catalogue landed: assert the golden-run workflow is listed")


@_missing("P08", "POST /api/v1/runs missing (start a dry run)")
async def test_dry_run_can_be_started(client, new_account):
    await require_operation(client, await new_account("alice"), "POST", "/api/v1/runs")
    pytest.fail("runs landed: start a dry run and assert the run row records dry_run")


@_missing(
    "P08",
    "GET /api/v1/runs/{run_id}/evidence missing (P05 evidence and P13 verifier rounds follow)",
)
async def test_run_detail_shows_progress_verifier_rounds_and_evidence(
    client, new_account
):
    alice = await new_account("alice")
    await require_operation(client, alice, "GET", "/api/v1/runs/{run_id}/evidence")
    pytest.fail("run detail landed: assert tasks, verifier rounds and stored evidence")


@_missing("P06", "no ClaimSupport schema is served, so claim verdicts cannot be read")
async def test_claim_verdicts_are_exposed(client, new_account):
    schemas = (await openapi(client, await new_account("alice")))["components"][
        "schemas"
    ]
    assert any("claimsupport" in name.lower().replace("_", "") for name in schemas), (
        sorted(schemas)
    )
    pytest.fail(
        "verdicts landed: assert each claim carries a verdict bound to stored evidence"
    )


@_missing("P06", "the report exposes no citations bound to stored evidence")
async def test_report_is_generated_and_downloadable(client, db, new_account):
    alice = await new_account("alice")
    project_id = await create_project(client, alice, "report source")
    response = await client.post(
        "/api/v1/reports/generate",
        headers=alice.headers,
        json={
            "title": "J1",
            "query": "q",
            "project_id": project_id,
            "user_id": alice.user_id,
        },
    )
    assert response.status_code == 202, response.text
    report_id = response.json()["id"]

    status = "pending"
    for _ in range(20):
        status = await db.fetchval(
            "SELECT generation_status FROM generated_reports WHERE id = $1::uuid",
            report_id,
        )
        if status != "pending":
            break
        await asyncio.sleep(1)
    assert status == "completed", status
    fetched = await client.get(f"/api/v1/reports/{report_id}", headers=alice.headers)
    assert fetched.status_code == 200, fetched.status_code
    downloaded = await client.get(
        f"/api/v1/reports/{report_id}/download/markdown", headers=alice.headers
    )
    assert downloaded.status_code == 200, downloaded.status_code
    citations = fetched.json().get("citations")
    assert citations and all(c.get("evidence_id") for c in citations), fetched.text
    pytest.fail("citations landed: assert each evidence_id resolves to stored evidence")
