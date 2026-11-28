"""Outbox handler that records dark adaptive routing outcomes on terminal runs.

Each terminal run event appends an ``adaptive_outcome`` outbox row. This
handler reads the durable P01 run outcome, P10-A evaluation verdict (falling
back to ``not_attempted`` when absent or unmeasured, never lexical scoring),
and P04 model-call journal spend (keeping unknown spend unknown, never 0)
inside a tenant-scoped application session, then delivers one retry-stable
outcome to :class:`RoutingOutcomeRecorder`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai_brain.router.outcome_recorder import (
    TERMINAL_RUN_EVENTS,
    ExecutedAllocationOutcome,
    RecordedRoutingDecision,
    RoutingOutcomeRecorder,
)
from src.ai_brain.router.routing_outcome import (
    ADAPTIVE_ARMS,
    MetricAvailability,
    OutcomeSource,
)
from src.ai_brain.router.routing_types import CollaborationMode
from src.core.contracts import EvaluationStatus, RunStatus
from src.core.evaluation.events import EVALUATION_DESTINATION, EVALUATION_TRIGGER_EVENT
from src.models.db.evaluation_result import AgentEvaluationResult
from src.models.db.model_call_reservation import ModelCallReservation
from src.models.db.run_event import AgentRunEventOutbox, EventDeliveryStatus
from src.models.db.run_lifecycle import AgentRun
from src.models.db.session import tenant_session
from src.repositories.evaluation_result_repository import EvaluationResultRepository
from src.repositories.run_config_snapshot_repository import RunConfigSnapshotRepository

_TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.SUCCEEDED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
    }
)
_EVENT_TO_STATUS = {
    "run.succeeded": RunStatus.SUCCEEDED.value,
    "run.failed": RunStatus.FAILED.value,
    "run.budget_exceeded": RunStatus.FAILED.value,
    "run.cancelled": RunStatus.CANCELLED.value,
    "run.interrupted": RunStatus.FAILED.value,
}
_VERDICT_EVALUATION_STATUSES = frozenset(
    {EvaluationStatus.PASSED.value, EvaluationStatus.FAILED.value}
)


def summarize_journal_cost(
    reservations: Sequence[ModelCallReservation],
) -> tuple[float | None, MetricAvailability]:
    """Compute measured spend from P04 journal rows without coercing unknown to 0.

    Returns ``(None, UNAVAILABLE)`` when no rows exist or any row is ``open``
    or ``settled_unknown``. Only returns ``MEASURED`` when every call in the
    run settled with a known amount (or was provably released unsent).
    """
    if not reservations:
        return None, MetricAvailability.UNAVAILABLE

    total = Decimal(0)
    for row in reservations:
        if row.settlement_state in {"settled_unknown", "open"}:
            return None, MetricAvailability.UNAVAILABLE
        if row.settlement_state == "settled_known":
            if row.actual_cost_usd is None:
                return None, MetricAvailability.UNAVAILABLE
            total += Decimal(row.actual_cost_usd)
        elif row.settlement_state != "released_unsent":
            return None, MetricAvailability.UNAVAILABLE

    measured = float(total)
    if not math.isfinite(measured) or measured < 0.0:
        return None, MetricAvailability.MALFORMED
    return measured, MetricAvailability.MEASURED


def _latency_ms(run_row: AgentRun) -> int | None:
    if run_row.completed_at is None:
        return None
    anchor = run_row.started_at or run_row.created_at
    if anchor is None:
        return None
    delta_ms = int((run_row.completed_at - anchor).total_seconds() * 1000)
    return max(0, delta_ms)


def _decision_from_snapshot(
    configuration: dict[str, Any] | None,
    event_payload: dict[str, Any],
) -> RecordedRoutingDecision:
    routing_policy = (configuration or {}).get("routing_policy") or {}
    raw_mode = routing_policy.get("collaboration_mode")
    try:
        mode = CollaborationMode(raw_mode) if raw_mode else CollaborationMode.DIRECT
    except ValueError:
        mode = CollaborationMode.DIRECT

    workers = (configuration or {}).get("workers")
    if isinstance(workers, list) and workers:
        worker_count = len(workers)
    else:
        raw_workers = event_payload.get("workers_used")
        worker_count = (
            raw_workers if isinstance(raw_workers, int) and raw_workers > 0 else 1
        )

    adaptive = routing_policy.get("adaptive_metadata") or {}
    proposed = adaptive.get("proposed_arm", 0)
    applied = adaptive.get("applied_arm", 0)
    proposed_arm = proposed if proposed in ADAPTIVE_ARMS else 0
    applied_arm = applied if applied in ADAPTIVE_ARMS else 0
    return RecordedRoutingDecision.from_allocation(
        collaboration_mode=mode,
        worker_count=worker_count,
        proposed_arm=proposed_arm,
        applied_arm=applied_arm,
    )


def _select_evaluation_row(
    rows: Sequence[AgentEvaluationResult],
    allowed_evaluators: dict[str, frozenset[str]] | Any,
) -> AgentEvaluationResult | None:
    """Pick one deterministic, scored P10-A verdict row, or ``None``."""
    candidates = [
        row
        for row in rows
        if row.status in _VERDICT_EVALUATION_STATUSES
        and row.score is not None
        and math.isfinite(row.score)
        and 0.0 <= row.score <= 1.0
    ]
    if not candidates:
        return None

    def _is_allowed(row: AgentEvaluationResult) -> bool:
        versions = allowed_evaluators.get(row.evaluator_id)
        return versions is not None and row.evaluator_version in versions

    # Prefer evaluators allowed by the router's eligibility policy, then the
    # lowest score (conservative) with deterministic tie-breaking.
    candidates.sort(
        key=lambda row: (
            0 if _is_allowed(row) else 1,
            float(row.score),  # type: ignore[arg-type]
            row.evaluator_id,
            row.evaluator_version,
            row.evaluation_result_id,
        )
    )
    return candidates[0]


class AdaptiveOutcomeOutboxHandler:
    """Deliver terminal run outcomes to :class:`RoutingOutcomeRecorder`."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AsyncSession],
        outcome_recorder: RoutingOutcomeRecorder,
        max_attempts: int = 10,
    ) -> None:
        self._session_factory = session_factory
        self._outcome_recorder = outcome_recorder
        self._max_attempts = max_attempts

    async def __call__(self, row: AgentRunEventOutbox) -> bool:
        envelope = row.payload or {}
        event_type = envelope.get("event_type")
        if event_type not in TERMINAL_RUN_EVENTS:
            return True
        if row.organization_id is None:
            return False

        organization = row.organization_id
        event_payload = envelope.get("payload") or {}
        eval_settled = True

        async with tenant_session(self._session_factory, organization) as session:
            if event_type == EVALUATION_TRIGGER_EVENT:
                eval_settled = await self._evaluation_settled(
                    session, event_id=row.event_id, organization_id=organization
                )
                # Defer while sibling evaluation is still in progress, except on
                # the final delivery attempt where falling back to unavailable
                # prevents dead-lettering the run's adaptive outcome.
                if not eval_settled and (row.attempts or 0) < self._max_attempts:
                    return False

            run_row = (
                await session.execute(
                    select(AgentRun).where(
                        AgentRun.run_id == row.run_id,
                        AgentRun.organization_id == organization,
                    )
                )
            ).scalar_one_or_none()
            if run_row is None:
                return False

            snapshot = await RunConfigSnapshotRepository(session).get_by_run(
                row.run_id, organization_id=organization
            )
            reservations = list(
                (
                    await session.execute(
                        select(ModelCallReservation).where(
                            ModelCallReservation.organization_id == organization,
                            ModelCallReservation.owner_kind == "research_run",
                            ModelCallReservation.owner_id == row.run_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            evaluation_rows = (
                await EvaluationResultRepository(session).list_evaluations_for_run(
                    row.run_id,
                    organization_id=organization,
                    trigger=EVALUATION_TRIGGER_EVENT,
                    limit=10_000,
                    latest_only=True,
                )
                if event_type == EVALUATION_TRIGGER_EVENT and eval_settled
                else []
            )

        execution_status = (
            run_row.status
            if run_row.status in _TERMINAL_RUN_STATUSES
            else _EVENT_TO_STATUS[event_type]
        )
        measured_cost, cost_availability = summarize_journal_cost(reservations)
        decision = _decision_from_snapshot(
            snapshot.configuration if snapshot is not None else None,
            event_payload if isinstance(event_payload, dict) else {},
        )

        fixture_run = (
            isinstance(event_payload, dict)
            and event_payload.get("fixture_mode") is True
        )
        source = OutcomeSource.UNAVAILABLE
        quality_score: float | None = None
        quality_availability = MetricAvailability.UNAVAILABLE
        evaluator_name: str | None = None
        evaluator_version: str | None = None
        evaluation_id: str | None = None

        if fixture_run:
            source = OutcomeSource.FIXTURE
        elif execution_status == RunStatus.SUCCEEDED.value:
            allowed = getattr(
                getattr(
                    self._outcome_recorder._router,
                    "_outcome_eligibility_policy",
                    None,
                ),
                "allowed_evaluators",
                {},
            )
            chosen = _select_evaluation_row(evaluation_rows, allowed)
            if chosen is not None:
                source = OutcomeSource.EVALUATOR
                quality_score = float(chosen.score)  # type: ignore[arg-type]
                quality_availability = MetricAvailability.MEASURED
                evaluator_name = chosen.evaluator_id
                evaluator_version = chosen.evaluator_version
                evaluation_id = chosen.evaluation_result_id

        observation = ExecutedAllocationOutcome(
            execution_id=row.run_id,
            allocation_key="primary",
            allocation_attempt_id="terminal",
            execution_status=execution_status,
            latency_ms=_latency_ms(run_row),
            source=source,
            measured_cost=measured_cost,
            cost_availability=cost_availability,
            quality_score=quality_score,
            quality_availability=quality_availability,
            evaluator_name=evaluator_name,
            evaluator_version=evaluator_version,
            evaluation_id=evaluation_id,
            run_id=row.run_id,
        )
        result = await self._outcome_recorder.record(decision, observation)
        return not result.retryable

    async def _evaluation_settled(
        self, session: AsyncSession, *, event_id: str, organization_id: Any
    ) -> bool:
        """True once any sibling evaluation outbox row finished or exhausted retries."""
        eval_row = (
            await session.execute(
                select(AgentRunEventOutbox).where(
                    AgentRunEventOutbox.event_id == event_id,
                    AgentRunEventOutbox.destination == EVALUATION_DESTINATION,
                    AgentRunEventOutbox.organization_id == organization_id,
                )
            )
        ).scalar_one_or_none()
        if eval_row is None:
            return True
        if eval_row.status == EventDeliveryStatus.DELIVERED.value:
            return True
        return eval_row.status == EventDeliveryStatus.FAILED.value and (
            "dead-lettered" in (eval_row.last_error or "")
            or (eval_row.attempts or 0) >= self._max_attempts
        )


__all__ = ["AdaptiveOutcomeOutboxHandler", "summarize_journal_cost"]
