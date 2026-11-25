"""Agent monitoring endpoints must only show the caller's own organization."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio

from src.agents.models import AgentResult
from src.api.services.agent_execution_service import (
    AgentExecutionService,
    get_application_agent_execution_service,
)
from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_jwt_service
from src.middleware.tenant_context import TenantContext, get_tenant_context
from src.models.agent_api_models import AgentExecutionRequest, AgentType
from src.models.db.session import get_session

ORG_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ORG_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
AGENT = AgentType.LITERATURE_REVIEW
OTHER_AGENT = AgentType.METHODOLOGY


STATS_PATHS = [
    f"/api/v1/agents/{AGENT.value}/metrics",
    "/api/v1/agents/system/stats",
    "/api/v1/agents/executions/active",
    "/api/v1/agents/performance/comparison",
]


class _JWT:
    """Issues tokens for whichever organization the test is acting as."""

    def __init__(self, current_org: dict[str, str | None]) -> None:
        self.current_org = current_org

    async def validate_token(self, token: str) -> TokenPayload:
        now = datetime.now(UTC)
        return TokenPayload(
            sub="u",
            email="u@example.com",
            organization_id=self.current_org["org"],
            jti="j",
            iat=now,
            exp=now + timedelta(minutes=5),
        )


class _GatedAgent:
    """Blocks until released so an execution stays in flight."""

    def __init__(self, gate: asyncio.Event) -> None:
        self.gate = gate

    async def execute(self, task: Any) -> AgentResult:
        await self.gate.wait()
        return AgentResult(
            task_id=task.id,
            status="success",
            output={"ok": 1},
            confidence=0.9,
            execution_time=0.0,
        )


@pytest_asyncio.fixture
async def env() -> AsyncIterator[tuple[AgentExecutionService, httpx.AsyncClient, Any]]:
    from src.api.main import app

    gate = asyncio.Event()
    service = AgentExecutionService()

    async def get_agent(_t: AgentType) -> _GatedAgent:
        return _GatedAgent(gate)

    service._get_agent_instance = get_agent  # type: ignore[method-assign]
    current_org: dict[str, str | None] = {"org": ORG_A}

    def no_session() -> None:
        raise AssertionError("stats route opened a DB session")

    app.dependency_overrides[get_application_agent_execution_service] = lambda: service
    app.dependency_overrides[get_jwt_service] = lambda: _JWT(current_org)
    app.dependency_overrides[get_session] = no_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://t",
            headers={"Authorization": "Bearer x"},
        ) as client:
            yield service, client, (gate, current_org)
    finally:
        gate.set()
        for dep in (
            get_application_agent_execution_service,
            get_jwt_service,
            get_session,
            get_tenant_context,
        ):
            app.dependency_overrides.pop(dep, None)


async def _until(predicate: Any) -> None:
    async def spin() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(spin(), timeout=5)


async def _run_in_flight(service: AgentExecutionService) -> list[asyncio.Task[Any]]:
    """Start one execution per org (org B uses a different agent type)."""
    tasks = [
        asyncio.create_task(
            service.execute_single_agent(
                AGENT, AgentExecutionRequest(query="q", organization_id=ORG_A)
            )
        ),
        asyncio.create_task(
            service.execute_single_agent(
                OTHER_AGENT, AgentExecutionRequest(query="q", organization_id=ORG_B)
            )
        ),
    ]
    await _until(lambda: len(service.active_executions) >= 2)
    return tasks


@pytest.mark.asyncio
async def test_active_executions_scoped_to_caller_org(env: Any) -> None:
    service, client, (gate, current_org) = env
    tasks = await _run_in_flight(service)
    ids_by_org = {
        ctx.get("organization_id"): ctx["execution_id"]
        for ctx in service.active_executions.values()
    }

    for org, other in ((ORG_A, ORG_B), (ORG_B, ORG_A)):
        current_org["org"] = org
        body = (await client.get("/api/v1/agents/executions/active")).json()
        seen = {e["execution_id"] for e in body["active_executions"]}
        assert seen == {ids_by_org[org]}
        assert ids_by_org[other] not in seen
        assert body["total_active"] == 1

    gate.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_client_cannot_spoof_organization_in_body(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.api.routes import agent_api

    # Authority resolution needs a database; this test is about ownership.
    monkeypatch.setattr(
        agent_api, "_require_agent_execution_authority", lambda *a, **k: None
    )
    from src.api.main import app

    # /execute resolves authority from the database, so it keeps the
    # DB-backed tenant dependency; stand it in for this test only.
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id="u", organization_id=ORG_A
    )
    service, client, (gate, current_org) = env
    current_org["org"] = ORG_A
    post = asyncio.create_task(
        client.post(
            f"/api/v1/agents/{AGENT.value}/execute",
            json={"query": "q", "organization_id": ORG_B},
        )
    )
    await _until(lambda: bool(service.active_executions))
    # Whatever the route returns, the in-flight record is owned by the caller.
    owners = {c.get("organization_id") for c in service.active_executions.values()}
    gate.set()
    await post
    assert owners == {ORG_A}


@pytest.mark.asyncio
async def test_system_stats_and_metrics_scoped_to_caller_org(env: Any) -> None:
    service, client, (gate, current_org) = env
    gate.set()
    await service.execute_single_agent(
        AGENT, AgentExecutionRequest(query="q", organization_id=ORG_B)
    )
    await service.execute_single_agent(
        AGENT, AgentExecutionRequest(query="q", organization_id=ORG_B)
    )

    current_org["org"] = ORG_A
    stats = (await client.get("/api/v1/agents/system/stats")).json()
    assert stats["agent_metrics"][AGENT.value]["total_executions"] == 0
    metrics = (await client.get(f"/api/v1/agents/{AGENT.value}/metrics")).json()
    assert metrics["total_executions"] == 0

    current_org["org"] = ORG_B
    stats = (await client.get("/api/v1/agents/system/stats")).json()
    assert stats["agent_metrics"][AGENT.value]["total_executions"] == 2
    metrics = (await client.get(f"/api/v1/agents/{AGENT.value}/metrics")).json()
    assert metrics["total_executions"] == 2


@pytest.mark.asyncio
async def test_agent_list_execution_total_scoped_to_caller_org(env: Any) -> None:
    service, client, (gate, current_org) = env
    gate.set()
    await service.execute_single_agent(
        AGENT, AgentExecutionRequest(query="q", organization_id=ORG_B)
    )

    current_org["org"] = ORG_A
    assert (await client.get("/api/v1/agents")).json()["total_system_executions"] == 0
    current_org["org"] = ORG_B
    assert (await client.get("/api/v1/agents")).json()["total_system_executions"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("path", STATS_PATHS)
async def test_stats_routes_resolve_tenant_without_db(env: Any, path: str) -> None:
    _service, client, _ = env
    response = await client.get(path)
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", STATS_PATHS)
async def test_stats_routes_require_org_claim(env: Any, path: str) -> None:
    _service, client, (_gate, current_org) = env
    current_org["org"] = None
    response = await client.get(path)
    assert response.status_code == 403, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", STATS_PATHS)
async def test_stats_routes_require_token(env: Any, path: str) -> None:
    _service, client, _ = env
    del client.headers["Authorization"]
    response = await client.get(path)
    assert response.status_code == 401, response.text
