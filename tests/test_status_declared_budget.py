"""The status payload must not present unenforced budget ceilings as limits.

``max_cost_usd`` and ``max_total_tokens`` are declared on the plan but no code
path counts spend, so the status API reports them under a name that says so.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.api.services.direct_execution_service import (
    ExecutionStatus,
    get_application_direct_execution_service,
)
from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_jwt_service
from src.models.db.session import get_session
from tests.test_execution_plan_conformance import _plan

ORG_ID = "11111111-1111-1111-1111-111111111111"
TOKEN = "test-token"


class _JWT:
    async def validate_token(self, token: str) -> TokenPayload:
        now = datetime.now(UTC)
        return TokenPayload(
            sub="user-1",
            email="t@example.com",
            organization_id=ORG_ID,
            jti="jti",
            iat=now,
            exp=now + timedelta(minutes=5),
        )


class _Backend:
    async def get_execution_status(
        self, execution_id: str, *, organization_id: str | None = None
    ) -> ExecutionStatus:
        return ExecutionStatus(
            execution_id=execution_id,
            project_id="00000000-0000-0000-0000-000000000001",
            status="completed",
            execution_plan=_plan(),
        )


async def _noop(*_a: object, **_k: object) -> None:
    return None


@pytest.fixture
def client() -> Iterator[TestClient]:
    from src.api.main import app

    overrides = {
        get_jwt_service: _JWT,
        get_session: lambda: Mock(execute=_noop),
        get_application_direct_execution_service: _Backend,
    }
    app.dependency_overrides.update(overrides)
    try:
        yield TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})
    finally:
        for dep in overrides:
            app.dependency_overrides.pop(dep, None)


def test_status_does_not_present_unenforced_ceilings_as_limits(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/query/execution/execution-1/status")

    assert response.status_code == 200, response.text
    plan = response.json()["execution_plan"]
    assert "max_cost_usd" not in plan["budget"]
    assert "max_total_tokens" not in plan["budget"]
    assert plan["declared_budget_unenforced"] == {
        "max_cost_usd": 1.0,
        "max_total_tokens": 1000,
    }
    # Enforced ceilings stay where they were.
    assert plan["budget"] == {
        "max_parallel_tasks": 2,
        "max_attempts_per_task": 1,
        "task_timeout_seconds": 30,
    }
