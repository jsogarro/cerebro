"""Unit tests for AdaptiveOutcomeOutboxHandler decision, cost, and evaluation logic."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.ai_brain.router.adaptive_state_store import InMemoryAdaptiveStateStore
from src.ai_brain.router.masr import MASRouter
from src.ai_brain.router.outcome_recorder import (
    ADAPTIVE_OUTCOME_DESTINATION,
    RoutingOutcomeRecorder,
)
from src.ai_brain.router.routing_outcome import (
    EvaluatorEligibilityPolicy,
    MetricAvailability,
    OutcomeSource,
    RoutingOutcome,
)
from src.ai_brain.router.routing_types import CollaborationMode
from src.api.services.adaptive_outcome_outbox_handler import (
    AdaptiveOutcomeOutboxHandler,
    summarize_journal_cost,
)
from src.api.services.outbox_relay import OutboxRelay
from src.core.contracts import (
    EvaluationResult,
    EvaluationStatus,
    PinnedVersions,
    Run,
    RunStatus,
)
from src.core.evaluation.events import EVALUATION_DESTINATION, EVALUATION_TRIGGER_EVENT
from src.models.db.base import Base
from src.models.db.evaluation_result import AgentEvaluationResult
from src.models.db.model_call_reservation import ModelCallReservation
from src.models.db.run_config_snapshot import AgentRunConfigSnapshot
from src.models.db.run_event import (
    AgentRunEvent,
    AgentRunEventOutbox,
    EventDeliveryStatus,
)
from src.models.db.run_lifecycle import AgentRun
from src.repositories.evaluation_result_repository import EvaluationResultRepository
from src.repositories.run_config_snapshot_repository import RunConfigSnapshotRepository
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository

ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)


@asynccontextmanager
async def _sqlite_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        Base.metadata.tables[t.__tablename__]
        for t in (
            AgentRun,
            AgentRunConfigSnapshot,
            AgentRunEvent,
            AgentRunEventOutbox,
            AgentEvaluationResult,
            ModelCallReservation,
        )
    ]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


def _outbox_row(
    *,
    run_id: str,
    event_id: str = "evt-1",
    event_type: str = "run.succeeded",
    payload: dict[str, Any] | None = None,
    organization_id: uuid.UUID | None = ORG_ID,
) -> AgentRunEventOutbox:
    return AgentRunEventOutbox(
        event_id=event_id,
        run_id=run_id,
        organization_id=organization_id,
        destination=ADAPTIVE_OUTCOME_DESTINATION,
        idempotency_key=f"{ADAPTIVE_OUTCOME_DESTINATION}:{event_id}",
        payload={
            "event_id": event_id,
            "run_id": run_id,
            "event_type": event_type,
            "payload": payload or {},
        },
    )


async def _seed_run(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: str,
    status: RunStatus,
    collaboration_mode: str = "parallel",
    worker_count: int = 3,
    started_at: datetime | None = NOW,
    completed_at: datetime | None = NOW + timedelta(milliseconds=450),
) -> None:
    run = Run(
        run_id=run_id,
        tenant_id=str(ORG_ID),
        workflow_definition_id="wf-1",
        workflow_definition_version="1",
        routing_policy_id="rp-1",
        routing_policy_version="1",
        idempotency_key=f"idem-{run_id}",
        requested_by="user-1",
        status=status,
        status_reason="worker failed" if status is RunStatus.FAILED else None,
        created_at=NOW,
        updated_at=completed_at or NOW,
        started_at=started_at,
        completed_at=completed_at,
    )
    async with factory() as session:
        await RunLifecycleRepository(session).create_run(run, organization_id=ORG_ID)
        await RunConfigSnapshotRepository(session).create_snapshot(
            run=run,
            organization_id=ORG_ID,
            config_snapshot_id=f"{run_id}:config",
            authority_id="auth-1",
            authority_version="1",
            pinned_versions=PinnedVersions(
                workflow_definition_id="wf-1",
                workflow_definition_version="1",
                routing_policy_id="rp-1",
                routing_policy_version="1",
                event_envelope_version="1.0",
            ),
            configuration={
                "routing_policy": {"collaboration_mode": collaboration_mode},
                "workers": [{"worker_id": f"w-{i}"} for i in range(worker_count)],
            },
        )
        await session.commit()


def test_summarize_journal_cost_never_coerces_unknown_or_missing_to_zero() -> None:
    assert summarize_journal_cost([]) == (None, MetricAvailability.UNAVAILABLE)

    unknown_only = [
        ModelCallReservation(
            organization_id=ORG_ID,
            call_id="c-1",
            owner_kind="research_run",
            owner_id="r-1",
            price_digest="a" * 64,
            reserved_tokens=100,
            reserved_cost_usd=Decimal("0.05"),
            dispatch_state="may_have_sent",
            settlement_state="settled_unknown",
            settled_at=NOW,
        )
    ]
    assert summarize_journal_cost(unknown_only) == (
        None,
        MetricAvailability.UNAVAILABLE,
    )

    mixed = [
        ModelCallReservation(
            organization_id=ORG_ID,
            call_id="c-known",
            owner_kind="research_run",
            owner_id="r-1",
            price_digest="a" * 64,
            reserved_tokens=100,
            reserved_cost_usd=Decimal("0.05"),
            dispatch_state="may_have_sent",
            settlement_state="settled_known",
            actual_tokens=80,
            actual_cost_usd=Decimal("0.03"),
            settled_at=NOW,
        ),
        *unknown_only,
    ]
    assert summarize_journal_cost(mixed) == (None, MetricAvailability.UNAVAILABLE)

    known_only = [mixed[0]]
    cost, avail = summarize_journal_cost(known_only)
    assert avail is MetricAvailability.MEASURED
    assert cost == pytest.approx(0.03)


@pytest.mark.asyncio
async def test_handler_records_succeeded_run_with_p10_eval_and_p04_known_cost() -> None:
    captured: list[RoutingOutcome] = []
    store = InMemoryAdaptiveStateStore()
    router = MASRouter(
        config={"adaptive_routing_enabled": False},
        adaptive_state_store=store,
        outcome_eligibility_policy=EvaluatorEligibilityPolicy(
            allowed_evaluators={"claims.coverage": frozenset({"1.0.0"})}
        ),
    )
    original_record = router.record_routing_outcome

    async def spy_record(outcome: RoutingOutcome):
        captured.append(outcome)
        return await original_record(outcome)

    router.record_routing_outcome = spy_record  # type: ignore[method-assign]

    async with _sqlite_factory() as factory:
        await _seed_run(factory, run_id="run-ok", status=RunStatus.SUCCEEDED)
        async with factory() as session:
            session.add(
                ModelCallReservation(
                    organization_id=ORG_ID,
                    call_id="call-1",
                    owner_kind="research_run",
                    owner_id="run-ok",
                    price_digest="b" * 64,
                    reserved_tokens=100,
                    reserved_cost_usd=Decimal("0.05"),
                    dispatch_state="may_have_sent",
                    settlement_state="settled_known",
                    actual_tokens=90,
                    actual_cost_usd=Decimal("0.042"),
                    settled_at=NOW,
                )
            )
            await EvaluationResultRepository(session).append_evaluation(
                EvaluationResult(
                    evaluation_result_id="eval-ok-1",
                    run_id="run-ok",
                    evaluator_id="claims.coverage",
                    evaluator_version="1.0.0",
                    dimension="claims.coverage",
                    status=EvaluationStatus.PASSED,
                    deterministic=True,
                    score=1.0,
                    trigger="run.succeeded",
                    completed_at=NOW,
                ),
                organization_id=ORG_ID,
            )
            await session.commit()

        handler = AdaptiveOutcomeOutboxHandler(
            session_factory=factory,
            outcome_recorder=RoutingOutcomeRecorder(router, retry_delay_seconds=0),
        )
        assert await handler(_outbox_row(run_id="run-ok")) is True

    assert len(captured) == 1
    outcome = captured[0]
    assert outcome.run_id == "run-ok"
    assert outcome.execution_status == "succeeded"
    assert outcome.collaboration_mode is CollaborationMode.PARALLEL
    assert outcome.final_worker_count == 3
    assert outcome.latency_ms == 450
    assert outcome.cost_availability is MetricAvailability.MEASURED
    assert outcome.measured_cost == pytest.approx(0.042)
    assert outcome.source is OutcomeSource.EVALUATOR
    assert outcome.quality_availability is MetricAvailability.MEASURED
    assert outcome.quality_score == pytest.approx(1.0)
    assert outcome.evaluator_name == "claims.coverage"
    assert outcome.evaluator_version == "1.0.0"
    assert outcome.evaluation_id == "eval-ok-1"
    loaded = await store.load()
    assert loaded.snapshot is not None
    assert loaded.snapshot.eligible_outcome_count == 1
    assert loaded.snapshot.processed_outcome_count == 1


@pytest.mark.asyncio
async def test_handler_records_failed_run_truthfully_and_keeps_unknown_cost_unknown() -> (
    None
):
    captured: list[RoutingOutcome] = []
    store = InMemoryAdaptiveStateStore()
    router = MASRouter(
        config={"adaptive_routing_enabled": False},
        adaptive_state_store=store,
        outcome_eligibility_policy=EvaluatorEligibilityPolicy(
            allowed_evaluators={"claims.coverage": frozenset({"1.0.0"})}
        ),
    )
    original_record = router.record_routing_outcome

    async def spy_record(outcome: RoutingOutcome):
        captured.append(outcome)
        return await original_record(outcome)

    router.record_routing_outcome = spy_record  # type: ignore[method-assign]

    async with _sqlite_factory() as factory:
        await _seed_run(
            factory,
            run_id="run-fail",
            status=RunStatus.FAILED,
            collaboration_mode="hierarchical",
            worker_count=2,
        )
        async with factory() as session:
            session.add(
                ModelCallReservation(
                    organization_id=ORG_ID,
                    call_id="call-unk",
                    owner_kind="research_run",
                    owner_id="run-fail",
                    price_digest="c" * 64,
                    reserved_tokens=200,
                    reserved_cost_usd=Decimal("0.10"),
                    dispatch_state="may_have_sent",
                    settlement_state="settled_unknown",
                    settled_at=NOW,
                )
            )
            await session.commit()

        handler = AdaptiveOutcomeOutboxHandler(
            session_factory=factory,
            outcome_recorder=RoutingOutcomeRecorder(router, retry_delay_seconds=0),
        )
        row = _outbox_row(
            run_id="run-fail", event_id="evt-fail", event_type="run.failed"
        )
        assert await handler(row) is True
        # Second delivery (e.g. redelivery) is deduplicated by the store.
        assert await handler(row) is True

    assert len(captured) == 2
    assert captured[0].outcome_id == captured[1].outcome_id
    outcome = captured[0]
    assert outcome.execution_status == "failed"
    assert outcome.measured_cost is None
    assert outcome.cost_availability is MetricAvailability.UNAVAILABLE
    assert outcome.quality_score is None
    assert outcome.quality_availability is MetricAvailability.UNAVAILABLE
    assert outcome.source is OutcomeSource.UNAVAILABLE
    loaded = await store.load()
    assert loaded.snapshot is not None
    assert loaded.snapshot.eligible_outcome_count == 0
    assert loaded.snapshot.ineligible_outcome_count == 1
    assert loaded.snapshot.processed_outcome_count == 1


@pytest.mark.asyncio
async def test_handler_marks_fixture_run_as_fixture_source() -> None:
    captured: list[RoutingOutcome] = []
    router = MASRouter(config={"adaptive_routing_enabled": False})
    original = router.record_routing_outcome

    async def spy_record(outcome: RoutingOutcome):
        captured.append(outcome)
        return await original(outcome)

    router.record_routing_outcome = spy_record  # type: ignore[method-assign]

    async with _sqlite_factory() as factory:
        await _seed_run(factory, run_id="run-fix", status=RunStatus.SUCCEEDED)
        handler = AdaptiveOutcomeOutboxHandler(
            session_factory=factory,
            outcome_recorder=RoutingOutcomeRecorder(router, retry_delay_seconds=0),
        )
        row = _outbox_row(
            run_id="run-fix",
            event_id="evt-fix",
            event_type="run.succeeded",
            payload={"fixture_mode": True},
        )
        assert await handler(row) is True

    assert len(captured) == 1
    assert captured[0].source is OutcomeSource.FIXTURE
    assert captured[0].quality_availability is MetricAvailability.UNAVAILABLE
    assert captured[0].cost_availability is MetricAvailability.UNAVAILABLE
    assert captured[0].measured_cost is None
    status = await router.get_adaptive_status()
    assert status["eligible_outcome_count"] == 0
    assert status["ineligible_outcome_count"] == 1


@pytest.mark.asyncio
async def test_handler_ignores_non_terminal_trigger_evaluations() -> None:
    captured: list[RoutingOutcome] = []
    router = MASRouter(
        config={"adaptive_routing_enabled": False},
        outcome_eligibility_policy=EvaluatorEligibilityPolicy(
            allowed_evaluators={"claims.coverage": frozenset({"1.0.0"})}
        ),
    )
    original = router.record_routing_outcome

    async def spy_record(outcome: RoutingOutcome):
        captured.append(outcome)
        return await original(outcome)

    router.record_routing_outcome = spy_record  # type: ignore[method-assign]

    async with _sqlite_factory() as factory:
        await _seed_run(factory, run_id="run-offline", status=RunStatus.SUCCEEDED)
        async with factory() as session:
            await EvaluationResultRepository(session).append_evaluation(
                EvaluationResult(
                    evaluation_result_id="eval-offline-1",
                    run_id="run-offline",
                    evaluator_id="claims.coverage",
                    evaluator_version="1.0.0",
                    dimension="claims.coverage",
                    status=EvaluationStatus.PASSED,
                    deterministic=True,
                    score=0.95,
                    trigger="offline.audit",
                    completed_at=NOW,
                ),
                organization_id=ORG_ID,
            )
            await session.commit()

        handler = AdaptiveOutcomeOutboxHandler(
            session_factory=factory,
            outcome_recorder=RoutingOutcomeRecorder(router, retry_delay_seconds=0),
        )
        assert await handler(_outbox_row(run_id="run-offline")) is True

    assert len(captured) == 1
    assert captured[0].source is OutcomeSource.UNAVAILABLE
    assert captured[0].quality_score is None
    assert captured[0].quality_availability is MetricAvailability.UNAVAILABLE


@pytest.mark.asyncio
async def test_handler_reads_beyond_default_200_evaluation_rows() -> None:
    captured: list[RoutingOutcome] = []
    router = MASRouter(
        config={"adaptive_routing_enabled": False},
        outcome_eligibility_policy=EvaluatorEligibilityPolicy(
            allowed_evaluators={"claims.coverage": frozenset({"1.0.0"})}
        ),
    )
    original = router.record_routing_outcome

    async def spy_record(outcome: RoutingOutcome):
        captured.append(outcome)
        return await original(outcome)

    router.record_routing_outcome = spy_record  # type: ignore[method-assign]

    async with _sqlite_factory() as factory:
        await _seed_run(factory, run_id="run-many-evals", status=RunStatus.SUCCEEDED)
        async with factory() as session:
            repo = EvaluationResultRepository(session)
            for idx in range(205):
                await repo.append_evaluation(
                    EvaluationResult(
                        evaluation_result_id=f"eval-other-{idx:03d}",
                        run_id="run-many-evals",
                        evaluator_id="other.evaluator",
                        evaluator_version="1.0.0",
                        dimension="other.dim",
                        subject_id=f"claim-{idx:03d}",
                        status=EvaluationStatus.PASSED,
                        deterministic=True,
                        score=0.99,
                        trigger=EVALUATION_TRIGGER_EVENT,
                        completed_at=NOW,
                    ),
                    organization_id=ORG_ID,
                )
            await repo.append_evaluation(
                EvaluationResult(
                    evaluation_result_id="eval-zzz-allowed-tail",
                    run_id="run-many-evals",
                    evaluator_id="claims.coverage",
                    evaluator_version="1.0.0",
                    dimension="claims.coverage",
                    status=EvaluationStatus.PASSED,
                    deterministic=True,
                    score=0.82,
                    trigger=EVALUATION_TRIGGER_EVENT,
                    completed_at=NOW,
                ),
                organization_id=ORG_ID,
            )
            await session.commit()

        handler = AdaptiveOutcomeOutboxHandler(
            session_factory=factory,
            outcome_recorder=RoutingOutcomeRecorder(router, retry_delay_seconds=0),
        )
        assert await handler(_outbox_row(run_id="run-many-evals")) is True

    assert len(captured) == 1
    assert captured[0].source is OutcomeSource.EVALUATOR
    assert captured[0].evaluator_name == "claims.coverage"
    assert captured[0].quality_score == pytest.approx(0.82)


@pytest.mark.asyncio
async def test_handler_falls_back_to_unavailable_on_final_attempt_instead_of_dead_lettering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import select, update

    from src.core.config import settings

    monkeypatch.setattr(settings, "EVALUATION_DISPATCH_ENABLED", True)
    captured: list[RoutingOutcome] = []
    router = MASRouter(config={"adaptive_routing_enabled": False})
    original = router.record_routing_outcome

    async def spy_record(outcome: RoutingOutcome):
        captured.append(outcome)
        return await original(outcome)

    router.record_routing_outcome = spy_record  # type: ignore[method-assign]

    class _StubPublisher:
        async def publish_run_event(self, **_: Any) -> bool:
            return True

    async with _sqlite_factory() as factory:
        await _seed_run(factory, run_id="run-budget", status=RunStatus.SUCCEEDED)
        async with factory() as session:
            await RunEventRepository(session).append_event(
                run_id="run-budget",
                organization_id=ORG_ID,
                event_id="evt-budget",
                aggregate_type="run",
                aggregate_id="run-budget",
                event_type=EVALUATION_TRIGGER_EVENT,
                event_type_version="1",
                occurred_at=NOW,
                producer="test",
                deduplication_key="evt-budget",
                payload={},
                destinations=(),
            )
            await session.commit()

        async def stuck_eval(_row: AgentRunEventOutbox) -> bool:
            return False

        handler = AdaptiveOutcomeOutboxHandler(
            session_factory=factory,
            outcome_recorder=RoutingOutcomeRecorder(router, retry_delay_seconds=0),
            max_attempts=3,
        )
        relay = OutboxRelay(
            session_factory=factory,
            event_publisher=_StubPublisher(),  # type: ignore[arg-type]
            max_attempts=3,
            handlers={
                EVALUATION_DESTINATION: stuck_eval,
                ADAPTIVE_OUTCOME_DESTINATION: handler,
            },
        )

        # Attempts 1 and 2 defer while sibling evaluation is still unsettled.
        for _ in range(2):
            assert await relay.run_once() == 0
            assert captured == []
            async with factory() as session:
                await session.execute(
                    update(AgentRunEventOutbox)
                    .where(AgentRunEventOutbox.event_id == "evt-budget")
                    .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
                )
                await session.commit()

        # Keep evaluation row unsettled (e.g. still retrying with remaining budget).
        async with factory() as session:
            await session.execute(
                update(AgentRunEventOutbox)
                .where(
                    AgentRunEventOutbox.event_id == "evt-budget",
                    AgentRunEventOutbox.destination == EVALUATION_DESTINATION,
                )
                .values(
                    status=EventDeliveryStatus.PENDING.value,
                    attempts=0,
                    available_at=datetime.now(UTC) + timedelta(minutes=5),
                )
            )
            await session.commit()

        # Final attempt for adaptive_outcome must fall back cleanly instead of dead-lettering.
        assert await relay.run_once() == 1
        async with factory() as session:
            outcome_row = (
                await session.execute(
                    select(AgentRunEventOutbox).where(
                        AgentRunEventOutbox.event_id == "evt-budget",
                        AgentRunEventOutbox.destination == ADAPTIVE_OUTCOME_DESTINATION,
                    )
                )
            ).scalar_one()

    assert outcome_row.status == EventDeliveryStatus.DELIVERED.value
    assert outcome_row.attempts == 3
    assert len(captured) == 1
    assert captured[0].source is OutcomeSource.UNAVAILABLE
    assert captured[0].quality_score is None
    assert captured[0].quality_availability is MetricAvailability.UNAVAILABLE


@pytest.mark.asyncio
async def test_main_lifespan_sqlite_dev_delivers_terminal_adaptive_outcome(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from sqlalchemy import select

    from src.api import main
    from src.api.services import (
        agent_execution_service,
        masr_routing_service,
        supervisor_coordination_service,
        talkhier_session_service,
    )
    from src.models.db import session as db_session_module

    sqlite_url = f"sqlite+aiosqlite:///{tmp_path / 'dev.db'}"
    db_session_module._engine = None
    db_session_module._async_session_factory = None
    db_session_module._maintenance_engine = None
    db_session_module._maintenance_session_factory = None
    monkeypatch.setattr(main.settings, "DATABASE_URL", sqlite_url)
    monkeypatch.setattr(main.settings, "MAINTENANCE_DATABASE_URL", "")
    monkeypatch.setattr(main.settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(main.settings, "EVALUATION_DISPATCH_ENABLED", False)

    router = MASRouter(config={"adaptive_routing_enabled": False})

    class _Runtime:
        def __init__(self) -> None:
            self.router = router

        async def close(self) -> None:
            return None

    class _StubService:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def close(self) -> None:
            return None

    monkeypatch.setattr(main.MASRRuntime, "create", AsyncMock(return_value=_Runtime()))
    monkeypatch.setattr(main.event_publisher, "initialize", AsyncMock())
    monkeypatch.setattr(main.event_publisher, "shutdown", AsyncMock())
    monkeypatch.setattr(main.event_publisher, "redis_subscriber", None)
    monkeypatch.setattr(
        agent_execution_service,
        "configure_agent_execution_service",
        lambda **_kwargs: AsyncMock(),
    )
    monkeypatch.setattr(masr_routing_service, "MASRRoutingService", _StubService)
    monkeypatch.setattr(
        talkhier_session_service, "TalkHierSessionService", _StubService
    )
    monkeypatch.setattr(
        supervisor_coordination_service,
        "SupervisorCoordinationService",
        _StubService,
    )
    monkeypatch.setattr(
        "src.api.services.outbox_relay.OutboxRelay.start", lambda self: None
    )

    test_app = FastAPI(lifespan=main.lifespan)
    test_app.state.execution_authority_resolver = SimpleNamespace()
    async with main.lifespan(test_app):
        factory = db_session_module.get_session_factory()
        await _seed_run(factory, run_id="sqlite-run-1", status=RunStatus.SUCCEEDED)
        async with factory() as session:
            await RunEventRepository(session).append_event(
                run_id="sqlite-run-1",
                organization_id=ORG_ID,
                event_id="evt-sqlite-1",
                aggregate_type="run",
                aggregate_id="sqlite-run-1",
                event_type=EVALUATION_TRIGGER_EVENT,
                event_type_version="1",
                occurred_at=NOW,
                producer="test",
                deduplication_key="evt-sqlite-1",
                payload={},
                destinations=(),
            )
            await session.commit()

        handler = AdaptiveOutcomeOutboxHandler(
            session_factory=factory,
            outcome_recorder=RoutingOutcomeRecorder(router, retry_delay_seconds=0),
        )
        relay = OutboxRelay(
            session_factory=factory,
            event_publisher=SimpleNamespace(  # type: ignore[arg-type]
                publish_run_event=AsyncMock(return_value=True)
            ),
            handlers={ADAPTIVE_OUTCOME_DESTINATION: handler},
        )
        delivered = await relay.run_once()
        async with factory() as session:
            row = (
                await session.execute(
                    select(AgentRunEventOutbox).where(
                        AgentRunEventOutbox.event_id == "evt-sqlite-1",
                        AgentRunEventOutbox.destination == ADAPTIVE_OUTCOME_DESTINATION,
                    )
                )
            ).scalar_one()

    assert delivered == 1
    assert row.status == EventDeliveryStatus.DELIVERED.value
    assert row.last_error is None
