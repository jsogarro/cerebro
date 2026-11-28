"""The run read routes under the application role with row-level security forced.

The SQLite tests in ``tests/test_run_read_api.py`` prove the repository filters;
this proves the same routes on real Postgres as ``cerebro_app``, where the row
policies are enforced too, so a missing tenant GUC reads as an empty result
rather than another tenant's rows.
"""

import base64
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.middleware.auth_middleware import get_jwt_service
from src.models.db.evaluation_result import AgentEvaluationResult
from src.models.db.session import get_session
from tests.integration.test_rls_roles import ALEMBIC, _apply_roles, _role_url, _run
from tests.test_run_read_api import _JWT, CHILD_ROUTES, ORG_A, ORG_B, _as, _seed_run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
]


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer(image="postgres:16-alpine") as container:
        host, port = container.get_container_host_ip(), container.get_exposed_port(5432)
        url = (
            f"postgresql://{container.username}:{container.password}"
            f"@{host}:{port}/{container.dbname}"
        )
        _apply_roles(url)
        _run([str(ALEMBIC), "upgrade", "head"], url)
        _apply_roles(url)
        yield url


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def client(admin_url: str) -> AsyncIterator[httpx.AsyncClient]:
    from src.api.main import app

    admin = create_async_engine(
        admin_url.replace("postgresql://", "postgresql+asyncpg://")
    )
    async with async_sessionmaker(admin, expire_on_commit=False)() as seed:
        # The seed helper builds rows directly; skip FK ordering checks for it.
        await seed.execute(text("SET session_replication_role = replica"))
        await _seed_run(seed, ORG_A, "run-a", events=3, evaluate="passed")
        await _seed_run(seed, ORG_B, "run-b", events=3, evaluate="passed")
        seed.add(
            AgentEvaluationResult(
                evaluation_result_id="run-a-later-failure",
                run_id="run-a",
                organization_id=uuid.UUID(ORG_A),
                evaluator_id="claims.coverage",
                evaluator_version="1.0.0",
                dimension="claims.coverage",
                status="failed",
                subject_id="claim-2",
                deterministic=True,
                trigger="run.succeeded",
                completed_at=datetime(2026, 11, 2, tzinfo=UTC),
                recorded_at=datetime(2026, 11, 2, tzinfo=UTC),
            )
        )
        await seed.commit()

    app_engine = create_async_engine(_role_url(admin_url, "cerebro_app"))
    factory = async_sessionmaker(app_engine, expire_on_commit=False)

    async def session() -> AsyncIterator[object]:
        async with factory() as db:
            yield db
            await db.commit()

    app.dependency_overrides[get_jwt_service] = _JWT
    app.dependency_overrides[get_session] = session
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        yield http
    app.dependency_overrides.pop(get_jwt_service, None)
    app.dependency_overrides.pop(get_session, None)
    await app_engine.dispose()
    await admin.dispose()


@pytest.mark.parametrize(
    ("org", "own", "other"), [(ORG_A, "run-a", "run-b"), (ORG_B, "run-b", "run-a")]
)
async def test_each_organization_reads_only_its_own_run(
    client: httpx.AsyncClient, org: str, own: str, other: str
) -> None:
    listing = (await client.get("/api/v1/runs", headers=_as(org))).json()
    assert [r["run_id"] for r in listing["items"]] == [own]

    detail = await client.get(f"/api/v1/runs/{own}", headers=_as(org))
    assert detail.status_code == 200
    assert detail.json()["objective"] == f"objective of {own}"

    for route in CHILD_ROUTES:
        mine = await client.get(f"/api/v1/runs/{own}/{route}", headers=_as(org))
        assert mine.status_code == 200, (route, mine.text)
        assert other not in mine.text
        foreign = await client.get(f"/api/v1/runs/{other}/{route}", headers=_as(org))
        assert foreign.status_code == 404, (route, foreign.text)


async def test_a_foreign_run_reads_exactly_like_a_missing_one(
    client: httpx.AsyncClient,
) -> None:
    foreign = await client.get("/api/v1/runs/run-a", headers=_as(ORG_B))
    missing = await client.get("/api/v1/runs/no-such-run", headers=_as(ORG_B))
    assert (foreign.status_code, foreign.text) == (missing.status_code, missing.text)


async def test_forged_cursors_are_rejected_not_errors(
    client: httpx.AsyncClient,
) -> None:
    forged_event = base64.urlsafe_b64encode(
        json.dumps({"run_id": "run-a", "last_sequence": 2**70}).encode()
    ).decode()
    for url in (
        f"/api/v1/runs/run-a/event-history?cursor={forged_event}",
        "/api/v1/runs?cursor=not-a-cursor",
    ):
        response = await client.get(url, headers=_as(ORG_A))
        assert response.status_code == 400, (url, response.text)


async def test_evaluator_state_ignores_the_page_size(
    client: httpx.AsyncClient,
) -> None:
    one = (
        await client.get("/api/v1/runs/run-a/evaluations?limit=1", headers=_as(ORG_A))
    ).json()
    assert [s["status"] for s in one["evaluator_states"]] == ["failed"]
