"""Lease-aware recovery coverage for the session-backed tool audit store."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.agents.tools.durable_audit import SessionToolAuditStore
from src.core.contracts import (
    CapabilityDecision,
    CapabilityDecisionEffect,
    CapabilityGrant,
    SensitivityClass,
    ToolInvocation,
    ToolInvocationStatus,
    TrustClassification,
)
from src.core.tools import EVENT_COMPLETED, EVENT_REQUESTED, ToolAuditEvent
from src.core.tools.audit import LeaseAwareToolAuditStore
from src.core.tools.errors import ToolInvocationConflictError
from src.models.db.run_event import AgentRunEvent
from src.models.db.tool_invocation import AgentToolInvocation
from src.repositories.capability_repository import CapabilityRepository
from src.repositories.run_event_repository import RunEventRepository
from tests.integration.wave4_helpers import seed_run_task_attempt

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
OTHER_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
RUN_ID = "run-1"
TASK_ID = "task-1"
ATTEMPT_ID = "attempt-1"
TOOL = "academic_search"
VERSION = "1.0"
SCOPE = "search"
GRANT_ID = "grant-1"
UNKNOWN_OUTCOME_ERROR = "unknown_after_lease_expired"


@pytest_asyncio.fixture(name="session_factory")
async def session_factory_fixture(
    test_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(name="seeded", autouse=True)
async def seeded_fixture(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await seed_run_task_attempt(session, organization_id=ORG_ID)
        grant = CapabilityGrant(
            grant_id=GRANT_ID,
            run_id=RUN_ID,
            task_id=TASK_ID,
            capability_scope=SCOPE,
            tool_name=TOOL,
            tool_versions=(VERSION,),
            sensitivity=SensitivityClass.READ_ONLY,
            max_input_trust=TrustClassification.USER_SUPPLIED,
            requires_approval=False,
            issued_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(days=365),
        )
        await CapabilityRepository(session).create_grant(grant, organization_id=ORG_ID)
        await session.commit()


@pytest_asyncio.fixture(name="store")
async def store_fixture(
    session_factory: async_sessionmaker[AsyncSession],
) -> SessionToolAuditStore:
    return SessionToolAuditStore(session_factory, clock=lambda: NOW)


def _allow_decision() -> CapabilityDecision:
    return CapabilityDecision(
        effect=CapabilityDecisionEffect.ALLOW,
        request_fingerprint="a" * 64,
        grant_id=GRANT_ID,
        decided_at=NOW,
    )


def _invocation(
    *,
    invocation_id: str = "lease-invocation-1",
    idempotency_key: str = "lease-key-1",
    lease_owner_id: str | None = "worker-1",
    lease_expires_at: datetime | None = None,
    status: ToolInvocationStatus = ToolInvocationStatus.REQUESTED,
) -> ToolInvocation:
    terminal = status in {
        ToolInvocationStatus.SUCCEEDED,
        ToolInvocationStatus.FAILED,
        ToolInvocationStatus.CANCELLED,
        ToolInvocationStatus.TIMED_OUT,
        ToolInvocationStatus.DENIED,
    }
    return ToolInvocation(
        tool_invocation_id=invocation_id,
        run_id=RUN_ID,
        task_id=TASK_ID,
        attempt_id=ATTEMPT_ID,
        tool_name=TOOL,
        tool_version=VERSION,
        status=status,
        capability_scope=SCOPE,
        idempotency_key=idempotency_key,
        input={"query": "lease recovery"},
        input_trust=TrustClassification.USER_SUPPLIED,
        output={"hits": 1} if status is ToolInvocationStatus.SUCCEEDED else None,
        output_trust=(
            TrustClassification.EXTERNAL_UNTRUSTED
            if status is ToolInvocationStatus.SUCCEEDED
            else None
        ),
        error_code=UNKNOWN_OUTCOME_ERROR
        if status is ToolInvocationStatus.FAILED
        else None,
        status_reason=(
            "Tool outcome is unknown after the durable lease expired; "
            "the invocation must not be re-executed."
            if status is ToolInvocationStatus.FAILED
            else None
        ),
        requested_at=NOW,
        completed_at=NOW if terminal else None,
        lease_owner_id=lease_owner_id,
        lease_expires_at=lease_expires_at,
    )


def _requested_event(invocation: ToolInvocation) -> ToolAuditEvent:
    return ToolAuditEvent(
        event_id=f"{invocation.tool_invocation_id}:requested",
        run_id=invocation.run_id,
        task_id=invocation.task_id,
        attempt_id=invocation.attempt_id,
        aggregate_id=invocation.tool_invocation_id,
        event_type=EVENT_REQUESTED,
        occurred_at=invocation.requested_at,
        producer="integration-test",
        deduplication_key=f"{invocation.tool_invocation_id}:requested",
        payload={"phase": "requested"},
    )


def _completed_event(invocation: ToolInvocation) -> ToolAuditEvent:
    return ToolAuditEvent(
        event_id=f"{invocation.tool_invocation_id}:completed",
        run_id=invocation.run_id,
        task_id=invocation.task_id,
        attempt_id=invocation.attempt_id,
        aggregate_id=invocation.tool_invocation_id,
        event_type=EVENT_COMPLETED,
        occurred_at=invocation.completed_at or NOW,
        producer="integration-test",
        deduplication_key=f"{invocation.tool_invocation_id}:completed",
        payload={"phase": "completed", "outcome": invocation.status.value},
    )


async def _persist_pending(
    store: SessionToolAuditStore,
    *,
    invocation_id: str = "lease-invocation-1",
    idempotency_key: str = "lease-key-1",
    lease_owner_id: str | None = "worker-1",
    lease_expires_at: datetime | None = None,
) -> ToolInvocation:
    invocation = _invocation(
        invocation_id=invocation_id,
        idempotency_key=idempotency_key,
        lease_owner_id=lease_owner_id,
        lease_expires_at=lease_expires_at,
    )
    await store.persist(
        invocation=invocation,
        events=(_requested_event(invocation),),
        organization_id=ORG_ID,
        capability_decision=_allow_decision(),
    )
    return invocation


async def _event_rows(
    session_factory: async_sessionmaker[AsyncSession],
    invocation_id: str,
) -> list[AgentRunEvent]:
    async with session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(AgentRunEvent)
                    .where(AgentRunEvent.aggregate_id == invocation_id)
                    .order_by(AgentRunEvent.sequence)
                )
            ).all()
        )


async def _row(
    session_factory: async_sessionmaker[AsyncSession],
    invocation_id: str,
) -> AgentToolInvocation:
    async with session_factory() as session:
        row = await session.scalar(
            select(AgentToolInvocation).where(
                AgentToolInvocation.tool_invocation_id == invocation_id
            )
        )
        assert row is not None
        return row


def test_session_store_advertises_optional_lease_aware_protocol(
    store: SessionToolAuditStore,
) -> None:
    assert isinstance(store, LeaseAwareToolAuditStore)


@pytest.mark.asyncio
async def test_live_lease_is_returned_with_reconstructed_lease_fields(
    store: SessionToolAuditStore,
) -> None:
    lease_expires_at = NOW + timedelta(minutes=5)
    invocation = await _persist_pending(store, lease_expires_at=lease_expires_at)

    found = await store.reconcile_pending_invocation(
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        organization_id=ORG_ID,
        idempotency_key=invocation.idempotency_key,
        now=NOW,
    )

    assert found is not None
    assert found.status is ToolInvocationStatus.REQUESTED
    assert found.lease_owner_id == "worker-1"
    assert found.lease_expires_at == lease_expires_at


@pytest.mark.asyncio
async def test_stale_lease_is_terminalized_with_one_event(
    store: SessionToolAuditStore,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    invocation = await _persist_pending(
        store, lease_expires_at=NOW - timedelta(seconds=1)
    )

    found = await store.reconcile_pending_invocation(
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        organization_id=ORG_ID,
        idempotency_key=invocation.idempotency_key,
        now=NOW,
    )

    assert found is not None
    assert found.status is ToolInvocationStatus.FAILED
    assert found.output is None
    assert found.lease_owner_id is None
    assert found.lease_expires_at is None
    assert found.error_code == UNKNOWN_OUTCOME_ERROR
    assert found.status_reason is not None
    assert "unknown" in found.status_reason
    assert "must not be re-executed" in found.status_reason

    row = await _row(session_factory, invocation.tool_invocation_id)
    assert row.status == ToolInvocationStatus.FAILED.value
    assert row.output is None
    assert row.lease_owner_id is None
    assert row.lease_expires_at is None

    events = await _event_rows(session_factory, invocation.tool_invocation_id)
    assert [event.event_type for event in events] == [
        EVENT_REQUESTED,
        "tool.invocation.completed",
    ]
    assert events[-1].payload["outcome"] == ToolInvocationStatus.FAILED.value
    assert events[-1].payload["error_code"] == UNKNOWN_OUTCOME_ERROR


@pytest.mark.asyncio
async def test_late_owner_cannot_overwrite_reconciled_lease(
    store: SessionToolAuditStore,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    pending = await _persist_pending(
        store,
        invocation_id="late-owner-fence",
        idempotency_key="late-owner-fence-key",
        lease_expires_at=NOW - timedelta(seconds=1),
    )
    reconciled = await store.reconcile_pending_invocation(
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        organization_id=ORG_ID,
        idempotency_key=pending.idempotency_key,
        now=NOW,
    )
    assert reconciled is not None
    assert reconciled.error_code == UNKNOWN_OUTCOME_ERROR

    late_success = _invocation(
        invocation_id=pending.tool_invocation_id,
        idempotency_key=pending.idempotency_key,
        status=ToolInvocationStatus.SUCCEEDED,
        lease_owner_id=None,
        lease_expires_at=None,
    )
    with pytest.raises(ToolInvocationConflictError):
        await store.persist(
            invocation=late_success,
            events=(_completed_event(late_success),),
            organization_id=ORG_ID,
            capability_decision=_allow_decision(),
        )

    row = await _row(session_factory, pending.tool_invocation_id)
    assert row.status == ToolInvocationStatus.FAILED.value
    assert row.error_code == UNKNOWN_OUTCOME_ERROR
    assert row.output is None
    events = await _event_rows(session_factory, pending.tool_invocation_id)
    assert [event.event_type for event in events] == [
        EVENT_REQUESTED,
        EVENT_COMPLETED,
    ]
    assert events[-1].payload["error_code"] == UNKNOWN_OUTCOME_ERROR


@pytest.mark.asyncio
async def test_expired_owner_cannot_win_before_reconciliation(
    store: SessionToolAuditStore,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    pending = await _persist_pending(
        store,
        invocation_id="expired-owner-fence",
        idempotency_key="expired-owner-fence-key",
        lease_expires_at=NOW - timedelta(seconds=1),
    )
    late_success = _invocation(
        invocation_id=pending.tool_invocation_id,
        idempotency_key=pending.idempotency_key,
        status=ToolInvocationStatus.SUCCEEDED,
        lease_owner_id=None,
        lease_expires_at=None,
    )

    with pytest.raises(ToolInvocationConflictError):
        await store.persist(
            invocation=late_success,
            events=(_completed_event(late_success),),
            organization_id=ORG_ID,
            capability_decision=_allow_decision(),
        )

    row = await _row(session_factory, pending.tool_invocation_id)
    assert row.status == ToolInvocationStatus.FAILED.value
    assert row.error_code == UNKNOWN_OUTCOME_ERROR
    assert row.output is None
    events = await _event_rows(session_factory, pending.tool_invocation_id)
    assert [event.event_type for event in events] == [
        EVENT_REQUESTED,
        EVENT_COMPLETED,
    ]


@pytest.mark.asyncio
async def test_persist_fences_late_result_at_store_persistence_time(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    lease_expires_at = NOW + timedelta(minutes=5)
    persistence_now = lease_expires_at + timedelta(seconds=1)
    store = SessionToolAuditStore(session_factory, clock=lambda: persistence_now)

    pending = await _persist_pending(
        store,
        invocation_id="persistence-time-fence",
        idempotency_key="persistence-time-fence-key",
        lease_expires_at=lease_expires_at,
    )
    completed_at = NOW + timedelta(minutes=1)
    late_success = _invocation(
        invocation_id=pending.tool_invocation_id,
        idempotency_key=pending.idempotency_key,
        status=ToolInvocationStatus.SUCCEEDED,
        lease_owner_id=None,
        lease_expires_at=None,
    ).model_copy(update={"completed_at": completed_at})

    with pytest.raises(ToolInvocationConflictError) as conflict:
        await store.persist(
            invocation=late_success,
            events=(_completed_event(late_success),),
            organization_id=ORG_ID,
            capability_decision=_allow_decision(),
        )

    terminal = conflict.value.invocation
    assert terminal.status is ToolInvocationStatus.FAILED
    assert terminal.completed_at == persistence_now
    assert terminal.output is None
    assert terminal.lease_owner_id is None
    assert terminal.lease_expires_at is None
    assert terminal.error_code == UNKNOWN_OUTCOME_ERROR

    replayed = await store.find_invocation(
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        organization_id=ORG_ID,
        idempotency_key=pending.idempotency_key,
    )
    assert replayed == terminal

    row = await _row(session_factory, pending.tool_invocation_id)
    assert row.status == ToolInvocationStatus.FAILED.value
    assert row.completed_at == persistence_now
    assert row.output is None
    assert row.lease_owner_id is None
    assert row.lease_expires_at is None
    assert row.error_code == UNKNOWN_OUTCOME_ERROR

    events = await _event_rows(session_factory, pending.tool_invocation_id)
    assert [event.event_type for event in events] == [
        EVENT_REQUESTED,
        EVENT_COMPLETED,
    ]
    assert events[-1].occurred_at == persistence_now
    assert events[-1].payload["outcome"] == ToolInvocationStatus.FAILED.value
    assert events[-1].payload["error_code"] == UNKNOWN_OUTCOME_ERROR


@pytest.mark.asyncio
async def test_late_result_cannot_overwrite_legacy_null_lease_pending_row(
    store: SessionToolAuditStore,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    pending = await _persist_pending(
        store,
        invocation_id="legacy-null-lease-late-result",
        idempotency_key="legacy-null-lease-late-result-key",
        lease_owner_id=None,
        lease_expires_at=None,
    )
    late_success = _invocation(
        invocation_id=pending.tool_invocation_id,
        idempotency_key=pending.idempotency_key,
        status=ToolInvocationStatus.SUCCEEDED,
        lease_owner_id=None,
        lease_expires_at=None,
    )

    with pytest.raises(ToolInvocationConflictError) as conflict:
        await store.persist(
            invocation=late_success,
            events=(_completed_event(late_success),),
            organization_id=ORG_ID,
            capability_decision=_allow_decision(),
        )

    terminal = conflict.value.invocation
    assert terminal.status is ToolInvocationStatus.FAILED
    assert terminal.output is None
    assert terminal.lease_owner_id is None
    assert terminal.lease_expires_at is None
    assert terminal.error_code == UNKNOWN_OUTCOME_ERROR

    replayed = await store.find_invocation(
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        organization_id=ORG_ID,
        idempotency_key=pending.idempotency_key,
    )
    assert replayed == terminal

    row = await _row(session_factory, pending.tool_invocation_id)
    assert row.status == ToolInvocationStatus.FAILED.value
    assert row.output is None
    assert row.lease_owner_id is None
    assert row.lease_expires_at is None
    assert row.error_code == UNKNOWN_OUTCOME_ERROR

    events = await _event_rows(session_factory, pending.tool_invocation_id)
    assert [event.event_type for event in events] == [
        EVENT_REQUESTED,
        EVENT_COMPLETED,
    ]
    assert events[-1].payload["outcome"] == ToolInvocationStatus.FAILED.value
    assert events[-1].payload["error_code"] == UNKNOWN_OUTCOME_ERROR


@pytest.mark.asyncio
async def test_legacy_null_lease_is_terminalized(
    store: SessionToolAuditStore,
) -> None:
    invocation = await _persist_pending(
        store,
        invocation_id="legacy-null-lease",
        idempotency_key="legacy-null-key",
        lease_owner_id=None,
        lease_expires_at=None,
    )

    found = await store.reconcile_pending_invocation(
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        organization_id=ORG_ID,
        idempotency_key=invocation.idempotency_key,
        now=NOW,
    )

    assert found is not None
    assert found.status is ToolInvocationStatus.FAILED
    assert found.output is None
    assert found.lease_owner_id is None
    assert found.lease_expires_at is None


@pytest.mark.asyncio
async def test_concurrent_reconciliation_appends_exactly_one_terminal_event(
    store: SessionToolAuditStore,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    invocation = await _persist_pending(
        store,
        invocation_id="concurrent-reconcile",
        idempotency_key="concurrent-reconcile-key",
        lease_expires_at=NOW - timedelta(seconds=1),
    )

    results = await asyncio.gather(
        store.reconcile_pending_invocation(
            run_id=RUN_ID,
            attempt_id=ATTEMPT_ID,
            organization_id=ORG_ID,
            idempotency_key=invocation.idempotency_key,
            now=NOW,
        ),
        store.reconcile_pending_invocation(
            run_id=RUN_ID,
            attempt_id=ATTEMPT_ID,
            organization_id=ORG_ID,
            idempotency_key=invocation.idempotency_key,
            now=NOW,
        ),
    )

    assert [result.status for result in results if result is not None] == [
        ToolInvocationStatus.FAILED,
        ToolInvocationStatus.FAILED,
    ]
    events = await _event_rows(session_factory, invocation.tool_invocation_id)
    assert [event.event_type for event in events].count(
        "tool.invocation.completed"
    ) == 1


@pytest.mark.asyncio
async def test_renewal_requires_owner_tenant_pending_and_unexpired_lease(
    store: SessionToolAuditStore,
) -> None:
    invocation = await _persist_pending(
        store,
        invocation_id="renew-live",
        idempotency_key="renew-live-key",
        lease_expires_at=NOW + timedelta(seconds=30),
    )

    assert await store.renew_invocation_lease(
        tool_invocation_id=invocation.tool_invocation_id,
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        organization_id=ORG_ID,
        idempotency_key=invocation.idempotency_key,
        lease_owner_id="worker-1",
        now=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    assert not await store.renew_invocation_lease(
        tool_invocation_id=invocation.tool_invocation_id,
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        organization_id=ORG_ID,
        idempotency_key=invocation.idempotency_key,
        lease_owner_id="wrong-worker",
        now=NOW,
        lease_expires_at=NOW + timedelta(minutes=10),
    )
    assert not await store.renew_invocation_lease(
        tool_invocation_id=invocation.tool_invocation_id,
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        organization_id=OTHER_ORG_ID,
        idempotency_key=invocation.idempotency_key,
        lease_owner_id="worker-1",
        now=NOW,
        lease_expires_at=NOW + timedelta(minutes=10),
    )


@pytest.mark.asyncio
async def test_renewal_rejects_naive_requested_expiry(
    store: SessionToolAuditStore,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    current_expiry = NOW + timedelta(seconds=30)
    invocation = await _persist_pending(
        store,
        invocation_id="renew-naive-requested-expiry",
        idempotency_key="renew-naive-requested-expiry-key",
        lease_expires_at=current_expiry,
    )

    with pytest.raises(ValueError, match="lease_expires_at must be timezone-aware"):
        await store.renew_invocation_lease(
            tool_invocation_id=invocation.tool_invocation_id,
            run_id=RUN_ID,
            attempt_id=ATTEMPT_ID,
            organization_id=ORG_ID,
            idempotency_key=invocation.idempotency_key,
            lease_owner_id="worker-1",
            now=NOW,
            lease_expires_at=(NOW + timedelta(minutes=5)).replace(tzinfo=None),
        )

    row = await _row(session_factory, invocation.tool_invocation_id)
    assert row.lease_expires_at == current_expiry


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("invocation_id", "requested_expiry"),
    [
        ("renew-past-requested-expiry", NOW - timedelta(microseconds=1)),
        ("renew-equal-requested-expiry", NOW),
    ],
)
async def test_renewal_rejects_requested_expiry_not_after_now(
    store: SessionToolAuditStore,
    session_factory: async_sessionmaker[AsyncSession],
    invocation_id: str,
    requested_expiry: datetime,
) -> None:
    current_expiry = NOW + timedelta(seconds=30)
    invocation = await _persist_pending(
        store,
        invocation_id=invocation_id,
        idempotency_key=f"{invocation_id}-key",
        lease_expires_at=current_expiry,
    )

    assert not await store.renew_invocation_lease(
        tool_invocation_id=invocation.tool_invocation_id,
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        organization_id=ORG_ID,
        idempotency_key=invocation.idempotency_key,
        lease_owner_id="worker-1",
        now=NOW,
        lease_expires_at=requested_expiry,
    )

    row = await _row(session_factory, invocation.tool_invocation_id)
    assert row.lease_expires_at == current_expiry


@pytest.mark.asyncio
async def test_renewal_does_not_resurrect_expired_or_terminal_rows(
    store: SessionToolAuditStore,
) -> None:
    expired = await _persist_pending(
        store,
        invocation_id="renew-expired",
        idempotency_key="renew-expired-key",
        lease_expires_at=NOW - timedelta(seconds=1),
    )

    assert not await store.renew_invocation_lease(
        tool_invocation_id=expired.tool_invocation_id,
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        organization_id=ORG_ID,
        idempotency_key=expired.idempotency_key,
        lease_owner_id="worker-1",
        now=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )

    terminal = await store.reconcile_pending_invocation(
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        organization_id=ORG_ID,
        idempotency_key=expired.idempotency_key,
        now=NOW,
    )
    assert terminal is not None
    assert terminal.status is ToolInvocationStatus.FAILED

    assert not await store.renew_invocation_lease(
        tool_invocation_id=expired.tool_invocation_id,
        run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        organization_id=ORG_ID,
        idempotency_key=expired.idempotency_key,
        lease_owner_id="worker-1",
        now=NOW,
        lease_expires_at=NOW + timedelta(minutes=5),
    )


@pytest.mark.asyncio
async def test_reconcile_rolls_back_if_terminal_event_append_fails(
    store: SessionToolAuditStore,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation = await _persist_pending(
        store,
        invocation_id="reconcile-rollback",
        idempotency_key="reconcile-rollback-key",
        lease_expires_at=NOW - timedelta(seconds=1),
    )

    original_append_event = RunEventRepository.append_event

    async def fail_terminal_append(
        repository: RunEventRepository, **kwargs: Any
    ) -> Any:
        if kwargs["event_type"] == "tool.invocation.completed":
            await original_append_event(repository, **kwargs)
            raise RuntimeError("terminal event append failed")
        return await original_append_event(repository, **kwargs)

    monkeypatch.setattr(RunEventRepository, "append_event", fail_terminal_append)

    with pytest.raises(RuntimeError, match="terminal event append failed"):
        await store.reconcile_pending_invocation(
            run_id=RUN_ID,
            attempt_id=ATTEMPT_ID,
            organization_id=ORG_ID,
            idempotency_key=invocation.idempotency_key,
            now=NOW,
        )

    row = await _row(session_factory, invocation.tool_invocation_id)
    assert row.status == ToolInvocationStatus.REQUESTED.value
    assert row.lease_owner_id == "worker-1"
    assert row.lease_expires_at == NOW - timedelta(seconds=1)
    events = await _event_rows(session_factory, invocation.tool_invocation_id)
    assert [event.event_type for event in events] == [EVENT_REQUESTED]
