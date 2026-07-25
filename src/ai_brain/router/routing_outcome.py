"""Typed, provenance-aware outcomes for adaptive routing.

Only evaluator-qualified outcomes are allowed to update adaptive state.  Other
operational sources remain representable so callers can record them truthfully
without accidentally turning estimates or fixtures into learning signals.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType

from .routing_types import CollaborationMode

ADAPTIVE_OUTCOME_SCHEMA_VERSION = "1"
ADAPTIVE_POLICY_VERSION = "masr-adaptive-v1"
ADAPTIVE_ARMS = (-2, -1, 0, 1, 2)
ADAPTIVE_OUTCOME_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
ADAPTIVE_OUTCOME_ALLOWED_FUTURE_SKEW_SECONDS = 5 * 60
# Marker retention covers the full eligibility interval, including the small
# permitted positive clock skew, so no still-eligible outcome can be replayed
# after its idempotency marker expires.
ADAPTIVE_OUTCOME_RETENTION_SECONDS = (
    ADAPTIVE_OUTCOME_MAX_AGE_SECONDS + ADAPTIVE_OUTCOME_ALLOWED_FUTURE_SKEW_SECONDS
)

_OPAQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class OutcomeSource(StrEnum):
    """Truthful provenance for a routing outcome."""

    EVALUATOR = "evaluator"
    HEURISTIC = "heuristic"
    FIXED = "fixed"
    MANUAL = "manual"
    FIXTURE = "fixture"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"
    INCOMPATIBLE = "incompatible"


class MetricAvailability(StrEnum):
    """Availability of a metric without substituting estimates for measurements."""

    MEASURED = "measured"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"
    INCOMPATIBLE = "incompatible"


class OutcomeEligibilityReason(StrEnum):
    """Stable eligibility result used by state and observability layers."""

    ELIGIBLE = "eligible"
    SOURCE_NOT_EVALUATOR = "source_not_evaluator"
    QUALITY_NOT_MEASURED = "quality_not_measured"
    EVALUATOR_NOT_ALLOWED = "evaluator_not_allowed"
    EVALUATOR_VERSION_NOT_ALLOWED = "evaluator_version_not_allowed"
    POLICY_VERSION_MISMATCH = "policy_version_mismatch"
    SCHEMA_VERSION_MISMATCH = "schema_version_mismatch"
    ARM_NOT_ALLOWED = "arm_not_allowed"
    EXECUTION_NOT_SUCCESSFUL = "execution_not_successful"
    FUTURE_TIMESTAMP = "future_timestamp"
    OUTCOME_TOO_OLD = "outcome_too_old"


class OutcomeApplicationStatus(StrEnum):
    """Result of applying an outcome to adaptive state."""

    APPLIED = "applied"
    INELIGIBLE_RECORDED = "ineligible_recorded"
    DUPLICATE = "duplicate"
    STORE_ERROR = "store_error"
    INCOMPATIBLE_STATE = "incompatible_state"
    CONFLICT_EXHAUSTED = "conflict_exhausted"


@dataclass(frozen=True)
class OutcomeEligibility:
    """Result of applying the learning eligibility policy."""

    eligible: bool
    reason: OutcomeEligibilityReason


@dataclass(frozen=True)
class RoutingOutcome:
    """A validated outcome containing only bounded operational metadata.

    Identifiers are opaque correlation identifiers.  Query text, prompts,
    output, user identifiers, provider payloads, and token content deliberately
    have no fields in this contract.
    """

    outcome_id: str
    routing_id: str
    policy_version: str
    source: OutcomeSource
    collaboration_mode: CollaborationMode
    proposed_arm: int
    applied_arm: int
    final_worker_count: int
    execution_status: str
    latency_ms: int | None
    measured_cost: float | None
    cost_availability: MetricAvailability
    quality_score: float | None
    quality_availability: MetricAvailability
    evaluator_name: str | None = None
    evaluator_version: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    evaluation_id: str | None = None
    schema_version: str = ADAPTIVE_OUTCOME_SCHEMA_VERSION
    recorded_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    eligibility: OutcomeEligibility = OutcomeEligibility(
        eligible=False,
        reason=OutcomeEligibilityReason.SOURCE_NOT_EVALUATOR,
    )

    def __post_init__(self) -> None:
        for field_name in (
            "outcome_id",
            "routing_id",
            "run_id",
            "task_id",
            "evaluation_id",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _validate_opaque_id(field_name, value)

        if not self.schema_version or not self.policy_version:
            raise ValueError("schema_version and policy_version must be non-empty")
        if self.proposed_arm not in ADAPTIVE_ARMS:
            raise ValueError(f"proposed_arm must be one of {ADAPTIVE_ARMS}")
        if self.applied_arm not in ADAPTIVE_ARMS:
            raise ValueError(f"applied_arm must be one of {ADAPTIVE_ARMS}")
        if self.final_worker_count < 1:
            raise ValueError("final_worker_count must be at least 1")
        if not self.execution_status or len(self.execution_status) > 64:
            raise ValueError("execution_status must be a bounded non-empty string")
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("latency_ms cannot be negative")

        _validate_metric(
            "measured_cost",
            self.measured_cost,
            self.cost_availability,
            lower=0.0,
            upper=None,
        )
        _validate_metric(
            "quality_score",
            self.quality_score,
            self.quality_availability,
            lower=0.0,
            upper=1.0,
        )

        if self.source == OutcomeSource.EVALUATOR:
            if not self.evaluator_name or not self.evaluator_version:
                raise ValueError(
                    "evaluator outcomes require evaluator_name and evaluator_version"
                )
            if len(self.evaluator_name) > 128 or len(self.evaluator_version) > 64:
                raise ValueError("evaluator identity fields are too long")
        elif self.evaluator_name is not None or self.evaluator_version is not None:
            raise ValueError(
                "only evaluator outcomes may carry evaluator identity fields"
            )

        if self.recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")

    def with_eligibility(self, eligibility: OutcomeEligibility) -> RoutingOutcome:
        """Return a copy carrying the policy-derived eligibility result."""

        return replace(self, eligibility=eligibility)


@dataclass(frozen=True)
class OutcomeApplicationResult:
    """Truthful result returned by the router feedback loop."""

    status: OutcomeApplicationStatus
    outcome: RoutingOutcome
    learning_updated: bool
    duplicate: bool = False
    retryable: bool = False
    reason: str | None = None


class EvaluatorEligibilityPolicy:
    """Allow-list versioned evaluators for one adaptive policy version."""

    def __init__(
        self,
        *,
        policy_version: str = ADAPTIVE_POLICY_VERSION,
        schema_version: str = ADAPTIVE_OUTCOME_SCHEMA_VERSION,
        allowed_evaluators: Mapping[str, frozenset[str]] | None = None,
        successful_statuses: frozenset[str] = frozenset({"completed", "success"}),
        max_outcome_age: timedelta = timedelta(
            seconds=ADAPTIVE_OUTCOME_MAX_AGE_SECONDS
        ),
        allowed_future_skew: timedelta = timedelta(
            seconds=ADAPTIVE_OUTCOME_ALLOWED_FUTURE_SKEW_SECONDS
        ),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if max_outcome_age <= timedelta(0):
            raise ValueError("max_outcome_age must be positive")
        if allowed_future_skew < timedelta(0):
            raise ValueError("allowed_future_skew cannot be negative")
        self.policy_version = policy_version
        self.schema_version = schema_version
        self.allowed_evaluators: Mapping[str, frozenset[str]] = MappingProxyType(
            dict(allowed_evaluators or {})
        )
        self.successful_statuses = successful_statuses
        self.max_outcome_age = max_outcome_age
        self.allowed_future_skew = allowed_future_skew
        self.clock = clock

    def assess(self, outcome: RoutingOutcome) -> OutcomeEligibility:
        """Assess an outcome without trusting caller-supplied eligibility."""

        if outcome.schema_version != self.schema_version:
            return _ineligible(OutcomeEligibilityReason.SCHEMA_VERSION_MISMATCH)
        if outcome.policy_version != self.policy_version:
            return _ineligible(OutcomeEligibilityReason.POLICY_VERSION_MISMATCH)
        if outcome.source != OutcomeSource.EVALUATOR:
            return _ineligible(OutcomeEligibilityReason.SOURCE_NOT_EVALUATOR)
        if outcome.quality_availability != MetricAvailability.MEASURED:
            return _ineligible(OutcomeEligibilityReason.QUALITY_NOT_MEASURED)
        if outcome.applied_arm not in ADAPTIVE_ARMS:
            return _ineligible(OutcomeEligibilityReason.ARM_NOT_ALLOWED)
        if outcome.execution_status not in self.successful_statuses:
            return _ineligible(OutcomeEligibilityReason.EXECUTION_NOT_SUCCESSFUL)
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("eligibility policy clock must be timezone-aware")
        if outcome.recorded_at - now > self.allowed_future_skew:
            return _ineligible(OutcomeEligibilityReason.FUTURE_TIMESTAMP)
        if now - outcome.recorded_at > self.max_outcome_age:
            return _ineligible(OutcomeEligibilityReason.OUTCOME_TOO_OLD)

        versions = self.allowed_evaluators.get(outcome.evaluator_name or "")
        if versions is None:
            return _ineligible(OutcomeEligibilityReason.EVALUATOR_NOT_ALLOWED)
        if outcome.evaluator_version not in versions:
            return _ineligible(OutcomeEligibilityReason.EVALUATOR_VERSION_NOT_ALLOWED)
        return OutcomeEligibility(
            eligible=True,
            reason=OutcomeEligibilityReason.ELIGIBLE,
        )


def _validate_opaque_id(field_name: str, value: str) -> None:
    if not _OPAQUE_ID_PATTERN.fullmatch(value):
        raise ValueError(
            f"{field_name} must be an opaque identifier using safe identifier characters"
        )


def _validate_metric(
    field_name: str,
    value: float | None,
    availability: MetricAvailability,
    *,
    lower: float,
    upper: float | None,
) -> None:
    if availability == MetricAvailability.MEASURED:
        if value is None:
            raise ValueError(f"{field_name} is required when availability is measured")
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must be finite")
        if value < lower or (upper is not None and value > upper):
            raise ValueError(f"{field_name} is outside the accepted range")
        return

    if value is not None:
        raise ValueError(f"{field_name} must be null unless availability is measured")


def _ineligible(reason: OutcomeEligibilityReason) -> OutcomeEligibility:
    return OutcomeEligibility(eligible=False, reason=reason)


__all__ = [
    "ADAPTIVE_ARMS",
    "ADAPTIVE_OUTCOME_ALLOWED_FUTURE_SKEW_SECONDS",
    "ADAPTIVE_OUTCOME_MAX_AGE_SECONDS",
    "ADAPTIVE_OUTCOME_RETENTION_SECONDS",
    "ADAPTIVE_OUTCOME_SCHEMA_VERSION",
    "ADAPTIVE_POLICY_VERSION",
    "EvaluatorEligibilityPolicy",
    "MetricAvailability",
    "OutcomeApplicationResult",
    "OutcomeApplicationStatus",
    "OutcomeEligibility",
    "OutcomeEligibilityReason",
    "OutcomeSource",
    "RoutingOutcome",
]
