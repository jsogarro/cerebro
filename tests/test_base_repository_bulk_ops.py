"""Bulk repository operations must be batched and tenant-scoped."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.models.db.agent_task import AgentTask
from src.models.db.research_project import ResearchProject
from src.models.db.research_result import ResearchResult
from src.models.db.workflow_checkpoint import WorkflowCheckpoint
from src.repositories.base import BaseRepository

N = 50


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        for model in (ResearchProject, AgentTask, ResearchResult, WorkflowCheckpoint):
            await connection.run_sync(model.__table__.create)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )() as s:
        yield s


def _statements(engine: AsyncEngine) -> list[str]:
    seen: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    return seen


async def _seed(
    session: AsyncSession, org: UUID, count: int = N
) -> list[ResearchProject]:
    projects = [
        ResearchProject(title=f"p{i}", query="q", user_id=uuid4(), organization_id=org)
        for i in range(count)
    ]
    session.add_all(projects)
    await session.flush()
    await session.commit()
    return projects


def _repo(session: AsyncSession) -> BaseRepository[ResearchProject]:
    return BaseRepository(ResearchProject, session)


async def _row(session: AsyncSession, id: UUID) -> ResearchProject:
    session.expire_all()
    return (
        await session.execute(select(ResearchProject).where(ResearchProject.id == id))
    ).scalar_one()


@pytest.mark.asyncio
async def test_bulk_delete_soft_is_bounded_and_sets_audit_fields(
    engine: AsyncEngine, session: AsyncSession
) -> None:
    org = uuid4()
    projects = await _seed(session, org)
    ids = [p.id for p in projects]
    seen = _statements(engine)

    count = await _repo(session).bulk_delete(ids, deleted_by="u1", organization_id=org)

    assert count == N
    assert len(seen) <= 3, seen
    row = await _row(session, ids[0])
    assert row.deleted_at is not None
    assert row.updated_by == "u1"


@pytest.mark.asyncio
async def test_bulk_delete_hard_is_bounded(
    engine: AsyncEngine, session: AsyncSession
) -> None:
    org = uuid4()
    ids = [p.id for p in await _seed(session, org)]
    seen = _statements(engine)

    count = await _repo(session).bulk_delete(ids, soft=False, organization_id=org)

    assert count == N
    assert len(seen) <= 8, seen
    session.expire_all()
    assert (await session.execute(select(ResearchProject))).first() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("soft", [True, False])
async def test_bulk_delete_skips_other_tenant_and_already_deleted(
    session: AsyncSession, soft: bool
) -> None:
    org, other = uuid4(), uuid4()
    mine = await _seed(session, org, 3)
    theirs = await _seed(session, other, 2)
    gone = mine[0]
    gone_id = gone.id
    theirs_ids = [p.id for p in theirs]
    ids = [p.id for p in mine + theirs]
    gone.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    await session.commit()

    count = await _repo(session).bulk_delete(
        [*ids, uuid4()], soft=soft, organization_id=org
    )

    assert count == 2  # mine[1], mine[2] only
    for tid in theirs_ids:
        assert (await _row(session, tid)).deleted_at is None
    if soft:
        assert (await _row(session, gone_id)).updated_by is None


@pytest.mark.asyncio
async def test_bulk_update_is_bounded_and_scoped(
    engine: AsyncEngine, session: AsyncSession
) -> None:
    org, other = uuid4(), uuid4()
    mine = await _seed(session, org)
    theirs = await _seed(session, other, 2)
    theirs_ids = [p.id for p in theirs]
    mine3 = mine[3].id
    updates = [{"id": p.id, "title": f"new{i}"} for i, p in enumerate(mine)]
    updates += [{"id": p.id, "title": "hijack"} for p in theirs]
    updates.append({"title": "no id"})
    seen = _statements(engine)

    count = await _repo(session).bulk_update(
        updates, updated_by="u2", organization_id=org
    )

    assert count == N
    assert len(seen) <= 8, seen
    row = await _row(session, mine3)
    assert row.title == "new3"
    assert row.updated_by == "u2"
    for tid in theirs_ids:
        assert (await _row(session, tid)).title != "hijack"


@pytest.mark.asyncio
async def test_bulk_update_skips_soft_deleted_and_ignores_protected_fields(
    session: AsyncSession,
) -> None:
    org = uuid4()
    a, b = await _seed(session, org, 2)
    b.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    await session.commit()
    a_id, b_id = a.id, b.id

    count = await _repo(session).bulk_update(
        [
            {"id": a_id, "title": "x", "nonexistent": 1},
            {"id": b_id, "title": "y"},
        ],
    )

    assert count == 1
    assert (await _row(session, a_id)).title == "x"
    assert (await _row(session, b_id)).title != "y"
