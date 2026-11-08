"""GET /api/v1/reports must page by offset and report the real total."""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_jwt_service
from src.middleware.tenant_context import TenantContext, get_tenant_context
from src.models.db.base import Base
from src.models.db.generated_report import GeneratedReport, ReportFormat
from src.models.db.session import get_session
from src.repositories.report_repository import ReportRepository

USER_ID, ORG_ID = uuid4(), uuid4()
TOKEN = "test-token"


class _Audit:
    async def log_event(self, **event: Any) -> str:
        return "audit-event"

    async def flush_buffer(self) -> None:
        return None


class _JWT:
    async def validate_token(self, token: str) -> TokenPayload:
        now = datetime.now(UTC)
        return TokenPayload(
            sub=str(USER_ID),
            email="t@example.com",
            organization_id=str(ORG_ID),
            jti="jti",
            iat=now,
            exp=now + timedelta(minutes=5),
        )


@pytest.fixture
def client() -> Iterator[TestClient]:
    from src.api.main import app

    async def session() -> AsyncIterator[AsyncSession]:
        yield AsyncMock(spec=AsyncSession)

    async def tenant() -> TenantContext:
        return TenantContext(user_id=str(USER_ID), organization_id=str(ORG_ID))

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_tenant_context] = tenant
    app.dependency_overrides[get_jwt_service] = _JWT
    missing = object()
    previous = getattr(app.state, "audit_logger", missing)
    app.state.audit_logger = _Audit()
    try:
        yield TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})
    finally:
        for dependency in (get_session, get_tenant_context, get_jwt_service):
            app.dependency_overrides.pop(dependency, None)
        if previous is missing:
            delattr(app.state, "audit_logger")
        else:
            app.state.audit_logger = previous


def _row() -> MagicMock:
    row = MagicMock(
        id=uuid4(),
        title="t",
        query="q",
        report_type="comprehensive",
        generation_status="completed",
        word_count=1,
        page_count=1,
        quality_score=0.5,
        confidence_score=0.5,
        generation_time_seconds=1.0,
        formats=[],
    )
    row.created_at.isoformat.return_value = "2024-01-01T00:00:00Z"
    return row


def _get(client: TestClient, page: int, page_size: int, rows: int, total: int) -> Any:
    storage = MagicMock()
    storage.list_user_reports = AsyncMock(return_value=[_row() for _ in range(rows)])
    storage.count_user_reports = AsyncMock(return_value=total)
    with patch(
        "src.api.routes.reports.get_report_services",
        return_value=(MagicMock(), storage, MagicMock(), MagicMock()),
    ):
        response = client.get(f"/api/v1/reports?page={page}&page_size={page_size}")
    return response, storage


def test_page_two_is_requested_with_an_offset_and_reports_the_real_total(
    client: TestClient,
) -> None:
    response, storage = _get(client, page=2, page_size=10, rows=10, total=25)

    assert response.status_code == 200
    kwargs = storage.list_user_reports.await_args.kwargs
    assert kwargs["offset"] == 10
    assert kwargs["limit"] == 10
    assert kwargs["organization_id"] == ORG_ID
    body = response.json()
    assert body["total_count"] == 25
    assert body["page"] == 2
    assert body["has_more"] is True


def test_last_page_has_no_more(client: TestClient) -> None:
    response, _ = _get(client, page=3, page_size=10, rows=5, total=25)

    body = response.json()
    assert len(body["reports"]) == 5
    assert body["total_count"] == 25
    assert body["has_more"] is False


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


async def test_repository_offsets_pages_and_counts_only_the_callers_tenant(
    session: AsyncSession,
) -> None:
    repo = ReportRepository(session)
    other_org = uuid4()
    for i in range(5):
        await repo.create_report(
            title=f"mine {i}",
            report_type="comprehensive",
            query="q",
            user_id=USER_ID,
            organization_id=ORG_ID,
            generation_status="completed",
            created_at=datetime(2024, 1, 1 + i, tzinfo=UTC),
        )
    for i in range(3):
        await repo.create_report(
            title=f"theirs {i}",
            report_type="comprehensive",
            query="q",
            user_id=USER_ID,
            organization_id=other_org,
            generation_status="completed",
        )

    first = await repo.get_by_user_id(USER_ID, 2, organization_id=ORG_ID)
    second = await repo.get_by_user_id(USER_ID, 2, organization_id=ORG_ID, offset=2)
    total = await repo.count_by_user_id(USER_ID, organization_id=ORG_ID)

    assert [r.title for r in first] == ["mine 4", "mine 3"]
    assert [r.title for r in second] == ["mine 2", "mine 1"]
    assert total == 5
    assert await repo.count_by_user_id(USER_ID, "failed", organization_id=ORG_ID) == 0
