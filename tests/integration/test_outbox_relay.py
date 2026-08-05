"""Integration coverage for the outbox relay (Wave 3 Packet 3C).

Proves the claim/deliver/finalize cycle against a real Postgres-backed
outbox: pending rows get claimed exactly once per attempt, delivered rows
are marked ``delivered`` and never reclaimed, and a delivery failure is
retried later rather than lost.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.api.services.outbox_relay import OutboxRelay
from src.core.contracts import Run, RunStatus
from src.models.db.run_event import AgentRunEventOutbox, EventDeliveryStatus
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository

pytestmark = [pytest.mark.integration]

ORG_ID = "00000000-0000-0000-0000-000000000abc"
NOW = datetime(2026, 8, 4, tzinfo=UTC)


class _FakeEventPublisher:
    """Records ``publish_run_event`` calls; success is scripted per call."""

    def __init__(self, *, succeed: bool = True) -> None:
        self.succeed = succeed
        self.calls: list[dict[str, Any]] = []

    async def publish_run_event(
        self,
        *,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        event_id: str,
        sequence: int,
        idempotency_key: str,
        occurred_at: str | None = None,
    ) -> bool:
        self.calls.append(
            {
                "run_id": run_id,
                "event_type": event_type,
                "payload": payload,
                "occurred_at": occurred_at,
                "event_id": event_id,
                "sequence": sequence,
                "idempotency_key": idempotency_key,
            }
        )
        return self.succeed


async def _seed_run_with_event(
    session: AsyncSession, *, run_id: str, destinations: tuple[str, ...] = ("redis",)
) -> str:
    run = Run(
        run_id=run_id,
        tenant_id=ORG_ID,
        workflow_definition_id="workflow-1",
        workflow_definition_version="1",
        routing_policy_id="policy-1",
        routing_policy_version="1",
        idempotency_key=f"key-{run_id}",
        requested_by="user-1",
        status=RunStatus.CREATED,
        created_at=NOW,
        updated_at=NOW,
    )
    await RunLifecycleRepository(session).create_run(run, organization_id=ORG_ID)
    event_id = str(uuid.uuid4())
    await RunEventRepository(session).append_event(
        run_id=run_id,
        organization_id=ORG_ID,
        event_id=event_id,
        aggregate_type="run",
        aggregate_id=run_id,
        event_type="run.admitted",
        event_type_version="1",
        occurred_at=NOW,
        producer="test",
        deduplication_key=event_id,
        payload={"note": "seeded"},
        destinations=destinations,
    )
    await session.commit()
    return event_id


@pytest.mark.asyncio
async def test_run_once_delivers_pending_row_and_marks_it_delivered(
    test_engine: AsyncEngine,
) -> None:
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "outbox-run-delivered"
    async with session_factory() as session:
        event_id = await _seed_run_with_event(session, run_id=run_id)

    publisher = _FakeEventPublisher(succeed=True)
    relay = OutboxRelay(session_factory=session_factory, event_publisher=publisher)

    delivered = await relay.run_once()

    assert delivered == 1

    async with session_factory() as session:
        row = (
            await session.execute(
                select(AgentRunEventOutbox).where(
                    AgentRunEventOutbox.event_id == event_id
                )
            )
        ).scalar_one()
        assert row.status == EventDeliveryStatus.DELIVERED.value
        assert row.delivered_at is not None
        assert row.attempts == 1
        row_idempotency_key = row.idempotency_key

    # The published call carries exactly what a consumer needs to dedupe a
    # redelivery: the stored envelope's event_id/sequence plus this row's own
    # idempotency_key, not four hand-picked fields.
    assert publisher.calls == [
        {
            "run_id": run_id,
            "event_type": "run.admitted",
            "payload": {"note": "seeded"},
            "occurred_at": publisher.calls[0]["occurred_at"],
            "event_id": event_id,
            "sequence": 1,
            "idempotency_key": row_idempotency_key,
        }
    ]

    # A second run_once must not redeliver an already-delivered row.
    redelivered = await relay.run_once()
    assert redelivered == 0
    assert len(publisher.calls) == 1


@pytest.mark.asyncio
async def test_failed_delivery_is_retried_with_backoff_not_lost(
    test_engine: AsyncEngine,
) -> None:
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "outbox-run-retry"
    async with session_factory() as session:
        event_id = await _seed_run_with_event(session, run_id=run_id)

    publisher = _FakeEventPublisher(succeed=False)
    relay = OutboxRelay(session_factory=session_factory, event_publisher=publisher)

    delivered = await relay.run_once()
    assert delivered == 0
    assert len(publisher.calls) == 1

    async with session_factory() as session:
        row = (
            await session.execute(
                select(AgentRunEventOutbox).where(
                    AgentRunEventOutbox.event_id == event_id
                )
            )
        ).scalar_one()
        assert row.status == EventDeliveryStatus.FAILED.value
        assert row.attempts == 1
        assert row.last_error is not None
        # Backoff pushed availability into the future — immediately retrying
        # must not reclaim it.
        assert row.available_at > NOW

    # Immediately retrying does not redeliver — it's not claimable yet.
    delivered_again = await relay.run_once()
    assert delivered_again == 0
    assert len(publisher.calls) == 1


@pytest.mark.asyncio
async def test_unknown_destination_is_not_delivered_or_lost(
    test_engine: AsyncEngine,
) -> None:
    session_factory = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    run_id = "outbox-run-unknown-destination"
    async with session_factory() as session:
        await _seed_run_with_event(
            session, run_id=run_id, destinations=("some-future-destination",)
        )

    publisher = _FakeEventPublisher(succeed=True)
    relay = OutboxRelay(session_factory=session_factory, event_publisher=publisher)

    delivered = await relay.run_once()

    assert delivered == 0
    assert publisher.calls == []

    async with session_factory() as session:
        row = (
            await session.execute(
                select(AgentRunEventOutbox).where(AgentRunEventOutbox.run_id == run_id)
            )
        ).scalar_one()
        # Claimed, attempted, and failed — never silently dropped.
        assert row.attempts == 1
        assert row.status == EventDeliveryStatus.FAILED.value
