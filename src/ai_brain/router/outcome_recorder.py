"""Execution-correlated operational outcome recording for MASR.

This boundary derives retry-stable opaque identifiers and records only
allocations which the caller confirms reached an execution boundary. It does
not infer evaluator quality, cost, or execution from a routing decision.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from structlog import get_logger

from .routing_observability import observe_outcome
from .routing_outcome import (
    MetricAvailability,
    OutcomeApplicationResult,
    OutcomeSource,
    RoutingOutcome,
)

if TYPE_CHECKING:
    from .masr import MASRouter, RoutingDecision

logger = get_logger()

_IDENTIFIER_VERSION = "v1"


def derive_opaque_identifier(kind: str, *stable_parts: str) -> str:
    """Derive a non-reversible, retry-stable correlation identifier."""

    if kind not in {"routing", "outcome"}:
        raise ValueError("kind must be routing or outcome")
    if not stable_parts or any(not part for part in stable_parts):
        raise ValueError("opaque identifiers require non-empty stable parts")
    digest = hashlib.sha256(
        "\x1f".join((_IDENTIFIER_VERSION, kind, *stable_parts)).encode()
    ).hexdigest()
    prefix = "rt" if kind == "routing" else "out"
    return f"{prefix}_{digest}"


@dataclass(frozen=True)
class ExecutedAllocationOutcome:
    """Truthful measurements for one allocation which actually executed."""

    execution_id: str
    allocation_key: str
    allocation_attempt_id: str
    execution_status: str
    latency_ms: int
    source: OutcomeSource
    measured_cost: float | None = None
    cost_availability: MetricAvailability = MetricAvailability.UNAVAILABLE
    quality_score: float | None = None
    quality_availability: MetricAvailability = MetricAvailability.UNAVAILABLE
    run_id: str | None = None
    task_id: str | None = None


class RoutingOutcomeRecorder:
    """Build and deliver execution-correlated outcomes to one router."""

    def __init__(
        self,
        router: MASRouter,
        *,
        max_delivery_attempts: int = 2,
        retry_delay_seconds: float = 0.01,
    ) -> None:
        if max_delivery_attempts < 1:
            raise ValueError("max_delivery_attempts must be positive")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds cannot be negative")
        self._router = router
        self._max_delivery_attempts = max_delivery_attempts
        self._retry_delay_seconds = retry_delay_seconds

    async def record(
        self,
        decision: RoutingDecision,
        observation: ExecutedAllocationOutcome,
    ) -> OutcomeApplicationResult:
        """Deliver one outcome, retrying transient failures with the same ID."""

        metadata = decision.adaptive_metadata
        proposed_arm = metadata.proposed_arm if metadata is not None else 0
        applied_arm = metadata.applied_arm if metadata is not None else 0
        final_worker_count = (
            metadata.final_worker_count
            if metadata is not None
            else decision.agent_allocation.worker_count
        )
        routing_id = derive_opaque_identifier(
            "routing",
            observation.execution_id,
            observation.allocation_key,
            observation.allocation_attempt_id,
        )
        outcome_id = derive_opaque_identifier(
            "outcome",
            observation.execution_id,
            observation.allocation_key,
            observation.allocation_attempt_id,
        )
        outcome = RoutingOutcome(
            outcome_id=outcome_id,
            routing_id=routing_id,
            run_id=observation.run_id,
            task_id=observation.task_id,
            policy_version=self._router.adaptive_policy_version,
            schema_version=self._router.adaptive_schema_version,
            source=observation.source,
            collaboration_mode=decision.collaboration_mode,
            proposed_arm=proposed_arm,
            applied_arm=applied_arm,
            final_worker_count=final_worker_count,
            execution_status=observation.execution_status,
            latency_ms=observation.latency_ms,
            measured_cost=observation.measured_cost,
            cost_availability=observation.cost_availability,
            quality_score=observation.quality_score,
            quality_availability=observation.quality_availability,
        )

        result: OutcomeApplicationResult | None = None
        for attempt in range(self._max_delivery_attempts):
            result = await self._router.record_routing_outcome(outcome)
            if not result.retryable or attempt + 1 >= self._max_delivery_attempts:
                break
            if self._retry_delay_seconds:
                await asyncio.sleep(self._retry_delay_seconds)
        if result is None:
            raise AssertionError("outcome delivery loop must execute")

        observe_outcome(outcome, result)
        logger.info(
            "masr_routing_outcome_recorded",
            routing_id=routing_id,
            outcome_id=outcome_id,
            source=outcome.source.value,
            collaboration_mode=outcome.collaboration_mode.value,
            application_status=result.status.value,
            eligible=result.outcome.eligibility.eligible,
            duplicate=result.duplicate,
            cost_availability=outcome.cost_availability.value,
            quality_availability=outcome.quality_availability.value,
        )
        return result


__all__ = [
    "ExecutedAllocationOutcome",
    "RoutingOutcomeRecorder",
    "derive_opaque_identifier",
]
