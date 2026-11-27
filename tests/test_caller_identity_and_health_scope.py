"""Caller identity comes from the token, and agent health is tenant-scoped."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api.services.agent_execution_service import AgentExecutionService
from src.api.services.direct_execution_service import ExecutionStatus
from src.api.services.research_kernel import get_application_research_kernel
from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_jwt_service
from src.middleware.tenant_context import TenantContext, get_tenant_context
from src.models.agent_api_models import AgentExecutionResponse, AgentType

TOKEN = "test-token"
ORG = "11111111-1111-1111-1111-111111111111"
ORG_OTHER = "22222222-2222-2222-2222-222222222222"
AGENT = AgentType.LITERATURE_REVIEW


class _JWT:
    async def validate_token(self, token: str) -> TokenPayload:
        now = datetime.now(UTC)
        return TokenPayload(
            sub="token-user",
            email="t@example.com",
            organization_id=ORG,
            jti="jti",
            iat=now,
            exp=now + timedelta(minutes=5),
        )


class _Kernel:
    def __init__(self) -> None:
        self.projects: list[Any] = []

    async def start_research_execution(self, project, context, **kwargs):
        self.projects.append(project)
        return "execution-1"

    async def get_execution_status(self, execution_id, *, organization_id=None):
        return ExecutionStatus(
            execution_id="execution-1",
            project_id="00000000-0000-0000-0000-000000000001",
            status="running",
            supervisor_type="research",
            started_at=datetime(2026, 7, 26, tzinfo=UTC),
        )


@pytest.fixture
def kernel() -> Iterator[tuple[_Kernel, TestClient]]:
    from src.api.main import app

    k = _Kernel()
    app.dependency_overrides[get_jwt_service] = _JWT
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id="token-user", organization_id=ORG
    )
    app.dependency_overrides[get_application_research_kernel] = lambda: k
    try:
        yield k, TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})
    finally:
        for dep in (
            get_jwt_service,
            get_tenant_context,
            get_application_research_kernel,
        ):
            app.dependency_overrides.pop(dep, None)


def test_project_owner_is_the_token_user_not_the_body(kernel: Any) -> None:
    k, client = kernel
    response = client.post(
        "/api/v1/query/research", json={"query": "q", "user_id": "victim"}
    )
    assert response.status_code == 200, response.text
    assert k.projects[0].user_id == "token-user"


def test_project_owner_is_the_token_user_when_body_omits_it(kernel: Any) -> None:
    k, client = kernel
    client.post("/api/v1/query/research", json={"query": "q"})
    assert k.projects[0].user_id == "token-user"


@pytest.mark.asyncio
async def test_agent_health_does_not_leak_other_tenants_load() -> None:
    service = AgentExecutionService()
    service.active_executions["x"] = {"status": "pending", "organization_id": ORG_OTHER}
    await service._update_agent_metrics(
        AGENT,
        AgentExecutionResponse.model_construct(
            execution_time_seconds=1.0, quality_score=0.0
        ),
        False,
        organization_id=ORG_OTHER,
    )

    mine = await service.get_agent_health(AGENT, organization_id=ORG)

    assert mine.queue_length == 0
    assert mine.resource_utilization == 0
    theirs = await service.get_agent_health(AGENT, organization_id=ORG_OTHER)
    assert theirs.queue_length == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("flush_fails", [False, True])
async def test_gdpr_erase_does_not_claim_memory_purged_when_none_attached(
    flush_fails: bool,
) -> None:
    from types import SimpleNamespace
    from uuid import uuid4

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from src.api.routes import users
    from src.models.db.audit_log import AuditEventType
    from src.models.db.generated_report import GeneratedReport
    from src.models.db.research_project import ResearchProject
    from src.models.db.research_result import ResearchResult
    from src.models.db.similarity_check import EgressEvent
    from src.models.db.uploaded_document import UploadedDocument, UsageLedger
    from src.models.db.user import User
    from src.models.db.workflow_checkpoint import WorkflowCheckpoint
    from src.security.audit_logger import AuditPersistenceError

    class _Jwt:
        async def revoke_all_user_tokens(self, user_id: str) -> None:
            return None

    class _Audit:
        def __init__(self) -> None:
            self.events: list[dict] = []
            self.flushed = False

        async def log_event(self, **event):
            self.events.append(event)
            return "id"

        async def flush_buffer(self) -> None:
            if flush_fails:
                raise AuditPersistenceError("audit store down")
            self.flushed = True

    audit = _Audit()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for model in (
            User,
            ResearchProject,
            GeneratedReport,
            ResearchResult,
            WorkflowCheckpoint,
            UploadedDocument,
            UsageLedger,
            EgressEvent,
        ):
            await conn.run_sync(model.__table__.create)
    user_id = uuid4()
    async with AsyncSession(engine) as session:
        session.add(
            User(
                id=user_id,
                email="gone@example.com",
                username="gone",
                hashed_password="x",
            )
        )
        await session.commit()
    async with AsyncSession(engine) as session:
        body = await users.delete_user_gdpr(
            user_id,
            db=session,
            memory_system=None,
            audit_logger=audit,  # type: ignore[arg-type]
            current_user=SimpleNamespace(id=user_id, is_superuser=False),  # type: ignore[arg-type]
            jwt_service=_Jwt(),  # type: ignore[arg-type]
        )
    async with AsyncSession(engine) as session:
        assert await session.get(User, user_id) is None
    await engine.dispose()

    # The erasure is committed either way, so the response must not fail.
    assert body["audit_recorded"] is (not flush_fails)

    assert body["memory_purged"] is False
    assert body["deleted"]["memory"] is None
    assert body["not_erased"]["memory"].startswith("not_configured")
    assert set(body["not_erased"]) == {"memory", "audit_logs", "agent_runs"}
    (event,) = audit.events
    assert event["event_type"] == AuditEventType.ACCOUNT_DELETED
    assert event["user_id"] == user_id
    assert audit.flushed is (not flush_fails)


@pytest.mark.asyncio
async def test_system_health_label_ignores_other_tenants_failures() -> None:
    service = AgentExecutionService()
    failed = AgentExecutionResponse.model_construct(
        execution_time_seconds=1.0, quality_score=0.0
    )
    for agent in AgentType:
        await service._update_agent_metrics(
            agent, failed, False, organization_id=ORG_OTHER
        )
        await service._update_agent_metrics(agent, failed, True, organization_id=ORG)

    other = await service.get_service_stats(organization_id=ORG_OTHER)
    mine = await service.get_service_stats(organization_id=ORG)

    assert other["system_health"] == "unhealthy"
    assert mine["system_health"] == "healthy"
