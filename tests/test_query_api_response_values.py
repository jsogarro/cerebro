"""POST /api/v1/query/research must not invent estimates it does not have."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api.services.direct_execution_service import ExecutionStatus
from src.api.services.research_kernel import get_application_research_kernel
from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_jwt_service
from src.middleware.tenant_context import TenantContext, get_tenant_context

TOKEN = "test-token"
ORG = "11111111-1111-1111-1111-111111111111"


class _JWT:
    async def validate_token(self, token: str) -> TokenPayload:
        assert token == TOKEN
        now = datetime.now(UTC)
        return TokenPayload(
            sub="user-1",
            email="t@example.com",
            organization_id=ORG,
            jti="jti",
            iat=now,
            exp=now + timedelta(minutes=5),
        )


class _Kernel:
    def __init__(self, routing_decision: dict[str, Any] | None) -> None:
        self.status = ExecutionStatus(
            execution_id="execution-1",
            project_id="00000000-0000-0000-0000-000000000001",
            status="running",
            routing_decision=routing_decision,
            supervisor_type="research",
            started_at=datetime(2026, 7, 26, tzinfo=UTC),
        )

    async def start_research_execution(self, project, context, **kwargs):
        return "execution-1"

    async def get_execution_status(self, execution_id, *, organization_id=None):
        return self.status


@pytest.fixture
def post(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from src.api.main import app

    recorded: list[float | None] = []
    monkeypatch.setattr(
        "src.api.routes.query_api.set_llm_request_estimated_cost", recorded.append
    )
    app.dependency_overrides[get_jwt_service] = _JWT
    # The DB session that sets the Postgres tenant context is the only stub.
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id="user-1", organization_id=ORG
    )

    def run(routing_decision: dict[str, Any] | None) -> tuple[dict[str, Any], list]:
        app.dependency_overrides[get_application_research_kernel] = lambda: _Kernel(
            routing_decision
        )
        response = TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}).post(
            "/api/v1/query/research", json={"query": "Compare two retrieval methods"}
        )
        assert response.status_code == 200, response.text
        return response.json(), recorded

    try:
        yield run
    finally:
        app.dependency_overrides.pop(get_jwt_service, None)
        app.dependency_overrides.pop(get_tenant_context, None)
        app.dependency_overrides.pop(get_application_research_kernel, None)


def test_values_are_null_without_a_routing_decision(post: Any) -> None:
    body, recorded = post(None)
    assert body["estimated_cost"] is None
    assert body["estimated_quality"] is None
    assert body["confidence"] is None
    assert body["routing_time_ms"] is None
    assert body["selected_agents"] == []
    assert recorded == []


def test_values_come_from_the_routing_decision(post: Any) -> None:
    body, recorded = post(
        {
            "estimated_cost": 0.123,
            "estimated_quality": 0.61,
            "agent_allocation": {"worker_types": ["literature_review", "synthesis"]},
        }
    )
    assert body["estimated_cost"] == 0.123
    assert body["estimated_quality"] == 0.61
    assert body["selected_agents"] == ["literature_review", "synthesis"]
    assert recorded == [0.123]
    assert body["confidence"] is None
    assert body["routing_time_ms"] is None
