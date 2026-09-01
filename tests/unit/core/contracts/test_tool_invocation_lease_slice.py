"""Focused tests for the first durable tool-invocation lease slice."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from src.core.contracts import (
    CapabilityDecision,
    CapabilityDecisionEffect,
    ToolInvocation,
    ToolInvocationStatus,
    TrustClassification,
)
from src.models.db.tool_invocation import AgentToolInvocation
from src.repositories.tool_invocation_repository import ToolInvocationRepository

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
LEASE_EXPIRY = NOW + timedelta(minutes=5)
ALLOW_DECISION = CapabilityDecision(
    effect=CapabilityDecisionEffect.ALLOW,
    request_fingerprint="a" * 64,
    grant_id="grant-1",
    decided_at=NOW,
)


def _make_invocation(**overrides: object) -> ToolInvocation:
    values: dict[str, object] = {
        "tool_invocation_id": "invocation-1",
        "run_id": "run-1",
        "task_id": "task-1",
        "attempt_id": "attempt-1",
        "tool_name": "academic_search",
        "tool_version": "1.0",
        "status": ToolInvocationStatus.REQUESTED,
        "capability_scope": "search",
        "idempotency_key": "invocation-key-1",
        "input": {"query": "graph neural networks"},
        "input_trust": TrustClassification.USER_SUPPLIED,
        "requested_at": NOW,
    }
    values.update(overrides)
    return ToolInvocation(**values)


@pytest.mark.parametrize(
    "status",
    [ToolInvocationStatus.REQUESTED, ToolInvocationStatus.RUNNING],
)
def test_pending_invocation_lease_fields_are_both_or_neither(
    status: ToolInvocationStatus,
) -> None:
    assert _make_invocation(status=status).lease_owner_id is None
    assert (
        _make_invocation(
            status=status,
            lease_owner_id="worker-1",
            lease_expires_at=LEASE_EXPIRY,
        ).lease_owner_id
        == "worker-1"
    )

    with pytest.raises(ValidationError, match="both or neither"):
        _make_invocation(status=status, lease_owner_id="worker-1")
    with pytest.raises(ValidationError, match="both or neither"):
        _make_invocation(status=status, lease_expires_at=LEASE_EXPIRY)


def test_agent_tool_invocation_declares_nullable_lease_columns_and_pending_index() -> (
    None
):
    table = AgentToolInvocation.__table__

    assert table.c.lease_owner_id.nullable is True
    assert table.c.lease_expires_at.nullable is True

    index = next(
        index
        for index in table.indexes
        if index.name == "idx_agent_tool_invocation_pending_lease"
    )
    assert [column.name for column in index.columns] == [
        "status",
        "lease_expires_at",
    ]


class _Result:
    def __init__(self, row: AgentToolInvocation | None) -> None:
        self.row = row

    def scalar_one_or_none(self) -> AgentToolInvocation | None:
        return self.row


class _Session:
    def __init__(self) -> None:
        self.added: AgentToolInvocation | None = None
        self.existing: AgentToolInvocation | None = None

    def add(self, row: AgentToolInvocation) -> None:
        self.added = row

    async def flush(self) -> None:
        return None

    async def refresh(self, row: AgentToolInvocation) -> None:
        return None

    async def execute(self, query: Any) -> _Result:
        return _Result(self.existing)


@pytest.mark.asyncio
async def test_repository_maps_lease_fields_on_create_and_transition() -> None:
    session = _Session()
    repository = ToolInvocationRepository(session)  # type: ignore[arg-type]
    leased = _make_invocation(lease_owner_id="worker-1", lease_expires_at=LEASE_EXPIRY)

    created = await repository.create_tool_invocation(
        leased,
        organization_id="00000000-0000-0000-0000-0000000000aa",
        capability_decision=ALLOW_DECISION,
    )

    assert created.lease_owner_id == "worker-1"
    assert created.lease_expires_at == LEASE_EXPIRY
    session.existing = created

    transitioned_contract = _make_invocation(
        status=ToolInvocationStatus.RUNNING,
        lease_owner_id="worker-2",
        lease_expires_at=LEASE_EXPIRY + timedelta(minutes=1),
    )
    transitioned = await repository.record_transition(
        transitioned_contract,
        organization_id="00000000-0000-0000-0000-0000000000aa",
    )

    assert transitioned.lease_owner_id == "worker-2"
    assert transitioned.lease_expires_at == LEASE_EXPIRY + timedelta(minutes=1)


def _migration_module() -> ModuleType:
    migration_path = (
        Path(__file__).parents[4]
        / "alembic"
        / "versions"
        / "a1b2c3d4e5f6_add_tool_invocation_leases.py"
    )
    spec = importlib.util.spec_from_file_location(
        "tool_invocation_leases", migration_path
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load migration at {migration_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lease_migration_is_chained_and_reversible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _migration_module()
    assert migration.revision == "a1b2c3d4e5f6"
    assert migration.down_revision == "f8c9d0e1a2b3"

    add_column = Mock()
    create_index = Mock()
    drop_column = Mock()
    drop_index = Mock()
    monkeypatch.setattr(migration.op, "add_column", add_column)
    monkeypatch.setattr(migration.op, "create_index", create_index)
    monkeypatch.setattr(migration.op, "drop_column", drop_column)
    monkeypatch.setattr(migration.op, "drop_index", drop_index)

    migration.upgrade()
    migration.downgrade()

    assert [call.args[0] for call in add_column.call_args_list] == [
        "agent_tool_invocations",
        "agent_tool_invocations",
    ]
    assert create_index.call_args.args[0] == "idx_agent_tool_invocation_pending_lease"
    assert drop_index.call_args.args[0] == "idx_agent_tool_invocation_pending_lease"
    assert [call.args for call in drop_column.call_args_list] == [
        ("agent_tool_invocations", "lease_expires_at"),
        ("agent_tool_invocations", "lease_owner_id"),
    ]
