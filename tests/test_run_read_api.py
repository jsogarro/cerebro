"""The read-only run surface: tenant isolation, cursor binding, honest evaluations.

Drives the real application with two organizations over an in-memory SQLite
database holding only the run tables, so a route that is not mounted, or reads
without the caller's organization, fails here rather than in an e2e stack.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_jwt_service
from src.models.db.artifact import AgentArtifact
from src.models.db.evaluation_result import AgentEvaluationResult
from src.models.db.evidence import AgentEvidence
from src.models.db.run_event import AgentRunEvent
from src.models.db.run_lifecycle import AgentRun, AgentRunTask, AgentTaskAttempt
from src.models.db.session import get_session

ORG_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ORG_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
T0 = datetime(2026, 11, 1, tzinfo=UTC)
DIGEST = "ab" * 32

CHILD_ROUTES = ("tasks", "evidence", "artifacts", "evaluations", "event-history")


class _JWT:
    """The bearer string is the organization the caller acts as."""

    async def validate_token(self, token: str) -> TokenPayload:
        now = datetime.now(UTC)
        return TokenPayload(
            sub="00000000-0000-0000-0000-000000000001",
            email="u@example.com",
            organization_id=token,
            jti="j",
            iat=now,
            exp=now + timedelta(minutes=5),
        )


def _as(org: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {org}"}


async def _seed_run(
    session: AsyncSession,
    org: str,
    run_id: str,
    *,
    minutes: int = 0,
    workflow: str = "research.single-worker",
    status: str = "succeeded",
    events: int = 0,
    evaluate: str | None = None,
) -> None:
    org_id = uuid.UUID(org)
    created = T0 + timedelta(minutes=minutes)
    session.add(
        AgentRun(
            run_id=run_id,
            tenant_id=org,
            organization_id=org_id,
            workflow_definition_id=workflow,
            workflow_definition_version="1",
            routing_policy_id="p",
            routing_policy_version="1",
            idempotency_key=run_id,
            requested_by="u",
            status=status,
            started_at=created,
            completed_at=created,
            created_at=created,
            updated_at=created,
        )
    )
    session.add(
        AgentRunTask(
            task_id=f"{run_id}-task",
            run_id=run_id,
            organization_id=org_id,
            task_key="k",
            task_type="t",
            objective=f"objective of {run_id}",
            idempotency_key="k",
            status="succeeded",
            created_at=created,
            updated_at=created,
        )
    )
    session.add(
        AgentTaskAttempt(
            attempt_id=f"{run_id}-attempt",
            task_id=f"{run_id}-task",
            run_id=run_id,
            organization_id=org_id,
            ordinal=1,
            idempotency_key="a",
            status="succeeded",
            created_at=created,
            updated_at=created,
        )
    )
    session.add(
        AgentArtifact(
            artifact_id=f"{run_id}-artifact",
            run_id=run_id,
            task_id=f"{run_id}-task",
            organization_id=org_id,
            kind="source_snapshot",
            media_type="text/plain",
            storage_uri=f"secret-store://{run_id}",
            content_sha256=DIGEST,
            status="final",
            trust="external_untrusted",
            producer="p",
            producer_kind="system",
            created_at=created,
            updated_at=created,
        )
    )
    session.add(
        AgentEvidence(
            evidence_id=f"{run_id}-evidence",
            run_id=run_id,
            task_id=f"{run_id}-task",
            organization_id=org_id,
            source_type="web",
            source_uri="https://example.test/a",
            snapshot_artifact_id=f"{run_id}-artifact",
            content_sha256=DIGEST,
            locator="char:0-4",
            locator_scheme="char",
            locator_start=0,
            locator_end=4,
            trust="external_untrusted",
            producer_kind="system",
            parent_evidence_ids=[],
            acquired_at=created,
        )
    )
    for sequence in range(1, events + 1):
        session.add(
            AgentRunEvent(
                event_id=f"{run_id}-event-{sequence}",
                run_id=run_id,
                organization_id=org_id,
                aggregate_type="run",
                aggregate_id=run_id,
                sequence=sequence,
                event_type="run.progress",
                event_type_version="1",
                occurred_at=created,
                producer="p",
                deduplication_key=f"d{sequence}",
                payload={"n": sequence},
            )
        )
    if evaluate is not None:
        session.add(
            AgentEvaluationResult(
                evaluation_result_id=f"{run_id}-evaluation",
                run_id=run_id,
                organization_id=org_id,
                evaluator_id="claims.coverage",
                evaluator_version="1.0.0",
                dimension="claims.coverage",
                status=evaluate,
                deterministic=True,
                trigger="run.succeeded",
                completed_at=created,
                recorded_at=created,
            )
        )
    await session.commit()


@pytest_asyncio.fixture
async def seeded() -> AsyncIterator[tuple[httpx.AsyncClient, async_sessionmaker[Any]]]:
    from src.api.main import app

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        for model in (
            AgentRun,
            AgentRunTask,
            AgentTaskAttempt,
            AgentArtifact,
            AgentEvidence,
            AgentEvaluationResult,
            AgentRunEvent,
        ):
            await connection.run_sync(model.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def session() -> AsyncIterator[AsyncSession]:
        async with factory() as db:
            yield db

    app.dependency_overrides[get_jwt_service] = _JWT
    app.dependency_overrides[get_session] = session
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            yield client, factory
    finally:
        app.dependency_overrides.pop(get_jwt_service, None)
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


async def test_each_route_returns_only_the_callers_organization(seeded) -> None:
    client, factory = seeded
    async with factory() as db:
        await _seed_run(db, ORG_A, "run-a", events=2, evaluate="passed")
        await _seed_run(db, ORG_B, "run-b", events=2, evaluate="passed")

    listing = (await client.get("/api/v1/runs", headers=_as(ORG_A))).json()
    assert [r["run_id"] for r in listing["items"]] == ["run-a"]
    detail = await client.get("/api/v1/runs/run-a", headers=_as(ORG_A))
    assert detail.status_code == 200
    assert detail.json()["objective"] == "objective of run-a"

    for route in CHILD_ROUTES:
        own = await client.get(f"/api/v1/runs/run-a/{route}", headers=_as(ORG_A))
        assert own.status_code == 200, (route, own.text)
        assert "run-b" not in own.text, route
        assert "run-a" in own.text, route


async def test_a_foreign_run_is_404_on_every_route_and_leaks_nothing(seeded) -> None:
    client, factory = seeded
    async with factory() as db:
        await _seed_run(db, ORG_A, "run-a", events=1, evaluate="passed")

    missing = await client.get("/api/v1/runs/no-such-run", headers=_as(ORG_B))
    for route in ("", *(f"/{r}" for r in CHILD_ROUTES)):
        foreign = await client.get(f"/api/v1/runs/run-a{route}", headers=_as(ORG_B))
        assert foreign.status_code == 404, (route, foreign.text)
        assert "run-a" not in foreign.text, route
        assert foreign.json() == missing.json() or route != ""

    assert (await client.get("/api/v1/runs", headers=_as(ORG_B))).json()["items"] == []


async def test_run_list_pages_with_an_opaque_cursor(seeded) -> None:
    client, factory = seeded
    async with factory() as db:
        for minute, run_id in enumerate(("r1", "r2", "r3")):
            await _seed_run(db, ORG_A, run_id, minutes=minute)

    first = (await client.get("/api/v1/runs?limit=2", headers=_as(ORG_A))).json()
    assert [r["run_id"] for r in first["items"]] == ["r3", "r2"]
    second = (
        await client.get(
            f"/api/v1/runs?limit=2&cursor={first['next_cursor']}", headers=_as(ORG_A)
        )
    ).json()
    assert [r["run_id"] for r in second["items"]] == ["r1"]
    assert second["next_cursor"] is None


async def test_a_run_list_cursor_is_bound_to_its_filter_and_organization(
    seeded,
) -> None:
    client, factory = seeded
    async with factory() as db:
        for minute, run_id in enumerate(("r1", "r2", "r3")):
            await _seed_run(db, ORG_A, run_id, minutes=minute)
        await _seed_run(db, ORG_B, "rb")
    cursor = (await client.get("/api/v1/runs?limit=1", headers=_as(ORG_A))).json()[
        "next_cursor"
    ]

    other_filter = await client.get(
        f"/api/v1/runs?limit=1&status=failed&cursor={cursor}", headers=_as(ORG_A)
    )
    other_org = await client.get(
        f"/api/v1/runs?limit=1&cursor={cursor}", headers=_as(ORG_B)
    )
    garbage = await client.get("/api/v1/runs?cursor=not-a-cursor", headers=_as(ORG_A))
    for rejected in (other_filter, other_org, garbage):
        assert rejected.status_code == 400, rejected.text
        assert "r1" not in rejected.text and "rb" not in rejected.text


async def test_event_history_pages_in_sequence_and_rejects_another_runs_cursor(
    seeded,
) -> None:
    client, factory = seeded
    async with factory() as db:
        await _seed_run(db, ORG_A, "run-1", events=3)
        await _seed_run(db, ORG_A, "run-2", events=3)

    page = (
        await client.get("/api/v1/runs/run-1/event-history?limit=2", headers=_as(ORG_A))
    ).json()
    assert [e["sequence"] for e in page["items"]] == [1, 2]
    rest = (
        await client.get(
            f"/api/v1/runs/run-1/event-history?cursor={page['next_cursor']}",
            headers=_as(ORG_A),
        )
    ).json()
    assert [e["sequence"] for e in rest["items"]] == [3]

    wrong_run = await client.get(
        f"/api/v1/runs/run-2/event-history?cursor={page['next_cursor']}",
        headers=_as(ORG_A),
    )
    assert wrong_run.status_code == 400, wrong_run.text


async def test_a_run_without_an_evaluation_reads_not_attempted(seeded) -> None:
    client, factory = seeded
    async with factory() as db:
        await _seed_run(db, ORG_A, "bare")
        await _seed_run(db, ORG_A, "judged", evaluate="failed")

    bare = (
        await client.get("/api/v1/runs/bare/evaluations", headers=_as(ORG_A))
    ).json()
    assert bare["items"] == []
    assert [s["status"] for s in bare["evaluator_states"]] == ["not_attempted"]
    assert bare["evaluator_states"][0]["evaluator_id"] == "claims.coverage"

    judged = (
        await client.get("/api/v1/runs/judged/evaluations", headers=_as(ORG_A))
    ).json()
    assert [s["status"] for s in judged["evaluator_states"]] == ["failed"]
    assert [i["status"] for i in judged["items"]] == ["failed"]


async def test_projections_do_not_expose_storage_locations(seeded) -> None:
    client, factory = seeded
    async with factory() as db:
        await _seed_run(db, ORG_A, "run-a")

    artifacts = await client.get("/api/v1/runs/run-a/artifacts", headers=_as(ORG_A))
    assert "secret-store" not in artifacts.text
    assert artifacts.json()["items"][0]["content_sha256"] == DIGEST
    tasks = (await client.get("/api/v1/runs/run-a/tasks", headers=_as(ORG_A))).json()
    assert [a["attempt_id"] for a in tasks["items"][0]["attempts"]] == ["run-a-attempt"]


async def test_child_rows_stamped_with_another_organization_are_never_returned(
    seeded,
) -> None:
    """The run check alone is not the tenant boundary: each child query scopes too."""
    client, factory = seeded
    async with factory() as db:
        await _seed_run(db, ORG_A, "run-a", events=1, evaluate="passed")
        org_b = uuid.UUID(ORG_B)
        db.add(
            AgentRunTask(
                task_id="planted-task",
                run_id="run-a",
                organization_id=org_b,
                task_key="x",
                task_type="t",
                objective="planted",
                idempotency_key="x",
                status="succeeded",
            )
        )
        db.add(
            AgentTaskAttempt(
                attempt_id="planted-attempt",
                task_id="run-a-task",
                run_id="run-a",
                organization_id=org_b,
                ordinal=2,
                idempotency_key="x",
                status="succeeded",
            )
        )
        db.add(
            AgentArtifact(
                artifact_id="planted-artifact",
                run_id="run-a",
                organization_id=org_b,
                kind="k",
                media_type="text/plain",
                storage_uri="x",
                content_sha256=DIGEST,
                status="final",
                trust="external_untrusted",
                producer="p",
                producer_kind="system",
            )
        )
        db.add(
            AgentEvidence(
                evidence_id="planted-evidence",
                run_id="run-a",
                task_id="run-a-task",
                organization_id=org_b,
                source_type="web",
                source_uri="x",
                snapshot_artifact_id="run-a-artifact",
                content_sha256=DIGEST,
                locator="char:5-9",
                locator_scheme="char",
                locator_start=5,
                locator_end=9,
                trust="external_untrusted",
                producer_kind="system",
                parent_evidence_ids=[],
                acquired_at=T0,
            )
        )
        db.add(
            AgentRunEvent(
                event_id="planted-event",
                run_id="run-a",
                organization_id=org_b,
                aggregate_type="run",
                aggregate_id="run-a",
                sequence=2,
                event_type="run.progress",
                event_type_version="1",
                occurred_at=T0,
                producer="p",
                deduplication_key="planted",
                payload={},
            )
        )
        db.add(
            AgentEvaluationResult(
                evaluation_result_id="planted-evaluation",
                run_id="run-a",
                organization_id=org_b,
                evaluator_id="claims.coverage",
                evaluator_version="1.0.0",
                dimension="claims.coverage",
                status="failed",
                deterministic=True,
                trigger="x",
                completed_at=T0,
                recorded_at=T0 + timedelta(hours=1),
            )
        )
        await db.commit()

    for route in CHILD_ROUTES:
        response = await client.get(f"/api/v1/runs/run-a/{route}", headers=_as(ORG_A))
        assert "planted" not in response.text, route
