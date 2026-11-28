"""Unit tests for tenant-scoped evidence metadata listing and cursor pagination."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.api.services.evidence_service import (
    InvalidEvidenceCursorError,
    RunNotFoundError,
    list_run_evidence,
)
from src.models.db.artifact import AgentArtifact
from src.models.db.base import Base
from src.models.db.evidence import AgentEvidence
from src.models.db.run_lifecycle import AgentRun, AgentRunTask
from src.repositories.tenant_scope import MissingOrganizationContextError

ORG_A = uuid.UUID("00000000-0000-0000-0000-00000000000a")
ORG_B = uuid.UUID("00000000-0000-0000-0000-00000000000b")


@pytest.fixture
async def db_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        Base.metadata.tables[name]
        for name in (
            "agent_runs",
            "agent_run_tasks",
            "agent_artifacts",
            "agent_evidence",
        )
    ]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_run_with_evidence(
    session: AsyncSession,
    *,
    run_id: str,
    org_id: uuid.UUID,
    count: int = 3,
    same_timestamp: bool = False,
) -> list[str]:
    now = datetime(2026, 11, 27, 12, 0, 0, tzinfo=UTC)
    run = AgentRun(
        run_id=run_id,
        tenant_id=str(org_id),
        organization_id=org_id,
        workflow_definition_id="wf-1",
        workflow_definition_version="1.0.0",
        routing_policy_id="rp-1",
        routing_policy_version="1.0.0",
        idempotency_key=f"idem-{run_id}",
        requested_by="user-1",
        status="succeeded",
    )
    task = AgentRunTask(
        task_id=f"task-{run_id}",
        run_id=run_id,
        organization_id=org_id,
        task_key="search",
        task_type="literature_review",
        objective="Search literature",
        idempotency_key=f"task-idem-{run_id}",
        status="succeeded",
    )
    artifact = AgentArtifact(
        artifact_id=f"art-{run_id}",
        run_id=run_id,
        organization_id=org_id,
        kind="source_snapshot",
        media_type="text/plain",
        status="final",
        storage_uri=f"/var/lib/cerebro/source_snapshots/aa/{'a' * 64}",
        content_sha256="a" * 64,
        trust="external_untrusted",
        producer="search-evidence-recorder",
        producer_kind="system",
    )
    session.add_all([run, task, artifact])
    await session.flush()

    ids: list[str] = []
    for i in range(count):
        ev_id = f"ev-{run_id}-{i}"
        ids.append(ev_id)
        session.add(
            AgentEvidence(
                id=uuid.UUID(int=100 + i),
                evidence_id=ev_id,
                run_id=run_id,
                task_id=task.task_id,
                organization_id=org_id,
                source_type="search_result",
                source_uri=f"https://arxiv.org/abs/2401.0000{i}",
                snapshot_artifact_id=artifact.artifact_id,
                content_sha256="a" * 64,
                locator=f"bytes:{i * 10}-{(i + 1) * 10}",
                locator_scheme="bytes",
                locator_start=i * 10,
                locator_end=(i + 1) * 10,
                trust="external_untrusted",
                producer_kind="system",
                parent_evidence_ids=[],
                acquired_at=now if same_timestamp else now + timedelta(seconds=i),
            )
        )
    await session.commit()
    return ids


@pytest.mark.asyncio
async def test_list_run_evidence_refuses_none_organization(
    db_session: AsyncSession,
) -> None:
    await _seed_run_with_evidence(db_session, run_id="run-1", org_id=ORG_A)

    with pytest.raises(MissingOrganizationContextError):
        await list_run_evidence(db_session, "run-1", organization_id=None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_list_run_evidence_hides_foreign_and_unknown_runs_as_not_found(
    db_session: AsyncSession,
) -> None:
    await _seed_run_with_evidence(db_session, run_id="run-a", org_id=ORG_A)

    with pytest.raises(RunNotFoundError) as foreign:
        await list_run_evidence(db_session, "run-a", organization_id=str(ORG_B))
    with pytest.raises(RunNotFoundError) as unknown:
        await list_run_evidence(db_session, "run-missing", organization_id=str(ORG_B))

    assert str(foreign.value) == str(unknown.value) == "Run not found"


@pytest.mark.asyncio
async def test_list_run_evidence_returns_metadata_only_and_paginates_on_acquired_at_and_id(
    db_session: AsyncSession,
) -> None:
    await _seed_run_with_evidence(
        db_session, run_id="run-ties", org_id=ORG_A, count=5, same_timestamp=True
    )

    page1 = await list_run_evidence(
        db_session, "run-ties", organization_id=str(ORG_A), limit=2
    )
    assert len(page1.items) == 2
    assert page1.has_more is True
    assert page1.next_cursor is not None

    first = page1.items[0].model_dump()
    assert set(first) == {
        "evidence_id",
        "source_type",
        "source_uri",
        "locator",
        "content_sha256",
        "trust",
        "acquired_at",
    }
    assert "storage_uri" not in first
    assert first["trust"] == "external_untrusted"

    page2 = await list_run_evidence(
        db_session,
        "run-ties",
        organization_id=str(ORG_A),
        cursor=page1.next_cursor,
        limit=2,
    )
    assert len(page2.items) == 2
    assert page2.has_more is True

    page3 = await list_run_evidence(
        db_session,
        "run-ties",
        organization_id=str(ORG_A),
        cursor=page2.next_cursor,
        limit=2,
    )
    assert len(page3.items) == 1
    assert page3.has_more is False
    assert page3.next_cursor is None

    collected = [
        item.evidence_id for page in (page1, page2, page3) for item in page.items
    ]
    assert collected == [f"ev-run-ties-{i}" for i in range(5)]


@pytest.mark.asyncio
async def test_list_run_evidence_enforces_limit_bounds_and_cursor_validation(
    db_session: AsyncSession,
) -> None:
    await _seed_run_with_evidence(db_session, run_id="run-1", org_id=ORG_A, count=1)

    with pytest.raises(ValueError, match="500"):
        await list_run_evidence(
            db_session, "run-1", organization_id=str(ORG_A), limit=501
        )
    with pytest.raises(ValueError, match="1"):
        await list_run_evidence(
            db_session, "run-1", organization_id=str(ORG_A), limit=0
        )
    with pytest.raises(InvalidEvidenceCursorError):
        await list_run_evidence(
            db_session,
            "run-1",
            organization_id=str(ORG_A),
            cursor="not-valid-base64-json",
        )
