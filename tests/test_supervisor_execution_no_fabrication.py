"""POST /api/v1/supervisors/{type}/execute must not fabricate work or scores.

Drives the real app (global auth middleware included) and the real
SupervisorCoordinationService; only the real-executor boundary is stubbed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.api.services.supervisor_coordination_service import (
    SupervisorCoordinationService,
)
from src.auth.models import TokenPayload
from src.middleware.auth_middleware import get_jwt_service
from src.models.db.session import get_session

CANNED = "Collaborative result from"


class _JWT:
    async def validate_token(self, token: str) -> TokenPayload:
        now = datetime.now(UTC)
        return TokenPayload(
            sub="user-123",
            email="user@example.com",
            roles=["researcher"],
            permissions=["read:projects"],
            organization_id="org-123",
            jti="jti-123",
            iat=now,
            exp=now + timedelta(minutes=15),
        )


class _Executor:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome

    async def execute_with_workers(self, *args: Any, **kwargs: Any) -> Any:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@contextmanager
def _post(outcome: Any) -> Iterator[Any]:
    from src.api.main import app

    service = SupervisorCoordinationService()
    service._real_executor = _Executor(outcome)
    missing = object()
    previous = getattr(app.state, "supervisor_coordination_service", missing)
    app.state.supervisor_coordination_service = service

    async def _session() -> AsyncIterator[Mock]:
        yield Mock()

    app.dependency_overrides[get_jwt_service] = lambda: _JWT()
    app.dependency_overrides[get_session] = _session
    client = TestClient(app)
    try:
        yield lambda: client.post(
            "/api/v1/supervisors/research/execute",
            json={"query": "compare vector databases"},
            headers={"Authorization": "Bearer t"},
        )
    finally:
        client.close()
        app.dependency_overrides.pop(get_jwt_service, None)
        app.dependency_overrides.pop(get_session, None)
        if previous is missing:
            del app.state.supervisor_coordination_service
        else:
            app.state.supervisor_coordination_service = previous


def test_raising_executor_reports_failed_without_canned_result() -> None:
    with _post(RuntimeError("provider down")) as call:
        response = call()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert CANNED not in response.text
    assert body["result"] is None
    assert body["quality_score"] is None


def test_executor_failure_dict_reports_failed() -> None:
    failure = {"status": "failed", "message": "no result", "errors": ["boom"]}
    with _post(failure) as call:
        body = call().json()

    assert body["status"] == "failed"
    assert body["result"] is None


def test_quality_score_is_executor_reported_and_deterministic() -> None:
    result = {"summary": "ok", "_execution_metadata": {"quality_score": 0.42}}
    with _post(result) as call:
        first, second = call().json(), call().json()

    assert first["status"] == "completed"
    assert first["quality_score"] == second["quality_score"] == pytest.approx(0.42)


def test_quality_score_is_none_when_executor_reports_none() -> None:
    with _post({"summary": "ok", "_execution_metadata": {}}) as call:
        body = call().json()

    assert body["status"] == "completed"
    assert body["quality_score"] is None
