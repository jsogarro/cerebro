"""Postgres integration tests for dark adaptive outcome recording via the outbox."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

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
from src.api.services.adaptive_outcome_outbox_handler import (
    AdaptiveOutcomeOutboxHandler,
)
from src.api.services.evaluation_outbox_handler import EvaluationOutboxHandler
from src.api.services.outbox_relay import OutboxRelay
from src.core.config import settings
from src.core.contracts import (
    Artifact,
    ArtifactStatus,
    ClaimSupport,
    ClaimSupportStatus,
    PinnedVersions,
    ProducerKind,
    Run,
    RunStatus,
    Task,
    TaskStatus,
    TrustClassification,
)
from src.core.evaluation.events import EVALUATION_DESTINATION
from src.core.evaluation.registry import build_default_registry
from src.models.db.model_call_reservation import ModelCallReservation
from src.models.db.run_event import AgentRunEventOutbox, EventDeliveryStatus
from src.repositories.artifact_repository import ArtifactRepository
from src.repositories.claim_support_repository import ClaimSupportRepository
from src.repositories.run_config_snapshot_repository import RunConfigSnapshotRepository
from src.repositories.run_event_repository import RunEventRepository
from src.repositories.run_lifecycle_repository import RunLifecycleRepository
from tests.integration.wave4_helpers import seed_artifact, seed_evidence

pytestmark = [pytest.mark.integration]

ORG = uuid.UUID("00000000-0000-0000-0000-0000000000cc")
NOW = datetime(2026, 11, 28, 10, 0, tzinfo=UTC)


class _Publisher:
    async def publish_run_event(self, **_: Any) -> bool:
        return True


@pytest.fixture(name="factory")
def factory_fixture(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False)


def _dark_router(
    store: InMemoryAdaptiveStateStore | None = None,
) -> MASRouter:
    return MASRouter(
        config={
            "adaptive_routing_enabled": False,
            "adaptive_routing_min_history": 0,
            "adaptive_routing_min_samples_per_arm": 0,
            "adaptive_routing_performance_threshold": 0.0,
            "adaptive_routing_rng": np.random.default_rng(17),
            "enable_caching": False,
        },
        adaptive_state_store=store or InMemoryAdaptiveStateStore(),
        outcome_eligibility_policy=EvaluatorEligibilityPolicy(
            allowed_evaluators={"claims.coverage": frozenset({"1.0.0"})}
        ),
    )


async def _seed_terminal_run(
    factory: async_sessionmaker[AsyncSession],
    *,
    run_id: str,
    status: RunStatus,
    event_type: str,
    collaboration_mode: str = "parallel",
    worker_count: int = 3,
    with_covered_report: bool = False,
    reservations: list[ModelCallReservation] = (),
) -> str:
    run = Run(
        run_id=run_id,
        tenant_id=str(ORG),
        workflow_definition_id="wf-1",
        workflow_definition_version="1",
        routing_policy_id="rp-1",
        routing_policy_version="1",
        idempotency_key=f"idem-{run_id}",
        requested_by="user-1",
        status=status,
        status_reason="worker failed" if status is RunStatus.FAILED else None,
        created_at=NOW,
        updated_at=NOW + timedelta(seconds=2),
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=2),
    )
    event_id = str(uuid.uuid4())
    async with factory() as session:
        await RunLifecycleRepository(session).create_run(run, organization_id=ORG)
        await RunConfigSnapshotRepository(session).create_snapshot(
            run=run,
            organization_id=ORG,
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
        if with_covered_report:
            task_id = f"task-{run_id}"
            await RunLifecycleRepository(session).create_task(
                Task(
                    task_id=task_id,
                    run_id=run_id,
                    task_key="research",
                    task_type="research",
                    objective="Test objective",
                    idempotency_key=f"key-{task_id}",
                    status=TaskStatus.SUCCEEDED,
                    created_at=NOW,
                    updated_at=NOW,
                    started_at=NOW,
                    completed_at=NOW,
                ),
                organization_id=ORG,
            )
            snapshot_artifact_id = f"snap-{run_id}"
            evidence_id = f"ev-{run_id}"
            await seed_artifact(
                session,
                organization_id=ORG,
                artifact_id=snapshot_artifact_id,
                run_id=run_id,
            )
            await seed_evidence(
                session,
                organization_id=ORG,
                evidence_id=evidence_id,
                run_id=run_id,
                task_id=task_id,
                snapshot_artifact_id=snapshot_artifact_id,
            )
            artifact_id = f"art-{run_id}"
            await ArtifactRepository(session).create_artifact(
                Artifact(
                    artifact_id=artifact_id,
                    run_id=run_id,
                    kind="research_report",
                    media_type="text/markdown",
                    storage_uri=f"s3://bucket/{artifact_id}.md",
                    content_sha256="a" * 64,
                    status=ArtifactStatus.FINAL,
                    trust=TrustClassification.APPLICATION,
                    producer="report-generator",
                    producer_kind=ProducerKind.SYSTEM,
                    created_at=NOW,
                ),
                organization_id=ORG,
            )
            await ClaimSupportRepository(session).record_claim_support(
                ClaimSupport(
                    claim_support_id=f"cs-{run_id}",
                    run_id=run_id,
                    artifact_id=artifact_id,
                    claim_id="claim-1",
                    claim_text="Grounded finding.",
                    status=ClaimSupportStatus.SUPPORTED,
                    evidence_ids=(evidence_id,),
                    evaluator_id="containment",
                    evaluator_version="1.0.0",
                    explanation="Direct span match in stored evidence.",
                    producer_kind=ProducerKind.SYSTEM,
                    evaluated_at=NOW,
                ),
                organization_id=ORG,
            )
        for reservation in reservations:
            session.add(reservation)
        await RunEventRepository(session).append_event(
            run_id=run_id,
            organization_id=ORG,
            event_id=event_id,
            aggregate_type="run",
            aggregate_id=run_id,
            event_type=event_type,
            event_type_version="1",
            occurred_at=NOW + timedelta(seconds=2),
            producer="test",
            deduplication_key=event_id,
            payload={},
            destinations=("redis",),
        )
        await session.commit()
    return event_id


@pytest.mark.asyncio
async def test_succeeded_run_runs_evaluation_before_dark_outcome_recording_and_is_exactly_once(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "EVALUATION_DISPATCH_ENABLED", True)
    store = InMemoryAdaptiveStateStore()
    router = _dark_router(store)
    captured: list[RoutingOutcome] = []
    original = router.record_routing_outcome

    async def spy(outcome: RoutingOutcome):
        captured.append(outcome)
        return await original(outcome)

    router.record_routing_outcome = spy  # type: ignore[method-assign]

    query = "Compare renewable grid storage technologies across regions"
    untrained_decision = await _dark_router().route(query)
    before_decision = await router.route(query)

    event_id = await _seed_terminal_run(
        factory,
        run_id="p14-succ-eval",
        status=RunStatus.SUCCEEDED,
        event_type="run.succeeded",
        with_covered_report=True,
        reservations=[
            ModelCallReservation(
                organization_id=ORG,
                call_id="p14-call-1",
                owner_kind="research_run",
                owner_id="p14-succ-eval",
                price_digest="d" * 64,
                reserved_tokens=500,
                reserved_cost_usd=Decimal("0.05"),
                dispatch_state="may_have_sent",
                settlement_state="settled_known",
                actual_tokens=420,
                actual_cost_usd=Decimal("0.031"),
                settled_at=NOW,
            )
        ],
    )

    relay = OutboxRelay(
        session_factory=factory,
        event_publisher=_Publisher(),  # type: ignore[arg-type]
        handlers={
            EVALUATION_DESTINATION: EvaluationOutboxHandler(
                session_factory=factory,
                registry=build_default_registry(),
            ),
            ADAPTIVE_OUTCOME_DESTINATION: AdaptiveOutcomeOutboxHandler(
                session_factory=factory,
                outcome_recorder=RoutingOutcomeRecorder(router, retry_delay_seconds=0),
            ),
        },
    )

    # Single relay pass: redis, then evaluation, then adaptive_outcome.
    delivered = await relay.run_once()
    assert delivered == 3
    assert len(captured) == 1
    assert captured[0].execution_status == "succeeded"
    assert captured[0].source is OutcomeSource.EVALUATOR
    assert captured[0].quality_score == pytest.approx(1.0)
    assert captured[0].quality_availability is MetricAvailability.MEASURED
    assert captured[0].measured_cost == pytest.approx(0.031)
    assert captured[0].cost_availability is MetricAvailability.MEASURED

    # Force a redelivery of the adaptive_outcome outbox row -> duplicate, bandit unchanged.
    async with factory() as session:
        await session.execute(
            update(AgentRunEventOutbox)
            .where(
                AgentRunEventOutbox.event_id == event_id,
                AgentRunEventOutbox.destination == ADAPTIVE_OUTCOME_DESTINATION,
            )
            .values(
                status=EventDeliveryStatus.PENDING.value,
                available_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        await session.commit()

    assert await relay.run_once() == 1
    loaded = await store.load()
    assert loaded.snapshot is not None
    assert loaded.snapshot.eligible_outcome_count == 1
    assert loaded.snapshot.processed_outcome_count == 1

    # Routing decision remains byte-identical with dark bandit updated.
    after_decision = await router.route(query)
    for field in ("collaboration_mode", "agent_allocation", "routing_strategy"):
        assert asdict(before_decision)[field] == asdict(untrained_decision)[field]
        assert asdict(after_decision)[field] == asdict(untrained_decision)[field]


@pytest.mark.asyncio
async def test_failed_run_writes_one_failed_outcome_and_unknown_cost_stays_unknown(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "EVALUATION_DISPATCH_ENABLED", False)
    store = InMemoryAdaptiveStateStore()
    router = _dark_router(store)
    captured: list[RoutingOutcome] = []
    original = router.record_routing_outcome

    async def spy(outcome: RoutingOutcome):
        captured.append(outcome)
        return await original(outcome)

    router.record_routing_outcome = spy  # type: ignore[method-assign]

    await _seed_terminal_run(
        factory,
        run_id="p14-failed-unk",
        status=RunStatus.FAILED,
        event_type="run.failed",
        reservations=[
            ModelCallReservation(
                organization_id=ORG,
                call_id="p14-call-unk",
                owner_kind="research_run",
                owner_id="p14-failed-unk",
                price_digest="e" * 64,
                reserved_tokens=500,
                reserved_cost_usd=Decimal("0.08"),
                dispatch_state="may_have_sent",
                settlement_state="settled_unknown",
                settled_at=NOW,
            )
        ],
    )

    relay = OutboxRelay(
        session_factory=factory,
        event_publisher=_Publisher(),  # type: ignore[arg-type]
        handlers={
            ADAPTIVE_OUTCOME_DESTINATION: AdaptiveOutcomeOutboxHandler(
                session_factory=factory,
                outcome_recorder=RoutingOutcomeRecorder(router, retry_delay_seconds=0),
            ),
        },
    )

    assert await relay.run_once() == 2
    assert len(captured) == 1
    assert captured[0].execution_status == "failed"
    assert captured[0].measured_cost is None
    assert captured[0].cost_availability is MetricAvailability.UNAVAILABLE
    assert captured[0].quality_score is None
    assert captured[0].quality_availability is MetricAvailability.UNAVAILABLE
    loaded = await store.load()
    assert loaded.snapshot is not None
    assert loaded.snapshot.eligible_outcome_count == 0
    assert loaded.snapshot.ineligible_outcome_count == 1
    assert loaded.snapshot.processed_outcome_count == 1


@pytest.mark.asyncio
async def test_succeeded_run_without_evaluation_falls_back_to_not_attempted(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "EVALUATION_DISPATCH_ENABLED", False)
    store = InMemoryAdaptiveStateStore()
    router = _dark_router(store)
    captured: list[RoutingOutcome] = []
    original = router.record_routing_outcome

    async def spy(outcome: RoutingOutcome):
        captured.append(outcome)
        return await original(outcome)

    router.record_routing_outcome = spy  # type: ignore[method-assign]

    await _seed_terminal_run(
        factory,
        run_id="p14-no-eval",
        status=RunStatus.SUCCEEDED,
        event_type="run.succeeded",
    )

    relay = OutboxRelay(
        session_factory=factory,
        event_publisher=_Publisher(),  # type: ignore[arg-type]
        handlers={
            ADAPTIVE_OUTCOME_DESTINATION: AdaptiveOutcomeOutboxHandler(
                session_factory=factory,
                outcome_recorder=RoutingOutcomeRecorder(router, retry_delay_seconds=0),
            ),
        },
    )

    assert await relay.run_once() == 2
    assert len(captured) == 1
    assert captured[0].execution_status == "succeeded"
    assert captured[0].source is OutcomeSource.UNAVAILABLE
    assert captured[0].quality_score is None
    assert captured[0].quality_availability is MetricAvailability.UNAVAILABLE
    assert captured[0].measured_cost is None
    assert captured[0].cost_availability is MetricAvailability.UNAVAILABLE


@pytest.mark.asyncio
async def test_pending_evaluation_defers_adaptive_outcome_until_evaluation_finishes(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "EVALUATION_DISPATCH_ENABLED", True)
    store = InMemoryAdaptiveStateStore()
    router = _dark_router(store)
    captured: list[RoutingOutcome] = []
    original = router.record_routing_outcome

    async def spy(outcome: RoutingOutcome):
        captured.append(outcome)
        return await original(outcome)

    router.record_routing_outcome = spy  # type: ignore[method-assign]

    event_id = await _seed_terminal_run(
        factory,
        run_id="p14-defer",
        status=RunStatus.SUCCEEDED,
        event_type="run.succeeded",
        with_covered_report=True,
    )

    # First pass: evaluation handler fails transiently -> adaptive_outcome must defer!
    async def failing_eval(_row: AgentRunEventOutbox) -> bool:
        return False

    outcome_handler = AdaptiveOutcomeOutboxHandler(
        session_factory=factory,
        outcome_recorder=RoutingOutcomeRecorder(router, retry_delay_seconds=0),
    )
    first_relay = OutboxRelay(
        session_factory=factory,
        event_publisher=_Publisher(),  # type: ignore[arg-type]
        handlers={
            EVALUATION_DESTINATION: failing_eval,
            ADAPTIVE_OUTCOME_DESTINATION: outcome_handler,
        },
    )
    assert await first_relay.run_once() == 1  # only redis delivered
    assert captured == []

    # Make both rows due now and run with real evaluation handler -> both deliver!
    async with factory() as session:
        await session.execute(
            update(AgentRunEventOutbox)
            .where(AgentRunEventOutbox.event_id == event_id)
            .values(available_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()

    second_relay = OutboxRelay(
        session_factory=factory,
        event_publisher=_Publisher(),  # type: ignore[arg-type]
        handlers={
            EVALUATION_DESTINATION: EvaluationOutboxHandler(
                session_factory=factory,
                registry=build_default_registry(),
            ),
            ADAPTIVE_OUTCOME_DESTINATION: outcome_handler,
        },
    )
    assert await second_relay.run_once() == 2
    assert len(captured) == 1
    assert captured[0].source is OutcomeSource.EVALUATOR
    assert captured[0].quality_score == pytest.approx(1.0)
    async with factory() as session:
        rows = (
            await session.execute(
                select(AgentRunEventOutbox).where(
                    AgentRunEventOutbox.event_id == event_id
                )
            )
        ).scalars().all()
    assert all(r.status == EventDeliveryStatus.DELIVERED.value for r in rows)
