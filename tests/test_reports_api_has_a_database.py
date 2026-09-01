"""Regression tests for the mounted reports API database wiring."""

import importlib.util
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import BackgroundTasks
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.api.routes.reports import (
    CreateReportRequest,
    ReportResponse,
    generate_report,
    get_report_services,
    get_report_statistics,
)
from src.middleware.tenant_context import TenantContext
from src.models.db.base import Base
from src.models.db.generated_report import GeneratedReport, ReportFormat
from src.repositories.report_repository import (
    ReportFormatRepository,
    ReportRepository,
)
from src.services.report_config import ReportSettings
from src.services.report_storage import ReportStorageService


async def test_report_services_are_bound_to_the_request_session(tmp_path, monkeypatch):
    """The mounted reports factory must build real session-backed services."""
    settings = ReportSettings(report_storage_path=str(tmp_path))
    monkeypatch.setattr(
        "src.api.routes.reports.create_report_settings", lambda: settings
    )

    async with AsyncSession() as session:
        generator, storage_service, report_repo, format_repo = get_report_services(
            session
        )

    assert generator is not None
    assert isinstance(report_repo, ReportRepository)
    assert isinstance(format_repo, ReportFormatRepository)
    assert isinstance(storage_service, ReportStorageService)
    assert report_repo.session is session
    assert format_repo.session is session
    assert storage_service.report_repo is report_repo
    assert storage_service.format_repo is format_repo


async def test_generate_route_persists_real_pending_report(tmp_path, monkeypatch):
    """The mounted route returns the durable row it queues for generation."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all,
            tables=[GeneratedReport.__table__, ReportFormat.__table__],
        )

    settings = ReportSettings(report_storage_path=str(tmp_path))
    monkeypatch.setattr(
        "src.api.routes.reports.create_report_settings", lambda: settings
    )
    user_id = uuid4()
    organization_id = uuid4()
    request = CreateReportRequest(
        title="Durable report",
        query="Which reports are durable?",
        workflow_data={
            "title": "caller cannot replace this",
            "query": "caller cannot replace this",
        },
    )

    async with async_session() as session:
        background_tasks = BackgroundTasks()
        response = await generate_report(
            request,
            background_tasks,
            session,
            TenantContext(str(user_id), str(organization_id)),
        )

        assert isinstance(response, ReportResponse)
        assert response.id != UUID("00000000-0000-0000-0000-000000000000")
        assert response.created_at != "2024-01-01T00:00:00Z"
        assert response.generation_status == "pending"
        assert len(background_tasks.tasks) == 1
        assert background_tasks.tasks[0].args[1] == response.id

        persisted = await session.get(GeneratedReport, response.id)
        assert persisted is not None
        assert persisted.organization_id == organization_id
        assert persisted.user_id == user_id
        assert persisted.title == request.title
        assert persisted.query == request.query

    await engine.dispose()


async def test_statistics_route_uses_the_request_session_and_tenant_scope(
    tmp_path, monkeypatch
):
    """The mounted statistics handler uses the live session-backed factory."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all,
            tables=[GeneratedReport.__table__, ReportFormat.__table__],
        )

    settings = ReportSettings(report_storage_path=str(tmp_path))
    monkeypatch.setattr(
        "src.api.routes.reports.create_report_settings", lambda: settings
    )
    user_id = uuid4()
    organization_id = uuid4()
    report_dir = tmp_path / str(uuid4())
    report_dir.mkdir()
    report_file = report_dir / "report.html"
    report_file.write_bytes(b"request session report")

    async with async_session() as session:
        report_repo = ReportRepository(session)
        report = await report_repo.create_report(
            title="Request-backed report",
            report_type="comprehensive",
            query="request session",
            user_id=user_id,
            organization_id=organization_id,
            generation_status="completed",
            storage_path=str(report_dir),
        )
        report_dir.rename(tmp_path / str(report.id))
        report.storage_path = str(tmp_path / str(report.id))
        await session.commit()

        response = await get_report_statistics(
            user_id=None,
            days=30,
            session=session,
            tenant_context=TenantContext(str(user_id), str(organization_id)),
        )

    await engine.dispose()

    assert response.total_reports == 1
    assert response.storage_statistics["total_files"] == 1


async def test_report_repository_scopes_reads_search_stats_and_deletes_by_tenant():
    """Foreign tenant rows remain indistinguishable from missing report IDs."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all,
            tables=[GeneratedReport.__table__, ReportFormat.__table__],
        )

    first_user = uuid4()
    second_user = uuid4()
    first_org = uuid4()
    second_org = uuid4()
    async with async_session() as session:
        report_repo = ReportRepository(session)
        format_repo = ReportFormatRepository(session)
        first_report = await report_repo.create_report(
            title="First tenant report",
            report_type="comprehensive",
            query="tenant isolation",
            user_id=first_user,
            organization_id=first_org,
            generation_status="completed",
            formats_generated=[],
        )
        second_report = await report_repo.create_report(
            title="Second tenant report",
            report_type="comprehensive",
            query="tenant isolation",
            user_id=second_user,
            organization_id=second_org,
            generation_status="completed",
            formats_generated=[],
        )
        await format_repo.create_format(
            first_report.id,
            "html",
            "text/html",
            b"first tenant",
            file_extension=".html",
            organization_id=first_org,
            user_id=first_user,
        )
        await session.commit()

        assert (
            await report_repo.get_report_with_formats(
                first_report.id,
                organization_id=first_org,
                user_id=first_user,
            )
        ) is not None
        assert (
            await report_repo.get_report_with_formats(
                first_report.id,
                organization_id=second_org,
                user_id=second_user,
            )
        ) is None
        assert (
            await format_repo.get_by_report_and_format(
                first_report.id,
                "html",
                organization_id=second_org,
                user_id=second_user,
            )
        ) is None

        reports, total_count = await report_repo.search_reports(
            "tenant isolation",
            user_id=first_user,
            organization_id=first_org,
        )
        assert [report.id for report in reports] == [first_report.id]
        assert total_count == 1

        stats = await report_repo.get_report_statistics(
            user_id=first_user,
            organization_id=first_org,
        )
        assert stats["total_reports"] == 1

        assert (
            await report_repo.delete_report(
                first_report.id,
                organization_id=second_org,
                user_id=second_user,
            )
            is False
        )
        assert (
            await report_repo.get_report_with_formats(
                first_report.id,
                organization_id=first_org,
                user_id=first_user,
            )
        ) is not None
        assert (
            await report_repo.get_report_with_formats(
                second_report.id,
                organization_id=second_org,
                user_id=second_user,
            )
        ) is not None

    await engine.dispose()


def _load_reports_migration():
    """Load the reports migration without relying on versions being a package."""
    path = next(
        Path(__file__)
        .parents[1]
        .glob("alembic/versions/c4a8d1e2f307_add_generated_report_tables.py")
    )
    spec = importlib.util.spec_from_file_location("reports_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


async def test_reports_migration_matches_models_and_downgrades_cleanly():
    """The new migration creates the declared shape and removes both tables."""
    migration = _load_reports_migration()
    assert migration.down_revision == "7b1e4c9d2a08"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(_run_migration_upgrade, migration)

        def inspect_schema(sync_connection):
            database_inspector = inspect(sync_connection)
            for model in (GeneratedReport, ReportFormat):
                table_name = model.__tablename__
                model_columns = {
                    column.name: column.nullable for column in model.__table__.columns
                }
                database_columns = {
                    column["name"]: column["nullable"]
                    for column in database_inspector.get_columns(table_name)
                }
                assert database_columns == model_columns

                model_indexes = {
                    index.name: tuple(column.name for column in index.columns)
                    for index in model.__table__.indexes
                }
                database_indexes = {
                    index["name"]: tuple(index["column_names"])
                    for index in database_inspector.get_indexes(table_name)
                }
                assert database_indexes == model_indexes

            model_foreign_keys = {
                (
                    foreign_key.parent.name,
                    foreign_key.target_fullname,
                    foreign_key.ondelete,
                )
                for model in (GeneratedReport, ReportFormat)
                for column in model.__table__.columns
                for foreign_key in column.foreign_keys
            }
            database_foreign_keys = {
                (
                    foreign_key["constrained_columns"][0],
                    f"{foreign_key['referred_table']}.{foreign_key['referred_columns'][0]}",
                    foreign_key["options"].get("ondelete"),
                )
                for table_name in ("generated_reports", "report_formats")
                for foreign_key in database_inspector.get_foreign_keys(table_name)
            }
            assert database_foreign_keys == model_foreign_keys

        await connection.run_sync(inspect_schema)
        await connection.run_sync(_run_migration_downgrade, migration)
        await connection.run_sync(
            lambda sync_connection: assert_report_tables_absent(sync_connection)
        )
    await engine.dispose()


def _run_migration_upgrade(sync_connection, migration) -> None:
    """Run one revision inside an Alembic operations context."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    context = MigrationContext.configure(sync_connection)
    with Operations.context(context):
        migration.upgrade()


def _run_migration_downgrade(sync_connection, migration) -> None:
    """Run the matching downgrade inside an Alembic operations context."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    context = MigrationContext.configure(sync_connection)
    with Operations.context(context):
        migration.downgrade()


def assert_report_tables_absent(sync_connection) -> None:
    """Assert the migration removes both report tables."""
    database_inspector = inspect(sync_connection)
    assert not database_inspector.has_table("generated_reports")
    assert not database_inspector.has_table("report_formats")
