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
    event_payload: dict[str, Any] | None = None,
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
                payload=event_payload or {"n": sequence},
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
    assert page["has_more"] is True
    rest = (
        await client.get(
            f"/api/v1/runs/run-1/event-history?cursor={page['next_cursor']}",
            headers=_as(ORG_A),
        )
    ).json()
    assert [e["sequence"] for e in rest["items"]] == [3]
    assert rest["has_more"] is False

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


def _evaluation(run_id: str, key: str, status: str, hour: int, **fields: Any) -> Any:
    return AgentEvaluationResult(
        evaluation_result_id=f"{run_id}-{key}",
        run_id=run_id,
        organization_id=uuid.UUID(ORG_A),
        evaluator_id=fields.pop("evaluator_id", "claims.coverage"),
        evaluator_version=fields.pop("evaluator_version", "1.0.0"),
        dimension=fields.pop("dimension", "claims.coverage"),
        status=status,
        deterministic=True,
        trigger="run.succeeded",
        completed_at=T0 + timedelta(hours=hour),
        recorded_at=T0 + timedelta(hours=hour),
        **fields,
    )


async def test_evaluator_states_do_not_depend_on_the_page_size(seeded) -> None:
    client, factory = seeded
    async with factory() as db:
        await _seed_run(db, ORG_A, "run-a", evaluate="passed")
        db.add(_evaluation("run-a", "later", "failed", 5, subject_id="claim-2"))
        await db.commit()

    full = (
        await client.get("/api/v1/runs/run-a/evaluations", headers=_as(ORG_A))
    ).json()
    one = (
        await client.get("/api/v1/runs/run-a/evaluations?limit=1", headers=_as(ORG_A))
    ).json()
    assert [s["status"] for s in full["evaluator_states"]] == ["failed"]
    assert [s["status"] for s in one["evaluator_states"]] == ["failed"]
    assert one["truncated"] is True and len(one["items"]) == 1


async def test_unregistered_rows_filling_the_page_do_not_hide_a_registered_evaluator(
    seeded,
) -> None:
    client, factory = seeded
    async with factory() as db:
        await _seed_run(db, ORG_A, "run-a")
        db.add(_evaluation("run-a", "old", "passed", -5))
        db.add(
            _evaluation(
                "run-a",
                "other",
                "passed",
                -9,
                evaluator_id="other.eval",
                evaluator_version="1",
                dimension="other",
            )
        )
        await db.commit()

    one = (
        await client.get("/api/v1/runs/run-a/evaluations?limit=1", headers=_as(ORG_A))
    ).json()
    assert [s["status"] for s in one["evaluator_states"]] == ["passed"]


async def test_failure_text_is_never_returned_raw(seeded) -> None:
    client, factory = seeded
    leak = "OperationalError: SELECT * FROM users WHERE key='sk-123' via https://p.test"
    async with factory() as db:
        await _seed_run(
            db,
            ORG_A,
            "run-a",
            status="failed",
            events=1,
            event_payload={"error": leak, "reason": leak, "attempt": 1},
        )
        run = await db.get(AgentRun, (await _run_pk(db, "run-a")))
        run.status_reason = leak
        task = (await db.execute(_task_query("run-a"))).scalar_one()
        task.status_reason = leak
        attempt = (await db.execute(_attempt_query("run-a"))).scalar_one()
        attempt.status_reason = leak
        await db.commit()
        db.add(_cancelled_run())
        await db.commit()

    run = await client.get("/api/v1/runs/run-a", headers=_as(ORG_A))
    tasks = await client.get("/api/v1/runs/run-a/tasks", headers=_as(ORG_A))
    events = await client.get("/api/v1/runs/run-a/event-history", headers=_as(ORG_A))
    for response in (run, tasks, events):
        assert "sk-123" not in response.text and "SELECT" not in response.text
    assert run.json()["status_reason"] == "execution failed"
    assert events.json()["items"][0]["payload"]["attempt"] == 1

    cancelled = await client.get("/api/v1/runs/run-cancelled", headers=_as(ORG_A))
    assert cancelled.json()["status_reason"] == "Execution cancelled by request"


async def test_event_history_returns_a_resume_cursor_when_exhausted(seeded) -> None:
    client, factory = seeded
    async with factory() as db:
        await _seed_run(db, ORG_A, "run-1", events=2)

    done = (
        await client.get("/api/v1/runs/run-1/event-history", headers=_as(ORG_A))
    ).json()
    assert done["has_more"] is False and done["next_cursor"]

    async with factory() as db:
        db.add(
            AgentRunEvent(
                event_id="late",
                run_id="run-1",
                organization_id=uuid.UUID(ORG_A),
                aggregate_type="run",
                aggregate_id="run-1",
                sequence=3,
                event_type="run.succeeded",
                event_type_version="1",
                occurred_at=T0,
                producer="p",
                deduplication_key="late",
                payload={},
            )
        )
        await db.commit()
    tail = (
        await client.get(
            f"/api/v1/runs/run-1/event-history?cursor={done['next_cursor']}",
            headers=_as(ORG_A),
        )
    ).json()
    assert [e["sequence"] for e in tail["items"]] == [3]

    empty = (
        await client.get(
            f"/api/v1/runs/run-1/event-history?cursor={tail['next_cursor']}",
            headers=_as(ORG_A),
        )
    ).json()
    assert empty["items"] == [] and empty["next_cursor"] == tail["next_cursor"]


async def test_a_forged_event_cursor_is_a_400_not_a_500(seeded) -> None:
    import base64
    import json

    client, factory = seeded
    async with factory() as db:
        await _seed_run(db, ORG_A, "run-a")
    token = base64.urlsafe_b64encode(
        json.dumps({"run_id": "run-a", "last_sequence": 2**70}).encode()
    ).decode()
    response = await client.get(
        f"/api/v1/runs/run-a/event-history?cursor={token}", headers=_as(ORG_A)
    )
    assert response.status_code == 400, response.text


async def test_runs_sharing_a_timestamp_page_without_loss_or_repeat(seeded) -> None:
    client, factory = seeded
    async with factory() as db:
        for run_id in ("r1", "r2", "r3", "r4", "r5"):
            await _seed_run(db, ORG_A, run_id, minutes=0)

    seen: list[str] = []
    cursor = ""
    for _ in range(6):
        page = (
            await client.get(f"/api/v1/runs?limit=2{cursor}", headers=_as(ORG_A))
        ).json()
        seen += [r["run_id"] for r in page["items"]]
        if page["next_cursor"] is None:
            break
        cursor = f"&cursor={page['next_cursor']}"
    assert sorted(seen) == ["r1", "r2", "r3", "r4", "r5"]


async def test_attempt_and_objective_reads_are_bounded_to_what_is_returned(
    seeded,
) -> None:
    from src.repositories.run_lifecycle_repository import RunLifecycleRepository

    _, factory = seeded
    async with factory() as db:
        await _seed_run(db, ORG_A, "run-a")
        for n in range(2, 5):
            db.add(
                AgentRunTask(
                    task_id=f"run-a-task-{n}",
                    run_id="run-a",
                    organization_id=uuid.UUID(ORG_A),
                    task_key=f"k{n}",
                    task_type="t",
                    objective=f"later {n}",
                    idempotency_key=f"k{n}",
                    status="succeeded",
                    created_at=T0 + timedelta(minutes=n),
                )
            )
        await db.commit()
        repo = RunLifecycleRepository(db)
        objectives = await repo.first_task_objectives(["run-a"], organization_id=ORG_A)
        attempts = await repo.get_attempts_for_run(
            "run-a", organization_id=ORG_A, task_ids=["run-a-task-2"]
        )
    assert objectives == {"run-a": "objective of run-a"}
    assert attempts == []


async def test_each_response_carries_the_fields_the_web_adapters_require(
    seeded,
) -> None:
    client, factory = seeded
    async with factory() as db:
        await _seed_run(db, ORG_A, "run-a", events=1, evaluate="passed")

    adapters = _required_adapter_fields()
    evidence_page = (
        await client.get("/api/v1/runs/run-a/evidence", headers=_as(ORG_A))
    ).json()
    assert evidence_page["run_id"] == "run-a"
    served = {
        "adaptRun": (await client.get("/api/v1/runs/run-a", headers=_as(ORG_A))).json(),
        "adaptTask": _first(
            await client.get("/api/v1/runs/run-a/tasks", headers=_as(ORG_A))
        ),
        "adaptEvidence": {
            "run_id": evidence_page["run_id"],
            **evidence_page["items"][0],
        },
        "adaptArtifact": _first(
            await client.get("/api/v1/runs/run-a/artifacts", headers=_as(ORG_A))
        ),
        "adaptEvaluation": _first(
            await client.get("/api/v1/runs/run-a/evaluations", headers=_as(ORG_A))
        ),
        "adaptEvent": _first(
            await client.get("/api/v1/runs/run-a/event-history", headers=_as(ORG_A))
        ),
    }
    for adapter, item in served.items():
        assert adapters[adapter], adapter
        missing = [
            canonical
            for canonical, aliases in adapters[adapter]
            if not any(key in item for key in (canonical, *aliases))
        ]
        assert not missing, (adapter, missing)


def _first(response: httpx.Response) -> dict[str, Any]:
    return response.json()["items"][0]


def _required_adapter_fields() -> dict[str, list[tuple[str, list[str]]]]:
    """The ``requiredStrings`` fields each run adapter in runs.ts insists on."""
    import re
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "cerebro/web/src/api/runs.ts"
    ).read_text()
    fields: dict[str, list[tuple[str, list[str]]]] = {}
    for match in re.finditer(r"export function (adapt\w+)\(", source):
        body = source[match.end() :]
        end = re.search(r"\nexport function ", body)
        body = body[: end.start()] if end else body
        block = re.search(r"requiredStrings\(value, \[(.*?)\]\);", body, re.S)
        if block:
            fields[match.group(1)] = [
                (
                    entry.group(1),
                    re.findall(r"'(\w+)'", entry.group(2) or ""),
                )
                for entry in re.finditer(
                    r"canonical: '(\w+)'(?:, aliases: \[(.*?)\])?", block.group(1)
                )
            ]
    return fields


async def _run_pk(db: AsyncSession, run_id: str) -> uuid.UUID:
    from sqlalchemy import select

    return (
        await db.execute(select(AgentRun.id).where(AgentRun.run_id == run_id))
    ).scalar_one()


def _task_query(run_id: str) -> Any:
    from sqlalchemy import select

    return select(AgentRunTask).where(AgentRunTask.run_id == run_id)


def _attempt_query(run_id: str) -> Any:
    from sqlalchemy import select

    return select(AgentTaskAttempt).where(AgentTaskAttempt.run_id == run_id)


def _cancelled_run() -> AgentRun:
    return AgentRun(
        run_id="run-cancelled",
        tenant_id=ORG_A,
        organization_id=uuid.UUID(ORG_A),
        workflow_definition_id="w",
        workflow_definition_version="1",
        routing_policy_id="p",
        routing_policy_version="1",
        idempotency_key="run-cancelled",
        requested_by="u",
        status="cancelled",
        status_reason="Execution cancelled by request",
        started_at=T0,
        completed_at=T0,
        created_at=T0,
        updated_at=T0,
    )
