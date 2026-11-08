"""cleanup_old_reports must not delete files for rows that were not committed."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.models.db.base import Base
from src.repositories.report_repository import ReportRepository


async def _setup(tmp_path, count=3):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = maker()
    repo = ReportRepository(session)
    org, user = uuid4(), uuid4()
    dirs = []
    for i in range(count):
        d = tmp_path / f"report{i}"
        d.mkdir()
        (d / "out.pdf").write_text("x")
        r = await repo.create_report(
            title=f"r{i}",
            report_type="comprehensive",
            query="q",
            user_id=user,
            organization_id=org,
            generation_status="completed",
            storage_path=str(d),
        )
        r.created_at = datetime.now(UTC) - timedelta(days=200)
        dirs.append(d)
    await session.commit()
    return session, repo, org, dirs


async def test_files_removed_after_commit(tmp_path):
    session, repo, org, dirs = await _setup(tmp_path)
    count, ids = await repo.cleanup_old_reports(dry_run=False, organization_id=org)
    assert count == 3 and len(ids) == 3
    assert not any(d.exists() for d in dirs)
    await session.close()


async def test_dry_run_touches_nothing(tmp_path):
    session, repo, org, dirs = await _setup(tmp_path)
    count, _ = await repo.cleanup_old_reports(dry_run=True, organization_id=org)
    assert count == 3
    assert all(d.exists() for d in dirs)
    await session.close()


async def test_failure_mid_loop_keeps_files_of_uncommitted_rows(tmp_path):
    session, repo, org, dirs = await _setup(tmp_path)
    real = repo.delete_report
    calls = 0

    async def flaky(*a, **kw):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("db blip")
        return await real(*a, **kw)

    with patch.object(repo, "delete_report", flaky), pytest.raises(RuntimeError):
        await repo.cleanup_old_reports(dry_run=False, organization_id=org)
    await session.rollback()
    assert all(d.exists() for d in dirs)
    await session.close()


async def test_commit_failure_keeps_all_files(tmp_path):
    session, repo, org, dirs = await _setup(tmp_path)

    async def boom():
        raise RuntimeError("commit failed")

    with patch.object(session, "commit", boom), pytest.raises(RuntimeError):
        await repo.cleanup_old_reports(dry_run=False, organization_id=org)
    assert all(d.exists() for d in dirs)
    await session.close()
