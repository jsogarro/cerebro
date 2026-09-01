from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.api.services.direct_execution_service import DirectExecutionService


def _row(**values: object) -> Any:
    now = datetime(2026, 8, 4, 12, tzinfo=UTC).replace(tzinfo=None)
    defaults = {
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
        "status_reason": None,
        "cancellation_requested_at": None,
        "cancellation_reason": None,
    }
    return SimpleNamespace(**(defaults | values))


@pytest.mark.asyncio
async def test_rehydrate_restores_contract_identity_and_grants_with_tenant_scope() -> (
    None
):
    org = "00000000-0000-0000-0000-0000000000ef"
    run = _row(
        run_id="run-45b",
        tenant_id=org,
        organization_id=org,
        workflow_definition_id="workflow",
        workflow_definition_version="1",
        routing_policy_id="policy",
        routing_policy_version="1",
        idempotency_key="idem",
        requested_by="user",
        status="running",
        started_at=datetime(2026, 8, 4, 12),
    )
    task = _row(
        run_id="run-45b",
        task_id="task-45b",
        task_key="execution-45b",
        task_type="execution_plan",
        objective="objective",
        idempotency_key="task-idem",
        dependency_ids=[],
        assigned_worker_type=None,
        input={"project_id": "project"},
        status="running",
        started_at=datetime(2026, 8, 4, 12),
    )
    attempt = _row(
        run_id="run-45b",
        task_id="task-45b",
        attempt_id="attempt-45b",
        ordinal=1,
        idempotency_key="attempt-idem",
        executor_id=None,
        status="running",
        started_at=datetime(2026, 8, 4, 12),
        journaled_result={"final_output": {"ok": True}},
    )
    grant = _row(
        grant_id="grant-45b",
        run_id="run-45b",
        task_id="task-45b",
        capability_scope="scope",
        tool_name="search",
        tool_versions=["1"],
        sensitivity="read_only",
        max_input_trust="external_untrusted",
        requires_approval=False,
        issued_at=datetime(2026, 8, 4, 12),
        expires_at=datetime(2026, 8, 4, 13),
    )
    repo = Mock(
        session=Mock(),
        get_tasks_for_run=AsyncMock(return_value=[task]),
        get_attempts_for_task=AsyncMock(return_value=[attempt]),
    )
    grants = Mock(list_grants_for_task=AsyncMock(return_value=[grant]))
    service = DirectExecutionService(session_factory=Mock())

    with patch(
        "src.api.services.direct_execution_service.CapabilityRepository",
        return_value=grants,
    ):
        status = await service._rehydrate_execution(repo, run)

    assert status is not None
    assert status._run is not None
    assert status._task is not None
    assert status._attempt is not None
    assert status._run.run_id == "run-45b"
    assert status._task.task_id == "task-45b"
    assert status._attempt.attempt_id == "attempt-45b"
    assert [item.grant_id for item in status.capability_grants] == ["grant-45b"]
    assert DirectExecutionService._durable_agent_task_context(status) == {
        "run_id": "run-45b",
        "task_id": "task-45b",
        "attempt_id": "attempt-45b",
        "organization_id": org,
        "capability_grants": status.capability_grants,
    }
    assert status.final_output == {"ok": True}
    assert status.started_at.tzinfo is UTC

    grants.list_grants_for_task.assert_awaited_once_with(
        "run-45b", "task-45b", organization_id=org
    )


@pytest.mark.asyncio
async def test_rehydrate_does_not_cross_tenant_scope() -> None:
    run = _row(
        run_id="run-other",
        tenant_id="tenant-other",
        organization_id="00000000-0000-0000-0000-0000000000aa",
        workflow_definition_id="workflow",
        workflow_definition_version="1",
        routing_policy_id="policy",
        routing_policy_version="1",
        idempotency_key="idem",
        requested_by="user",
        status="running",
    )
    repo = Mock(session=Mock(), get_tasks_for_run=AsyncMock(return_value=[]))
    service = DirectExecutionService(session_factory=Mock())
    assert await service._rehydrate_execution(repo, run) is None
    repo.get_tasks_for_run.assert_awaited_once_with(
        "run-other", organization_id="00000000-0000-0000-0000-0000000000aa"
    )
