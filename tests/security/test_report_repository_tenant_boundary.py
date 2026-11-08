"""Org B must never see, search, or delete org A's reports through the repository.

These run against a real (sqlite) database so removing the ``organization_id``
predicate in ``ReportRepository._scoped_query`` makes them fail. Calls pass
``user_id=None`` so the organization predicate is the only thing separating the
tenants.
"""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.models.db.base import Base
from src.models.db.generated_report import GeneratedReport, ReportFormat
from src.repositories.report_repository import ReportFormatRepository, ReportRepository

ORG_A, ORG_B = uuid4(), uuid4()
USER_A, USER_B = uuid4(), uuid4()


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all,
            tables=[GeneratedReport.__table__, ReportFormat.__table__],
        )
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session
    await engine.dispose()


async def _report_a(repo: ReportRepository) -> GeneratedReport:
    return await repo.create_report(
        title="Secret alpha",
        report_type="comprehensive",
        query="alpha query",
        user_id=USER_A,
        organization_id=ORG_A,
        generation_status="completed",
        workflow_id="wf-a",
        is_public=True,
    )


async def test_org_b_cannot_get_org_a_report(session: AsyncSession) -> None:
    repo = ReportRepository(session)
    report = await _report_a(repo)

    assert await repo.get_report_with_formats(report.id, organization_id=ORG_B) is None
    assert await repo.get_by_workflow_id("wf-a", organization_id=ORG_B) is None
    assert (
        await repo.get_report_with_formats(report.id, organization_id=ORG_A)
    ).id == (report.id)


async def test_org_b_cannot_list_org_a_reports(session: AsyncSession) -> None:
    repo = ReportRepository(session)
    await _report_a(repo)

    assert await repo.get_reports_by_status("completed", organization_id=ORG_B) == []
    assert await repo.get_public_reports(organization_id=ORG_B) == []
    assert (
        len(await repo.get_reports_by_status("completed", organization_id=ORG_A)) == 1
    )


async def test_org_b_cannot_search_org_a_reports(session: AsyncSession) -> None:
    repo = ReportRepository(session)
    await _report_a(repo)

    reports, total = await repo.search_reports("alpha", organization_id=ORG_B)
    assert (reports, total) == ([], 0)
    reports, total = await repo.search_reports("alpha", organization_id=ORG_A)
    assert total == 1 and len(reports) == 1


async def test_org_b_cannot_delete_or_update_org_a_report(
    session: AsyncSession,
) -> None:
    repo = ReportRepository(session)
    report = await _report_a(repo)

    assert await repo.delete_report(report.id, organization_id=ORG_B) is False
    assert (
        await repo.update_report_status(report.id, "failed", organization_id=ORG_B)
        is None
    )
    survivor = await repo.get_report_with_formats(report.id, organization_id=ORG_A)
    assert survivor is not None and survivor.generation_status == "completed"


async def test_org_b_cannot_download_org_a_report_content(
    session: AsyncSession,
) -> None:
    # Download goes through ReportFormatRepository's own join predicate, not
    # _scoped_query; pinned here so the whole read path is covered.
    repo = ReportRepository(session)
    format_repo = ReportFormatRepository(session)
    report = await _report_a(repo)
    await format_repo.create_format(
        report.id,
        "html",
        "text/html",
        b"<p>alpha</p>",
        organization_id=ORG_A,
        file_extension=".html",
    )

    assert (
        await format_repo.get_by_report_and_format(
            report.id, "html", organization_id=ORG_B
        )
        is None
    )
    assert (
        await format_repo.get_by_report_and_format(
            report.id, "html", organization_id=ORG_A
        )
        is not None
    )
    with pytest.raises(ValueError):
        await format_repo.create_format(
            report.id, "pdf", "application/pdf", b"x", organization_id=ORG_B
        )
