"""J2: two organizations, every read surface returns only the caller's rows."""

import pytest

from src.repositories.evidence_repository import EvidenceRepository
from tests.e2e.journeys.harness import (
    DATABASE_DSN,
    create_project,
    require_operation,
    seed_run_with_evidence,
)

pytestmark = pytest.mark.e2e


async def test_projects_are_invisible_across_organizations(client, new_account):
    alice, bob = await new_account("alice"), await new_account("bob")
    assert alice.org_id != bob.org_id
    project_id = await create_project(client, alice, "alice only")

    own = await client.get(
        f"/api/v1/research/projects/{project_id}", headers=alice.headers
    )
    assert own.status_code == 200
    assert (await client.get("/api/v1/research/projects", headers=alice.headers)).json()

    assert (
        await client.get(f"/api/v1/research/projects/{project_id}", headers=bob.headers)
    ).status_code == 404
    assert (
        await client.get("/api/v1/research/projects", headers=bob.headers)
    ).json() == []
    for method, suffix in (("GET", "progress"), ("GET", "results"), ("POST", "cancel")):
        other = await client.request(
            method,
            f"/api/v1/research/projects/{project_id}/{suffix}",
            headers=bob.headers,
        )
        assert other.status_code == 404, (suffix, other.text)


async def test_reports_are_invisible_across_organizations(client, new_account):
    alice, bob = await new_account("alice"), await new_account("bob")
    requested = await client.post(
        "/api/v1/reports/generate",
        headers=alice.headers,
        json={"title": "alice report", "query": "q", "user_id": alice.user_id},
    )
    assert requested.status_code == 202, requested.text
    report_id = requested.json()["id"]

    own = await client.get(
        f"/api/v1/reports/{report_id}/integrity", headers=alice.headers
    )
    assert own.status_code == 200, own.text
    assert (await client.get("/api/v1/reports", headers=bob.headers)).json()[
        "reports"
    ] == []
    for suffix in ("", "/download/markdown"):
        other = await client.get(
            f"/api/v1/reports/{report_id}{suffix}", headers=bob.headers
        )
        assert other.status_code == 404, (suffix, other.text)
    integrity = await client.get(
        f"/api/v1/reports/{report_id}/integrity", headers=bob.headers
    )
    assert "formats_checked" not in integrity.json(), integrity.text


async def test_run_event_stream_hides_another_organizations_run(
    client, db, new_account
):
    alice, bob = await new_account("alice"), await new_account("bob")
    run_id, _ = await seed_run_with_evidence(db, alice.org_id)

    async with client.stream(
        "GET", f"/api/v1/runs/{run_id}/events", headers=alice.headers
    ) as own:
        assert own.status_code == 200
    other = await client.get(f"/api/v1/runs/{run_id}/events", headers=bob.headers)
    assert other.status_code == 404


async def test_evidence_reads_are_scoped_to_the_callers_organization(db, new_account):
    """Evidence has no HTTP route yet (P08), so the repository is the read surface."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    alice, bob = await new_account("alice"), await new_account("bob")
    run_id, evidence_id = await seed_run_with_evidence(db, alice.org_id)

    engine = create_async_engine(
        DATABASE_DSN.replace("postgresql://", "postgresql+asyncpg://")
    )
    try:
        async with async_sessionmaker(engine)() as session:
            repository = EvidenceRepository(session)
            owned = await repository.list_evidence_for_run(
                run_id, organization_id=alice.org_id
            )
            assert [row.evidence_id for row in owned] == [evidence_id]
            assert (
                await repository.list_evidence_for_run(
                    run_id, organization_id=bob.org_id
                )
                == []
            )
            assert (
                await repository.get_evidence(evidence_id, organization_id=bob.org_id)
                is None
            )
    finally:
        await engine.dispose()


async def test_row_level_security_hides_rows_from_a_non_owner_role(db, new_account):
    alice, bob = await new_account("alice"), await new_account("bob")
    run_id, _ = await seed_run_with_evidence(db, alice.org_id)
    await db.execute(
        """DO $$ BEGIN
             IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'p23_rls_probe') THEN
               CREATE ROLE p23_rls_probe NOLOGIN;
             END IF;
           END $$"""
    )
    await db.execute("GRANT SELECT ON agent_evidence, agent_runs TO p23_rls_probe")

    async def visible_to(org_id: str) -> int:
        async with db.transaction():
            await db.execute("SET LOCAL ROLE p23_rls_probe")
            await db.execute(
                "SELECT set_config('app.current_org_id', $1, true)", org_id
            )
            return await db.fetchval(
                "SELECT count(*) FROM agent_evidence WHERE run_id = $1", run_id
            )

    assert await visible_to(alice.org_id) == 1
    assert await visible_to(bob.org_id) == 0


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="P07-C: the application connects as a superuser/BYPASSRLS role, so RLS is posture only",
)
async def test_application_database_role_is_subject_to_row_level_security(db):
    role = await db.fetchrow(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )
    assert not (role["rolsuper"] or role["rolbypassrls"]), dict(role)


def _missing(packet: str, what: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(
        strict=True, raises=AssertionError, reason=f"{packet}: {what}"
    )


_MISSING_READ_ROUTES = [
    pytest.param(
        "/api/v1/runs", marks=_missing("P08", "GET /api/v1/runs missing"), id="runs"
    ),
    pytest.param(
        "/api/v1/runs/{run_id}/evidence",
        marks=_missing("P08", "GET /api/v1/runs/{run_id}/evidence missing"),
        id="evidence",
    ),
    pytest.param(
        "/api/v1/runs/{run_id}/artifacts",
        marks=_missing("P08", "GET /api/v1/runs/{run_id}/artifacts missing"),
        id="artifacts",
    ),
    pytest.param(
        "/api/v1/memory",
        marks=_missing("P16", "GET /api/v1/memory missing"),
        id="memory",
    ),
]


@pytest.mark.parametrize("path", _MISSING_READ_ROUTES)
async def test_read_route_exists_for_isolation_check(client, new_account, path):
    await require_operation(client, await new_account("alice"), "GET", path)
    pytest.fail(
        "route landed: assert Bob cannot read Alice's rows through it, then drop the xfail"
    )


@pytest.mark.parametrize("path", ["/api/v1/admin/alerts", "/api/v1/admin/audit-events"])
async def test_a_tenant_user_cannot_read_global_audit_or_alerts(
    client, new_account, path
):
    for name in ("alice", "bob"):
        response = await client.get(path, headers=(await new_account(name)).headers)
        assert response.status_code == 403, (name, response.status_code)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="P12: document uploads are disabled (DOCUMENT_UPLOADS_ENABLED) in the default stack",
)
async def test_documents_listing_is_served(client, new_account):
    alice = await new_account("alice")
    response = await client.get("/api/v1/documents", headers=alice.headers)
    assert response.status_code == 200, response.status_code
