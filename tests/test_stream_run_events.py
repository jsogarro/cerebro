"""Unit tests for run-event stream derivation, entitlement, and notification.

These cover the parts of the stream that do not need a database: how a
subscriber's tenant entitlement is established from the token claim, that the
in-process wakeup hub can never wake a subscriber belonging to a different
organization, and that the outbox relay's notifications carry no payload.
"""

import asyncio
import uuid

import pytest

from src.api.services.outbox_relay import OutboxRelay
from src.api.websocket.run_stream import (
    RunStreamAuthorizationError,
    RunStreamEntitlement,
    RunStreamHub,
    StreamFrame,
)
from src.core.contracts import RunEventCursor, delivery_idempotency_key

ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
OTHER_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000bb")


class TestRunStreamEntitlement:
    """The subscriber's tenant boundary comes from the token, never the URL."""

    def test_entitlement_is_built_from_the_organization_claim(self) -> None:
        entitlement = RunStreamEntitlement.from_organization_claim(
            str(ORG_ID), user_id="user-1"
        )

        assert entitlement.organization_id == ORG_ID
        assert entitlement.user_id == "user-1"

    def test_missing_organization_claim_fails_closed(self) -> None:
        with pytest.raises(RunStreamAuthorizationError):
            RunStreamEntitlement.from_organization_claim(None)

    def test_blank_organization_claim_fails_closed(self) -> None:
        with pytest.raises(RunStreamAuthorizationError):
            RunStreamEntitlement.from_organization_claim("   ")

    def test_non_uuid_organization_claim_fails_closed(self) -> None:
        with pytest.raises(RunStreamAuthorizationError):
            RunStreamEntitlement.from_organization_claim("tenant-1")


class TestRunStreamHub:
    """The wakeup hub is keyed by organization as well as run."""

    @pytest.mark.asyncio
    async def test_notify_wakes_a_subscriber_of_the_same_run_and_org(self) -> None:
        hub = RunStreamHub()

        async with hub.subscribe(organization_id=ORG_ID, run_id="run-1") as waiter:
            hub.notify(organization_id=ORG_ID, run_id="run-1")
            await asyncio.wait_for(waiter.wait(), timeout=1.0)

    @pytest.mark.asyncio
    async def test_notify_never_wakes_a_subscriber_of_another_org(self) -> None:
        hub = RunStreamHub()

        async with hub.subscribe(organization_id=ORG_ID, run_id="run-1") as waiter:
            woken = hub.notify(organization_id=OTHER_ORG_ID, run_id="run-1")

            assert woken == 0
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(waiter.wait(), timeout=0.05)

    @pytest.mark.asyncio
    async def test_notify_never_wakes_a_subscriber_of_another_run(self) -> None:
        hub = RunStreamHub()

        async with hub.subscribe(organization_id=ORG_ID, run_id="run-1") as waiter:
            woken = hub.notify(organization_id=ORG_ID, run_id="run-2")

            assert woken == 0
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(waiter.wait(), timeout=0.05)

    @pytest.mark.asyncio
    async def test_subscription_is_released_on_exit(self) -> None:
        hub = RunStreamHub()

        async with hub.subscribe(organization_id=ORG_ID, run_id="run-1"):
            pass

        assert hub.notify(organization_id=ORG_ID, run_id="run-1") == 0
        assert hub.waiter_count == 0


class TestStreamFrame:
    """Frames carry the resume cursor and the consumer's dedupe key."""

    def test_frame_message_carries_cursor_and_idempotency_key(self) -> None:
        cursor = RunEventCursor(run_id="run-1", last_sequence=4)
        frame = StreamFrame(
            run_id="run-1",
            sequence=4,
            event_id="event-4",
            event_type="run.started",
            occurred_at="2026-08-04T00:00:00Z",
            payload={"progress": 10},
            cursor=cursor,
            destination="websocket",
            deduplication_key="dedupe-4",
        )

        message = frame.to_message()

        assert message["sequence"] == 4
        assert message["cursor"] == cursor.encode()
        assert message["idempotency_key"] == delivery_idempotency_key(
            deduplication_key="dedupe-4", destination="websocket"
        )
        assert message["payload"] == {"progress": 10}

    def test_frames_for_different_destinations_have_distinct_dedupe_keys(self) -> None:
        def build(destination: str) -> StreamFrame:
            return StreamFrame(
                run_id="run-1",
                sequence=1,
                event_id="event-1",
                event_type="run.started",
                occurred_at="2026-08-04T00:00:00Z",
                payload={},
                cursor=RunEventCursor(run_id="run-1", last_sequence=1),
                destination=destination,
                deduplication_key="dedupe-1",
            )

        assert (
            build("websocket").to_message()["idempotency_key"]
            != build("sse").to_message()["idempotency_key"]
        )


class _StubRow:
    def __init__(self, *, organization_id: uuid.UUID | None, run_id: str) -> None:
        self.organization_id = organization_id
        self.run_id = run_id
        self.destination = "websocket"
        self.event_id = "event-1"
        self.payload = {"event_type": "run.started", "payload": {}}


class TestOutboxRelayStreamNotification:
    """The relay signals live subscribers without shipping payloads to them."""

    @pytest.mark.asyncio
    async def test_websocket_destination_notifies_the_hub_for_its_own_org(
        self,
    ) -> None:
        hub = RunStreamHub()
        relay = OutboxRelay(
            session_factory=None,  # type: ignore[arg-type]
            event_publisher=None,  # type: ignore[arg-type]
            stream_hub=hub,
        )
        row = _StubRow(organization_id=ORG_ID, run_id="run-1")

        async with hub.subscribe(organization_id=ORG_ID, run_id="run-1") as waiter:
            delivered = await relay._deliver(row)  # type: ignore[arg-type]

            assert delivered is True
            await asyncio.wait_for(waiter.wait(), timeout=1.0)

    @pytest.mark.asyncio
    async def test_websocket_destination_does_not_notify_other_orgs(self) -> None:
        hub = RunStreamHub()
        relay = OutboxRelay(
            session_factory=None,  # type: ignore[arg-type]
            event_publisher=None,  # type: ignore[arg-type]
            stream_hub=hub,
        )
        row = _StubRow(organization_id=OTHER_ORG_ID, run_id="run-1")

        async with hub.subscribe(organization_id=ORG_ID, run_id="run-1") as waiter:
            await relay._deliver(row)  # type: ignore[arg-type]

            with pytest.raises(TimeoutError):
                await asyncio.wait_for(waiter.wait(), timeout=0.05)

    @pytest.mark.asyncio
    async def test_orgless_row_is_not_notified(self) -> None:
        hub = RunStreamHub()
        relay = OutboxRelay(
            session_factory=None,  # type: ignore[arg-type]
            event_publisher=None,  # type: ignore[arg-type]
            stream_hub=hub,
        )
        row = _StubRow(organization_id=None, run_id="run-1")

        delivered = await relay._deliver(row)  # type: ignore[arg-type]

        assert delivered is False
