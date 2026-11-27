"""Entry points must carry the caller's tenant and authority to the kernel."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api.services.direct_execution_service import ExecutionStatus
from src.api.services.execution_authority_resolver import (
    ExecutionAuthorityRequiredError,
)
from src.api.services.research_kernel import get_application_research_kernel
from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_jwt_service
from src.middleware.tenant_context import TenantContext, get_tenant_context

TOKEN = "test-token"
ORG = "11111111-1111-1111-1111-111111111111"
AUTHORITY = {"authority_id": "auth-1", "authority_version": "1"}


class _JWT:
    async def validate_token(self, token: str) -> TokenPayload:
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
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def start_research_execution(self, project, context=None, **kwargs):
        if self.error is not None:
            raise self.error
        self.calls.append(kwargs)
        return "execution-1"

    async def get_execution_status(self, execution_id, *, organization_id=None):
        return ExecutionStatus(
            execution_id=execution_id,
            project_id="00000000-0000-0000-0000-000000000001",
            status="running",
            started_at=datetime(2026, 7, 26, tzinfo=UTC),
        )


@pytest.fixture
def client() -> Iterator[tuple[TestClient, dict[str, _Kernel]]]:
    from src.api.main import app

    holder: dict[str, _Kernel] = {"kernel": _Kernel()}
    app.dependency_overrides[get_jwt_service] = _JWT
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id="user-1", organization_id=ORG
    )
    app.dependency_overrides[get_application_research_kernel] = lambda: holder["kernel"]
    try:
        yield (
            TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}),
            holder,
        )
    finally:
        for dep in (
            get_jwt_service,
            get_tenant_context,
            get_application_research_kernel,
        ):
            app.dependency_overrides.pop(dep, None)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/query/analyze", {"query": "Analyse retrieval methods"}),
        ("/api/v1/query/synthesize", {"query": "Synthesise retrieval methods"}),
    ],
)
def test_missing_authority_is_422_not_500(client, path, body) -> None:
    http, holder = client
    holder["kernel"] = _Kernel(
        ExecutionAuthorityRequiredError("execution authority is required")
    )
    response = http.post(path, json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EXECUTION_AUTHORITY_REQUIRED"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/query/literature?query=retrieval+augmented+generation",
        "/api/v1/query/methodology?query=retrieval+augmented+generation",
        "/api/v1/query/comparison?query=retrieval+augmented+generation",
    ],
)
def test_convenience_endpoints_forward_authority(client, path) -> None:
    http, holder = client
    response = http.post(path, json={"authority_reference": AUTHORITY})
    assert response.status_code == 200, response.text
    ref = holder["kernel"].calls[0]["authority_reference"]
    assert ref.authority_id == "auth-1"
    assert holder["kernel"].calls[0]["organization_id"] == ORG
