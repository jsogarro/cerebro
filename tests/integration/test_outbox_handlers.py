"""Outbox handler registration and the evaluation handler, against real Postgres."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.api.services.evaluation_outbox_handler import EvaluationOutboxHandler
from src.api.services.outbox_relay import OutboxRelay
from src.core.config import settings
from src.core.contracts import RunStatus
from src.core.evaluation.registry import (
    EvaluatorRegistry,
    build_default_registry,
    evaluation_states,
)
from src.models.db.evaluation_result import AgentEvaluationResult
from src.models.db.run_event import AgentRunEventOutbox, EventDeliveryStatus
from src.models.db.run_lifecycle import AgentRun
from src.repositories.evaluation_result_repository import EvaluationResultRepository
from src.repositories.run_event_repository import RunEventRepository
from tests.integration.wave4_helpers import seed_run_task_attempt

pytestmark = [pytest.mark.integration]

ORG = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
NOW = datetime(2026, 11, 27, tzinfo=UTC)


class _Publisher:
    async def publish_run_event(self, **_: Any) -> bool:
        return True


@pytest.fixture(name="factory")
def factory_fixture(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False)


async def _seed(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: str,
    event_type: str = "run.succeeded",
    destinations: tuple[str, ...] = ("evaluation",),
) -> str:
    async with factory() as session:
        await seed_run_task_attempt(
            session,
            organization_id=ORG,
            run_id=run_id,
            task_id=f"task-{run_id}",
            attempt_id=f"attempt-{run_id}",
        )
        event_id = str(uuid.uuid4())
        await RunEventRepository(session).append_event(
            run_id=run_id,
            organization_id=ORG,
            event_id=event_id,
            aggregate_type="run",
            aggregate_id=run_id,
            event_type=event_type,
            event_type_version="1",
            occurred_at=NOW,
            producer="test",
            deduplication_key=event_id,
            payload={},
            destinations=destinations,
        )
        await session.commit()
    return event_id


async def _outbox(
    factory: async_sessionmaker[AsyncSession], event_id: str
) -> AgentRunEventOutbox:
    async with factory() as session:
        return (
            await session.execute(
                select(AgentRunEventOutbox).where(
                    AgentRunEventOutbox.event_id == event_id
                )
            )
        ).scalar_one()


def _relay(
    factory: async_sessionmaker[AsyncSession], handlers: dict[str, Any], **kw: Any
) -> OutboxRelay:
    return OutboxRelay(
        session_factory=factory,
        event_publisher=_Publisher(),  # type: ignore[arg-type]
        handlers=handlers,
        **kw,
    )


@pytest.mark.asyncio
async def test_registered_handler_delivers_its_destination(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    seen: list[str] = []

    async def handler(row: AgentRunEventOutbox) -> bool:
        seen.append(row.run_id)
        return True

    event_id = await _seed(factory, run_id="h-ok")
    assert await _relay(factory, {"evaluation": handler}).run_once() == 1
    assert seen == ["h-ok"]
    assert (await _outbox(factory, event_id)).status == (
        EventDeliveryStatus.DELIVERED.value
    )


@pytest.mark.asyncio
async def test_raising_handler_backs_off_and_names_the_exception_class(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async def handler(row: AgentRunEventOutbox) -> bool:
        raise RuntimeError("secret-token-123")

    event_id = await _seed(factory, run_id="h-raise")
    assert await _relay(factory, {"evaluation": handler}).run_once() == 0
    row = await _outbox(factory, event_id)
    assert row.status == EventDeliveryStatus.FAILED.value
    assert row.available_at > datetime.now(UTC)
    assert "RuntimeError" in (row.last_error or "")
    assert "secret-token-123" not in (row.last_error or "")


@pytest.mark.asyncio
async def test_raising_handler_dead_letters_and_never_blocks_siblings(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async def handler(row: AgentRunEventOutbox) -> bool:
        raise RuntimeError("boom")

    bad = await _seed(factory, run_id="h-dead")
    good = await _seed(factory, run_id="h-sibling", destinations=("redis",))
    relay = _relay(factory, {"evaluation": handler}, max_attempts=1)
    assert await relay.run_once() == 1
    dead = await _outbox(factory, bad)
    assert dead.status == EventDeliveryStatus.FAILED.value
    assert "dead-lettered" in (dead.last_error or "")
    assert (await _outbox(factory, good)).status == EventDeliveryStatus.DELIVERED.value


@pytest.mark.parametrize(
    "name", ["", "Evaluation", "a b", "x" * 65, "redis", "websocket"]
)
def test_destination_names_are_validated_and_builtins_not_overridable(
    factory: async_sessionmaker[AsyncSession], name: str
) -> None:
    async def handler(row: AgentRunEventOutbox) -> bool:
        return True

    with pytest.raises(ValueError, match="destination"):
        _relay(factory, {name: handler})


@pytest.mark.asyncio
async def test_evaluation_handler_records_results_without_touching_the_run(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    event_id = await _seed(factory, run_id="e-ok")
    handler = EvaluationOutboxHandler(
        session_factory=factory, registry=build_default_registry()
    )
    relay = _relay(factory, {"evaluation": handler})

    assert await relay.run_once() == 1

    assert (await _outbox(factory, event_id)).status == (
        EventDeliveryStatus.DELIVERED.value
    )
    async with factory() as session:
        rows = await EvaluationResultRepository(session).list_evaluations_for_run(
            "e-ok", organization_id=ORG
        )
        run = (
            await session.execute(select(AgentRun).where(AgentRun.run_id == "e-ok"))
        ).scalar_one()
    assert [(r.evaluator_id, r.status, r.error_code) for r in rows] == [
        ("claims.coverage", "abstained", "no_final_report")
    ]
    assert run.status == RunStatus.CREATED.value  # a terminal status is never rewritten


@pytest.mark.asyncio
async def test_evaluation_handler_ignores_other_event_types(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    event_id = await _seed(factory, run_id="e-other", event_type="run.started")
    handler = EvaluationOutboxHandler(
        session_factory=factory, registry=build_default_registry()
    )
    assert await _relay(factory, {"evaluation": handler}).run_once() == 1
    assert (await _outbox(factory, event_id)).status == (
        EventDeliveryStatus.DELIVERED.value
    )
    async with factory() as session:
        count = len((await session.execute(select(AgentEvaluationResult))).all())
    assert count == 0


@pytest.mark.asyncio
async def test_unavailable_store_fails_the_delivery_not_the_relay(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    event_id = await _seed(factory, run_id="e-down")

    def broken() -> AsyncSession:
        raise ConnectionError("evaluation store down")

    handler = EvaluationOutboxHandler(
        session_factory=broken,  # type: ignore[arg-type]
        registry=build_default_registry(),
    )
    assert await _relay(factory, {"evaluation": handler}).run_once() == 0
    row = await _outbox(factory, event_id)
    assert row.status == EventDeliveryStatus.FAILED.value
    assert "ConnectionError" in (row.last_error or "")


@pytest.mark.asyncio
async def test_missing_row_reads_not_attempted_when_dispatch_is_off(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(factory, run_id="e-off", destinations=("redis",))
    await _relay(factory, {}).run_once()
    async with factory() as session:
        rows = await EvaluationResultRepository(session).list_evaluations_for_run(
            "e-off", organization_id=ORG, latest_only=True
        )
    assert set(evaluation_states(build_default_registry(), rows).values()) == {
        "not_attempted"
    }


@pytest.mark.asyncio
async def test_append_event_requests_evaluation_only_when_enabled(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def destinations(run_id: str, enabled: bool) -> list[str]:
        monkeypatch.setattr(settings, "EVALUATION_DISPATCH_ENABLED", enabled)
        event_id = await _seed(factory, run_id=run_id, destinations=("redis",))
        async with factory() as session:
            rows = (
                await session.execute(
                    select(AgentRunEventOutbox.destination).where(
                        AgentRunEventOutbox.event_id == event_id
                    )
                )
            ).all()
        return sorted(r[0] for r in rows)

    assert await destinations("a-off", False) == ["redis"]
    assert await destinations("a-on", True) == ["evaluation", "redis"]


class _Flaky:
    evaluator_id = "flaky"
    evaluator_version = "1"
    dimension = "flaky"
    reads_claim_support = False

    def __init__(self, *, fails: int) -> None:
        self.fails = fails

    async def evaluate(self, ctx: Any) -> list[Any]:
        if self.fails:
            self.fails -= 1
            raise RuntimeError("evaluator down")
        from src.core.contracts import EvaluationResult, EvaluationStatus

        return [
            EvaluationResult(
                evaluation_result_id=ctx.result_id(self),
                run_id=ctx.run_id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                dimension=self.dimension,
                status=EvaluationStatus.PASSED,
                deterministic=True,
                completed_at=ctx.completed_at,
            )
        ]


async def _due_now(factory: async_sessionmaker[AsyncSession], event_id: str) -> None:
    async with factory() as session:
        await session.execute(
            update(AgentRunEventOutbox)
            .where(AgentRunEventOutbox.event_id == event_id)
            .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()


def _flaky_handler(
    factory: async_sessionmaker[AsyncSession], evaluator: _Flaky
) -> EvaluationOutboxHandler:
    registry = EvaluatorRegistry()
    registry.register(evaluator)  # type: ignore[arg-type]
    return EvaluationOutboxHandler(session_factory=factory, registry=registry)


@pytest.mark.asyncio
async def test_a_failing_evaluator_is_retried_then_dead_lettered(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    event_id = await _seed(factory, run_id="f-dead")
    relay = _relay(
        factory,
        {"evaluation": _flaky_handler(factory, _Flaky(fails=99))},
        max_attempts=2,
    )
    assert await relay.run_once() == 0
    first = await _outbox(factory, event_id)
    assert first.status == EventDeliveryStatus.FAILED.value
    assert first.attempts == 1 and "dead-lettered" not in (first.last_error or "")
    await _due_now(factory, event_id)
    assert await relay.run_once() == 0
    assert "dead-lettered" in ((await _outbox(factory, event_id)).last_error or "")


@pytest.mark.asyncio
async def test_a_recovered_evaluator_completes_the_retried_delivery(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    event_id = await _seed(factory, run_id="f-recover")
    relay = _relay(factory, {"evaluation": _flaky_handler(factory, _Flaky(fails=1))})
    assert await relay.run_once() == 0
    await _due_now(factory, event_id)
    assert await relay.run_once() == 1
    assert (await _outbox(factory, event_id)).status == (
        EventDeliveryStatus.DELIVERED.value
    )


@pytest.mark.asyncio
async def test_a_hanging_handler_times_out_without_holding_other_destinations(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async def hang(row: AgentRunEventOutbox) -> bool:
        await asyncio.sleep(3600)
        return True

    slow = await _seed(factory, run_id="t-slow")  # lower id: claimed first
    fast = await _seed(factory, run_id="t-fast", destinations=("redis",))
    publisher = _Publisher()
    calls: list[str] = []

    async def publish(**kw: Any) -> bool:
        calls.append(kw["event_id"])
        return True

    publisher.publish_run_event = publish  # type: ignore[method-assign]
    relay = OutboxRelay(
        session_factory=factory,
        event_publisher=publisher,  # type: ignore[arg-type]
        handlers={"evaluation": hang},
        claim_lease_seconds=1.0,
        handler_timeout_seconds=0.2,
    )

    assert await asyncio.wait_for(relay.run_once(), timeout=5) == 1
    await asyncio.sleep(1.1)  # past the lease: a second relay may reclaim
    other = OutboxRelay(
        session_factory=factory,
        event_publisher=publisher,  # type: ignore[arg-type]
        handlers={"evaluation": hang},
        claim_lease_seconds=1.0,
        handler_timeout_seconds=0.2,
    )
    await asyncio.wait_for(other.run_once(), timeout=5)

    assert calls == [fast]
    row = await _outbox(factory, slow)
    assert row.status == EventDeliveryStatus.FAILED.value
    assert "TimeoutError" in (row.last_error or "")
