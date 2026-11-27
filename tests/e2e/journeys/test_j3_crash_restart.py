"""J3: an interrupted run is marked honestly; restart is an explicit linked new run."""

import pytest

from tests.e2e.journeys.harness import create_project, require_operation

pytestmark = pytest.mark.e2e


async def test_cancelled_project_is_recorded_as_cancelled(client, db, new_account):
    alice = await new_account("alice")
    project_id = await create_project(client, alice, "to cancel")

    cancelled = await client.post(
        f"/api/v1/research/projects/{project_id}/cancel", headers=alice.headers
    )
    assert cancelled.status_code == 204

    status = await db.fetchval(
        "SELECT status::text FROM research_projects WHERE id = $1::uuid", project_id
    )
    assert status.lower() == "cancelled"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="P08-A/P02-C: POST /api/v1/runs/{run_id}/restart missing (explicit restart with parent_run_id); interruption and reconciliation are covered by tests/integration/test_run_execution_ownership.py",
)
async def test_interrupted_run_restarts_as_a_linked_new_run(client, new_account):
    await require_operation(
        client, await new_account("alice"), "POST", "/api/v1/runs/{run_id}/restart"
    )
    pytest.fail(
        "restart landed: kill the api mid-run, assert 'interrupted', restart, assert parent_run_id"
    )
