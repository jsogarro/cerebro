"""Integration tests for run-event streaming against real Postgres.

These prove the two properties the stream exists for, and that only a real
database can demonstrate: a client that disconnects and comes back with its
cursor receives exactly the events it missed, and a subscriber can never
observe a run belonging to another tenant — on first subscribe or on resume
with a captured cursor.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.api.routes import run_stream as run_stream_route
from src.api.services.outbox_relay import OutboxRelay
from src.api.websocket.run_stream import (
    RunEventStreamReader,
    RunNotVisibleError,
    RunStreamEntitlement,
    RunStreamHub,
)
from src.auth.models import TokenPayload
from src.core.contracts import Run, RunEventCursor, RunStatus
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository

pytestmark = [pytest.mark.integration]

NOW = datetime(2026, 8, 4, tzinfo=UTC)
ORG_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
ORG_B = uuid.UUID("00000000-0000-0000-0000-0000000000b1")


def _make_run(run_id: str, organization_id: uuid.UUID) -> Run:
    return Run(
        run_id=run_id,
        tenant_id=str(organization_id),
        workflow_definition_id="research",
        workflow_definition_version="1",
        routing_policy_id="default",
        routing_policy_version="1",
        idempotency_key=f"submit-{run_id}",
        requested_by="user-1",
        status=RunStatus.CREATED,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest_asyncio.fixture(name="session_factory")
async def session_factory_fixture(
    test_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _seed_run(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: str,
    organization_id: uuid.UUID,
) -> None:
    async with session_factory() as session:
        await RunLifecycleRepository(session).create_run(
            _make_run(run_id, organization_id), organization_id=organization_id
        )
        await session.commit()


async def _append(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: str,
    organization_id: uuid.UUID,
    label: str,
    *,
    destinations: tuple[str, ...] = ("redis",),
) -> None:
    async with session_factory() as session:
        await RunEventRepository(session).append_event(
            run_id=run_id,
            organization_id=organization_id,
            event_id=f"{run_id}-{label}",
            aggregate_type="run",
            aggregate_id=run_id,
            event_type="run.progress",
            event_type_version="1",
            occurred_at=NOW,
            producer="test",
            deduplication_key=f"{run_id}-{label}",
            payload={"label": label},
            destinations=destinations,
        )
        await session.commit()


async def _complete_run(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: str,
    organization_id: uuid.UUID,
) -> None:
    run = _make_run(run_id, organization_id)
    async with session_factory() as session:
        repo = RunLifecycleRepository(session)
        for target in (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.SUCCEEDED):
            run = run.transition_to(target, at=NOW)
            await repo.record_run_transition(run, organization_id=organization_id)
        await session.commit()


def _token_payload(organization_id: uuid.UUID | None) -> TokenPayload:
    now = datetime.now(UTC)
    return TokenPayload(
        sub="00000000-0000-0000-0000-000000000001",
        email="user@example.com",
        roles=["researcher"],
        permissions=[],
        organization_id=str(organization_id) if organization_id else None,
        jti=str(uuid.uuid4()),
        iat=now,
        exp=now + timedelta(minutes=10),
    )


def _reader(
    session_factory: async_sessionmaker[AsyncSession],
    organization_id: uuid.UUID,
    hub: RunStreamHub | None = None,
) -> RunEventStreamReader:
    return RunEventStreamReader(
        session_factory=session_factory,
        entitlement=RunStreamEntitlement(organization_id=organization_id),
        destination="websocket",
        hub=hub if hub is not None else RunStreamHub(),
        poll_interval_seconds=0.05,
    )


class TestCursorResume:
    """A reconnecting client gets exactly what it missed, and nothing else."""

    @pytest.mark.asyncio
    async def test_resume_delivers_exactly_the_events_missed_while_disconnected(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run_id = "run-resume"
        await _seed_run(session_factory, run_id, ORG_A)
        for label in ("a", "b", "c"):
            await _append(session_factory, run_id, ORG_A, label)

        reader = _reader(session_factory, ORG_A)

        # First connection consumes what exists, then "disconnects" holding
        # only the opaque resume token.
        first_frames, cursor = await reader.read_batch(RunEventCursor(run_id=run_id))
        assert [frame.payload["label"] for frame in first_frames] == ["a", "b", "c"]
        assert [frame.sequence for frame in first_frames] == [1, 2, 3]
        token = cursor.encode()

        # Events the client is not connected for.
        for label in ("d", "e", "f"):
            await _append(session_factory, run_id, ORG_A, label)
        await _complete_run(session_factory, run_id, ORG_A)

        resumed = RunEventCursor.decode(token)
        replayed = [frame async for frame in reader.stream(run_id, cursor=resumed)]

        assert [frame.payload["label"] for frame in replayed] == ["d", "e", "f"]
        assert [frame.sequence for frame in replayed] == [4, 5, 6]

    @pytest.mark.asyncio
    async def test_resume_from_the_start_replays_the_whole_run(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run_id = "run-from-zero"
        await _seed_run(session_factory, run_id, ORG_A)
        for label in ("a", "b"):
            await _append(session_factory, run_id, ORG_A, label)
        await _complete_run(session_factory, run_id, ORG_A)

        reader = _reader(session_factory, ORG_A)
        frames = [frame async for frame in reader.stream(run_id)]

        assert [frame.sequence for frame in frames] == [1, 2]

    @pytest.mark.asyncio
    async def test_each_frame_carries_the_cursor_that_resumes_after_it(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run_id = "run-frame-cursor"
        await _seed_run(session_factory, run_id, ORG_A)
        for label in ("a", "b", "c"):
            await _append(session_factory, run_id, ORG_A, label)
        await _complete_run(session_factory, run_id, ORG_A)

        reader = _reader(session_factory, ORG_A)
        frames = [frame async for frame in reader.stream(run_id)]

        # Resuming from the cursor attached to the second frame yields the
        # third and nothing earlier.
        mid_token = frames[1].to_message()["cursor"]
        tail = [
            frame
            async for frame in reader.stream(
                run_id, cursor=RunEventCursor.decode(mid_token)
            )
        ]
        assert [frame.sequence for frame in tail] == [3]

    @pytest.mark.asyncio
    async def test_tailing_client_sees_events_appended_after_it_subscribed(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run_id = "run-tail"
        await _seed_run(session_factory, run_id, ORG_A)
        reader = _reader(session_factory, ORG_A)

        async def append_then_finish() -> None:
            await asyncio.sleep(0.1)
            await _append(session_factory, run_id, ORG_A, "live")
            await _complete_run(session_factory, run_id, ORG_A)

        appender = asyncio.create_task(append_then_finish())
        frames = [frame async for frame in reader.stream(run_id, max_idle_seconds=5.0)]
        await appender

        assert [frame.payload["label"] for frame in frames] == ["live"]


class TestCrossTenantIsolation:
    """Entitlement is the token's organization, re-checked on every read."""

    @pytest.mark.asyncio
    async def test_subscriber_cannot_stream_another_tenants_run(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run_id = "run-org-a"
        await _seed_run(session_factory, run_id, ORG_A)
        await _append(session_factory, run_id, ORG_A, "secret")

        intruder = _reader(session_factory, ORG_B)

        with pytest.raises(RunNotVisibleError):
            await intruder.authorize(run_id)

        with pytest.raises(RunNotVisibleError):
            async for _ in intruder.stream(run_id):
                pytest.fail("cross-tenant stream must not yield events")

    @pytest.mark.asyncio
    async def test_org_filter_holds_even_if_authorization_is_bypassed(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run_id = "run-org-a-defense"
        await _seed_run(session_factory, run_id, ORG_A)
        await _append(session_factory, run_id, ORG_A, "secret")

        intruder = _reader(session_factory, ORG_B)
        frames, cursor = await intruder.read_batch(RunEventCursor(run_id=run_id))

        assert frames == []
        assert cursor.last_sequence == 0

    @pytest.mark.asyncio
    async def test_a_captured_cursor_does_not_widen_access(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run_id = "run-org-a-cursor"
        await _seed_run(session_factory, run_id, ORG_A)
        await _append(session_factory, run_id, ORG_A, "secret")

        owner = _reader(session_factory, ORG_A)
        _, owner_cursor = await owner.read_batch(RunEventCursor(run_id=run_id))
        stolen = RunEventCursor.decode(owner_cursor.encode())

        intruder = _reader(session_factory, ORG_B)
        with pytest.raises(RunNotVisibleError):
            async for _ in intruder.stream(run_id, cursor=stolen):
                pytest.fail("a captured cursor must not grant cross-tenant access")

    @pytest.mark.asyncio
    async def test_a_missing_run_is_indistinguishable_from_another_tenants_run(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_run(session_factory, "run-present", ORG_A)
        intruder = _reader(session_factory, ORG_B)

        with pytest.raises(RunNotVisibleError) as existing:
            await intruder.authorize("run-present")
        with pytest.raises(RunNotVisibleError) as absent:
            await intruder.authorize("run-absent")

        assert str(existing.value).replace("run-present", "X") == str(
            absent.value
        ).replace("run-absent", "X")

    @pytest.mark.asyncio
    async def test_a_cursor_for_a_different_run_is_rejected(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_run(session_factory, "run-one", ORG_A)
        await _seed_run(session_factory, "run-two", ORG_A)
        reader = _reader(session_factory, ORG_A)

        with pytest.raises(RunNotVisibleError):
            async for _ in reader.stream(
                "run-one", cursor=RunEventCursor(run_id="run-two", last_sequence=0)
            ):
                pytest.fail("a cursor for another run must not be honored")


class TestServerSentEventsTransport:
    """The SSE route frames the same durable events, with the same scoping."""

    @pytest.mark.asyncio
    async def test_sse_frames_carry_the_cursor_as_the_event_id(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_id = "run-sse"
        await _seed_run(session_factory, run_id, ORG_A)
        for label in ("a", "b"):
            await _append(session_factory, run_id, ORG_A, label)
        await _complete_run(session_factory, run_id, ORG_A)
        monkeypatch.setattr(
            run_stream_route, "get_session_factory", lambda: session_factory
        )

        response = await run_stream_route.stream_run_events(
            run_id,
            cursor=None,
            last_event_id=None,
            token_payload=_token_payload(ORG_A),
        )
        body = "".join([chunk async for chunk in response.body_iterator])

        assert response.media_type == "text/event-stream"
        assert body.count("event: run.progress") == 2
        first_id = body.split("id: ", 1)[1].split("\n", 1)[0]
        assert RunEventCursor.decode(first_id).last_sequence == 1

    @pytest.mark.asyncio
    async def test_sse_resumes_from_a_supplied_cursor(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_id = "run-sse-resume"
        await _seed_run(session_factory, run_id, ORG_A)
        for label in ("a", "b", "c"):
            await _append(session_factory, run_id, ORG_A, label)
        await _complete_run(session_factory, run_id, ORG_A)
        monkeypatch.setattr(
            run_stream_route, "get_session_factory", lambda: session_factory
        )

        response = await run_stream_route.stream_run_events(
            run_id,
            cursor=RunEventCursor(run_id=run_id, last_sequence=2).encode(),
            last_event_id=None,
            token_payload=_token_payload(ORG_A),
        )
        body = "".join([chunk async for chunk in response.body_iterator])

        assert '"label": "c"' in body
        assert '"label": "a"' not in body
        assert '"label": "b"' not in body

    @pytest.mark.asyncio
    async def test_sse_returns_not_found_for_another_tenants_run(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_id = "run-sse-other-org"
        await _seed_run(session_factory, run_id, ORG_A)
        await _append(session_factory, run_id, ORG_A, "secret")
        monkeypatch.setattr(
            run_stream_route, "get_session_factory", lambda: session_factory
        )

        with pytest.raises(HTTPException) as error:
            await run_stream_route.stream_run_events(
                run_id,
                cursor=None,
                last_event_id=None,
                token_payload=_token_payload(ORG_B),
            )

        assert error.value.status_code == 404

    @pytest.mark.asyncio
    async def test_sse_rejects_a_caller_without_an_organization_claim(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        run_id = "run-sse-no-org"
        await _seed_run(session_factory, run_id, ORG_A)
        monkeypatch.setattr(
            run_stream_route, "get_session_factory", lambda: session_factory
        )

        with pytest.raises(HTTPException) as error:
            await run_stream_route.stream_run_events(
                run_id,
                cursor=None,
                last_event_id=None,
                token_payload=_token_payload(None),
            )

        assert error.value.status_code == 403


class TestOutboxDrivenNotification:
    """The relay's websocket destination wakes only its own tenant."""

    @pytest.mark.asyncio
    async def test_relay_delivers_websocket_rows_and_wakes_that_tenant(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        run_id = "run-notify"
        await _seed_run(session_factory, run_id, ORG_A)
        await _append(session_factory, run_id, ORG_A, "a", destinations=("websocket",))

        hub = RunStreamHub()
        relay = OutboxRelay(
            session_factory=session_factory,
            event_publisher=None,  # type: ignore[arg-type]
            stream_hub=hub,
        )

        async with (
            hub.subscribe(organization_id=ORG_A, run_id=run_id) as mine,
            hub.subscribe(organization_id=ORG_B, run_id=run_id) as theirs,
        ):
            delivered = await relay.run_once()

            assert delivered == 1
            await asyncio.wait_for(mine.wait(), timeout=1.0)
            assert theirs.is_set() is False
