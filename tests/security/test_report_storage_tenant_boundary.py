"""Adversarial tests for tenant-scoped report storage statistics."""

from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.models.db.base import Base
from src.models.db.generated_report import GeneratedReport, ReportFormat
from src.repositories.report_repository import ReportFormatRepository, ReportRepository
from src.services.report_config import ReportSettings
from src.services.report_storage import ReportStorageService


async def test_storage_statistics_exclude_foreign_and_unsafe_files(tmp_path):
    """Storage totals include only files owned by the requested tenant."""
    storage_root = tmp_path / "reports"
    storage_root.mkdir()
    outside_root = tmp_path / "outside"
    outside_root.mkdir()

    owner_user = uuid4()
    owner_organization = uuid4()
    foreign_user = uuid4()
    foreign_organization = uuid4()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all,
            tables=[GeneratedReport.__table__, ReportFormat.__table__],
        )

    async with async_session() as session:
        report_repo = ReportRepository(session)
        format_repo = ReportFormatRepository(session)
        owner_report = await report_repo.create_report(
            title="Owner report",
            report_type="comprehensive",
            query="owner query",
            user_id=owner_user,
            organization_id=owner_organization,
            generation_status="completed",
            storage_path=None,
        )
        no_file_report = await report_repo.create_report(
            title="Owner report without a file",
            report_type="comprehensive",
            query="no file query",
            user_id=owner_user,
            organization_id=owner_organization,
            generation_status="completed",
            storage_path=None,
        )
        foreign_report = await report_repo.create_report(
            title="Foreign report",
            report_type="comprehensive",
            query="foreign query",
            user_id=foreign_user,
            organization_id=foreign_organization,
            generation_status="completed",
            storage_path=None,
        )
        outside_report = await report_repo.create_report(
            title="Outside-root report",
            report_type="comprehensive",
            query="outside query",
            user_id=owner_user,
            organization_id=owner_organization,
            generation_status="completed",
            storage_path=str(outside_root / "report-files"),
        )

        owner_dir = storage_root / str(owner_report.id)
        foreign_dir = storage_root / str(foreign_report.id)
        outside_dir = outside_root / "report-files"
        owner_dir.mkdir()
        foreign_dir.mkdir()
        outside_dir.mkdir()
        owner_file = owner_dir / "report.html"
        foreign_file = foreign_dir / "report.html"
        outside_file = outside_dir / "report.html"
        owner_file.write_bytes(b"owner")
        foreign_file.write_bytes(b"foreign tenant data")
        outside_file.write_bytes(b"outside root data")
        (owner_dir / "symlink.html").symlink_to(outside_file)

        owner_report.storage_path = str(owner_dir)
        foreign_report.storage_path = str(foreign_dir)
        await session.commit()

        storage_service = ReportStorageService(
            report_repo,
            format_repo,
            ReportSettings(report_storage_path=str(storage_root)),
        )

        stats = await storage_service.get_storage_statistics(
            organization_id=owner_organization,
            user_id=owner_user,
        )

    await engine.dispose()

    assert stats["total_reports"] == 3
    assert stats["total_files"] == 1
    assert stats["total_storage_bytes"] == owner_file.stat().st_size
    assert stats["total_storage_mb"] == 0.0
    assert no_file_report.storage_path is None
    assert outside_report.storage_path == str(outside_dir)
