"""Unit and route tests for per-organization run admission caps."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.api.services.direct_execution_service import DirectExecutionService
from src.api.services.execution_authority_resolver import (
    MappingExecutionAuthorityResolver,
)
from src.api.services.research_kernel import (
    compose_application_research_kernel,
    get_application_research_kernel,
)
from src.auth.models import TokenPayload
from src.core.config import Settings, settings
from src.core.contracts import Run, RunStatus
from src.middleware.auth_middleware import get_jwt_service
from src.middleware.tenant_context import TenantContext, get_tenant_context
from src.models.db.artifact import AgentArtifact
from src.models.db.base import Base
from src.models.db.capability import AgentCapabilityGrant
from src.models.db.claim_support import AgentClaimSupport
from src.models.db.evidence import AgentEvidence
from src.models.db.run_config_snapshot import AgentRunConfigSnapshot
from src.models.db.run_event import AgentRunEvent, AgentRunEventOutbox
from src.models.db.run_lifecycle import AgentRun, AgentRunTask, AgentTaskAttempt
from src.models.execution_authority import ExecutionAuthorityReference
from src.repositories.run_lifecycle_repository import (
    RunCapExceededError,
    RunLifecycleRepository,
)
from tests.integration.crash_fixtures import (
    _RoutingDecisionStub,
    make_binding,
    make_project,
)

NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC)
ORG_A = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
ORG_B = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
TOKEN = "cap-route-test-token"

_ADMISSION_TABLES = [
    AgentRun.__table__,
    AgentRunTask.__table__,
    AgentTaskAttempt.__table__,
    AgentRunConfigSnapshot.__table__,
    AgentRunEvent.__table__,
    AgentRunEventOutbox.__table__,
    AgentCapabilityGrant.__table__,
    AgentArtifact.__table__,
    AgentEvidence.__table__,
    AgentClaimSupport.__table__,
]


def _make_run(
    run_id: str,
    *,
    org_id: uuid.UUID = ORG_A,
    status: RunStatus = RunStatus.CREATED,
    created_at: datetime = NOW,
) -> Run:
    return Run(
        run_id=run_id,
        tenant_id=str(org_id),
        workflow_definition_id="research",
        workflow_definition_version="1",
        routing_policy_id="default",
        routing_policy_version="1",
        idempotency_key=f"key-{run_id}",
        requested_by="user-1",
        status=status,
        created_at=created_at,
        updated_at=created_at,
    )


def _seed_row(
    run_id: str,
    *,
    org_id: uuid.UUID = ORG_A,
    status: RunStatus = RunStatus.RUNNING,
    created_at: datetime = NOW,
) -> AgentRun:
    return AgentRun(
        run_id=run_id,
        tenant_id=str(org_id),
        organization_id=org_id,
        workflow_definition_id="research",
        workflow_definition_version="1",
        routing_policy_id="default",
        routing_policy_version="1",
        idempotency_key=f"key-{run_id}",
        requested_by="user-1",
        status=status.value,
        created_at=created_at,
        updated_at=created_at,
    )


@pytest_asyncio.fixture(name="sqlite_factory")
async def sqlite_factory_fixture() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_ADMISSION_TABLES)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


def _make_service(
    session_factory: async_sessionmaker[AsyncSession],
    bindings: dict[tuple[str, str], Any],
) -> DirectExecutionService:
    router = AsyncMock()
    router.route.return_value = _RoutingDecisionStub()
    bridge = Mock()
    bridge.admit_execution_plan = Mock()
    bridge.execute_execution_plan = AsyncMock(
        return_value=Mock(output={"result": "ok"}, workers_used=1)
    )
    return DirectExecutionService(
        masr_router=router,
        supervisor_bridge=bridge,
        supervisor_factory=Mock(),
        session_factory=session_factory,
        execution_authority_resolver=MappingExecutionAuthorityResolver(bindings),
    )


def test_settings_defaults_for_org_admission_caps() -> None:
    fresh = Settings(SECRET_KEY="x" * 32)
    assert fresh.RUN_MAX_CONCURRENT_PER_ORG == 5
    assert fresh.RUN_MAX_DAILY_PER_ORG == 100
    assert not hasattr(fresh, "RUN_MAX_CONCURRENT_PER_USER")


@pytest.mark.asyncio
async def test_admission_refused_at_org_concurrent_cap(
    sqlite_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "RUN_MAX_CONCURRENT_PER_ORG", 1)
    monkeypatch.setattr(settings, "RUN_MAX_DAILY_PER_ORG", 100)

    async with sqlite_factory() as session:
        session.add(_seed_row("existing-active", status=RunStatus.RUNNING))
        await session.commit()

    binding = make_binding(
        "refused-run", authority_id="auth-refused", org_id=str(ORG_A)
    )
    service = _make_service(sqlite_factory, {("auth-refused", "1"): binding})
    reference = ExecutionAuthorityReference(
        authority_id="auth-refused", authority_version="1"
    )

    with pytest.raises(RunCapExceededError) as exc_info:
        await service.start_research_execution(
            make_project(), authority_reference=reference, organization_id=str(ORG_A)
        )

    assert exc_info.value.code == "run_concurrency_cap_exceeded"
    async with sqlite_factory() as session:
        rows = (await session.scalars(select(AgentRun.run_id))).all()
    assert rows == ["existing-active"]
    await service.close()


@pytest.mark.asyncio
async def test_terminal_runs_not_counted(
    sqlite_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with sqlite_factory() as session:
        for idx, terminal in enumerate(
            (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED)
        ):
            session.add(
                _seed_row(
                    f"done-{idx}", status=terminal, created_at=NOW - timedelta(hours=1)
                )
            )
        await session.commit()

    async with sqlite_factory() as session:
        repo = RunLifecycleRepository(session)
        await repo.create_run(_make_run("fresh-run"), organization_id=ORG_A)
        await repo.enforce_admission_caps(
            ORG_A,
            max_concurrent_per_org=1,
            max_daily_per_org=100,
            now=NOW,
        )
        await session.commit()

    async with sqlite_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            "fresh-run", organization_id=ORG_A
        )
        assert row is not None


@pytest.mark.asyncio
async def test_daily_window_24h(
    sqlite_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with sqlite_factory() as session:
        # Exactly 24h old must still fall inside the 24h rolling window (>=).
        session.add(
            _seed_row(
                "boundary-24h",
                status=RunStatus.SUCCEEDED,
                created_at=NOW - timedelta(hours=24),
            )
        )
        await session.commit()

    async with sqlite_factory() as session:
        repo = RunLifecycleRepository(session)
        await repo.create_run(_make_run("over-daily"), organization_id=ORG_A)
        with pytest.raises(RunCapExceededError) as exc_info:
            await repo.enforce_admission_caps(
                ORG_A,
                max_concurrent_per_org=10,
                max_daily_per_org=1,
                now=NOW,
            )
        await session.rollback()

    assert exc_info.value.code == "run_daily_cap_exceeded"
    async with sqlite_factory() as session:
        rows = (await session.scalars(select(AgentRun.run_id))).all()
    assert rows == ["boundary-24h"]


@pytest.mark.asyncio
async def test_25h_old_not_counted(
    sqlite_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with sqlite_factory() as session:
        session.add(
            _seed_row(
                "old-25h",
                status=RunStatus.SUCCEEDED,
                created_at=NOW - timedelta(hours=25),
            )
        )
        await session.commit()

    async with sqlite_factory() as session:
        repo = RunLifecycleRepository(session)
        await repo.create_run(_make_run("new-today"), organization_id=ORG_A)
        await repo.enforce_admission_caps(
            ORG_A,
            max_concurrent_per_org=10,
            max_daily_per_org=1,
            now=NOW,
        )
        await session.commit()

    async with sqlite_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            "new-today", organization_id=ORG_A
        )
        assert row is not None


@pytest.mark.asyncio
async def test_other_org_runs_dont_count(
    sqlite_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with sqlite_factory() as session:
        for idx in range(3):
            session.add(
                _seed_row(
                    f"org-b-{idx}",
                    org_id=ORG_B,
                    status=RunStatus.RUNNING,
                    created_at=NOW,
                )
            )
        await session.commit()

    async with sqlite_factory() as session:
        repo = RunLifecycleRepository(session)
        await repo.create_run(
            _make_run("org-a-run", org_id=ORG_A), organization_id=ORG_A
        )
        await repo.enforce_admission_caps(
            ORG_A,
            max_concurrent_per_org=1,
            max_daily_per_org=1,
            now=NOW,
        )
        await session.commit()

    async with sqlite_factory() as session:
        row = await RunLifecycleRepository(session).get_run(
            "org-a-run", organization_id=ORG_A
        )
        assert row is not None


class _JWT:
    async def validate_token(self, token: str) -> TokenPayload:
        assert token == TOKEN
        now = datetime.now(UTC)
        return TokenPayload(
            sub="user-1",
            email="t@example.com",
            organization_id=str(ORG_A),
            jti="jti",
            iat=now,
            exp=now + timedelta(minutes=5),
        )


@pytest.fixture
def route_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from src.api.main import app

    app.dependency_overrides[get_jwt_service] = _JWT
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id="user-1", organization_id=str(ORG_A)
    )

    def build(service: DirectExecutionService) -> TestClient:
        kernel = compose_application_research_kernel(service)
        app.dependency_overrides[get_application_research_kernel] = lambda: kernel
        return TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})

    try:
        yield build
    finally:
        app.dependency_overrides.pop(get_jwt_service, None)
        app.dependency_overrides.pop(get_tenant_context, None)
        app.dependency_overrides.pop(get_application_research_kernel, None)


@pytest.mark.parametrize(
    ("seeded_status", "max_concurrent", "max_daily", "expected_code"),
    [
        (RunStatus.RUNNING, 1, 100, "run_concurrency_cap_exceeded"),
        (RunStatus.SUCCEEDED, 10, 1, "run_daily_cap_exceeded"),
    ],
)
def test_query_route_returns_429_on_cap_exceeded(
    route_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    seeded_status: RunStatus,
    max_concurrent: int,
    max_daily: int,
    expected_code: str,
    tmp_path: Any,
) -> None:
    import asyncio

    db_path = tmp_path / "route_caps.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=_ADMISSION_TABLES)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add(
                _seed_row(
                    "seeded-run",
                    status=seeded_status,
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()

    asyncio.run(_setup())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "RUN_MAX_CONCURRENT_PER_ORG", max_concurrent)
    monkeypatch.setattr(settings, "RUN_MAX_DAILY_PER_ORG", max_daily)

    binding = make_binding("route-run", authority_id="auth-route", org_id=str(ORG_A))
    service = _make_service(factory, {("auth-route", "1"): binding})
    client = route_client(service)

    try:
        response = client.post(
            "/api/v1/query/research",
            json={
                "query": "Check cap refusal status code",
                "authority_reference": {
                    "authority_id": "auth-route",
                    "authority_version": "1",
                },
            },
        )
        assert response.status_code == 429, response.text
        assert response.json()["error"]["code"] == expected_code
    finally:
        asyncio.run(service.close())
        asyncio.run(engine.dispose())
