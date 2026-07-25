"""Manual, versioned promotion gate for MASR adaptive routing.

This module deliberately does not enable adaptive routing.  It evaluates an
explicit replay corpus against explicit criteria and emits evidence for a
separate operator decision.  Missing inputs and synthetic corpora can exercise
the wiring but can never authorize promotion.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

from src.ai_brain.router.masr import MASRouter
from src.ai_brain.router.routing_outcome import (
    ADAPTIVE_ARMS,
    ADAPTIVE_OUTCOME_SCHEMA_VERSION,
    ADAPTIVE_POLICY_VERSION,
    EvaluatorEligibilityPolicy,
    MetricAvailability,
    OutcomeSource,
    RoutingOutcome,
)
from src.ai_brain.router.routing_types import CollaborationMode

PROMOTION_CRITERIA_SCHEMA_VERSION = "1"
REPLAY_CORPUS_SCHEMA_VERSION = "1"
PROMOTION_REPORT_SCHEMA_VERSION = "1"
REPLAY_PROTOCOL_VERSION = "1"
_OPAQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _strict_object(value: Any, name: str) -> dict[str, Any]:
    if type(value) is not dict or not all(type(key) is str for key in value):
        raise ValueError(f"{name} must be an object with string keys")
    return value


def _require_fields(
    value: dict[str, Any], *, required: frozenset[str], name: str
) -> None:
    missing = required - value.keys()
    unexpected = value.keys() - required
    if missing or unexpected:
        raise ValueError(f"{name} does not match the required schema")


def _strict_string(value: Any, name: str, *, maximum: int = 128) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise ValueError(f"{name} must be a bounded non-empty string")
    return value


def _strict_bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean")
    return value


def _strict_int(value: Any, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


def _strict_float(value: Any, name: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _strict_optional_float(value: Any, name: str) -> float | None:
    return None if value is None else _strict_float(value, name)


def _reject_json_constant(_value: str) -> Any:
    raise ValueError("non-finite JSON constants are not accepted")


def _finite_difference(left: int | float, right: int | float, name: str) -> float:
    try:
        result = float(left - right)
    except (OverflowError, ValueError) as error:
        raise ValueError(
            f"{name} could not be represented as a finite value"
        ) from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _finite_mean(values: list[float], expected: int, name: str) -> float | None:
    if len(values) != expected:
        return None
    try:
        result = math.fsum(values) / len(values)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{name} could not be aggregated as a finite value") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


class GateStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class CorpusKind(StrEnum):
    EVALUATOR = "evaluator"
    SYNTHETIC = "synthetic"
    FIXTURE = "fixture"


@dataclass(frozen=True)
class ArmMeasurement:
    quality_score: float
    measured_cost: float | None
    latency_ms: int | None

    def __post_init__(self) -> None:
        if type(self.quality_score) not in (int, float) or not math.isfinite(
            self.quality_score
        ):
            raise ValueError("quality_score must be a finite number")
        if not 0.0 <= self.quality_score <= 1.0:
            raise ValueError("quality_score must be between 0 and 1")
        if self.measured_cost is not None:
            if type(self.measured_cost) not in (int, float) or not math.isfinite(
                self.measured_cost
            ):
                raise ValueError("measured_cost must be a finite number")
            if self.measured_cost < 0:
                raise ValueError("measured_cost cannot be negative")
        if self.latency_ms is not None:
            if type(self.latency_ms) is not int:
                raise ValueError("latency_ms must be an integer")
            if self.latency_ms < 0:
                raise ValueError("latency_ms cannot be negative")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ArmMeasurement:
        value = _strict_object(value, "arm measurement")
        _require_fields(
            value,
            required=frozenset({"quality_score", "measured_cost", "latency_ms"}),
            name="arm measurement",
        )
        return cls(
            quality_score=_strict_float(value["quality_score"], "quality_score"),
            measured_cost=_strict_optional_float(
                value["measured_cost"], "measured_cost"
            ),
            latency_ms=(
                None
                if value["latency_ms"] is None
                else _strict_int(value["latency_ms"], "latency_ms")
            ),
        )


@dataclass(frozen=True)
class ReplayCase:
    case_id: str
    observed_at: datetime
    collaboration_mode: CollaborationMode
    analytic_worker_count: int
    arm_measurements: dict[int, ArmMeasurement]

    def __post_init__(self) -> None:
        if not _OPAQUE_ID_PATTERN.fullmatch(self.case_id):
            raise ValueError("case_id must be a bounded opaque identifier")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if type(self.analytic_worker_count) is not int:
            raise ValueError("analytic_worker_count must be an integer")
        if self.analytic_worker_count < 1:
            raise ValueError("analytic_worker_count must be positive")
        if set(self.arm_measurements) != set(ADAPTIVE_ARMS):
            raise ValueError(f"every replay case must measure arms {ADAPTIVE_ARMS}")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ReplayCase:
        value = _strict_object(value, "replay case")
        _require_fields(
            value,
            required=frozenset(
                {
                    "case_id",
                    "observed_at",
                    "collaboration_mode",
                    "analytic_worker_count",
                    "arm_measurements",
                }
            ),
            name="replay case",
        )
        observed_at = _strict_string(value["observed_at"], "observed_at")
        timestamp = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        raw_measurements = _strict_object(value["arm_measurements"], "arm_measurements")
        expected_arm_keys = {str(arm) for arm in ADAPTIVE_ARMS}
        if set(raw_measurements) != expected_arm_keys:
            raise ValueError("arm_measurements must contain every supported arm")
        measurements = {
            int(arm): ArmMeasurement.from_dict(
                _strict_object(measurement, "arm measurement")
            )
            for arm, measurement in raw_measurements.items()
        }
        return cls(
            case_id=_strict_string(value["case_id"], "case_id"),
            observed_at=timestamp,
            collaboration_mode=CollaborationMode(
                _strict_string(value["collaboration_mode"], "collaboration_mode")
            ),
            analytic_worker_count=_strict_int(
                value["analytic_worker_count"], "analytic_worker_count"
            ),
            arm_measurements=measurements,
        )


@dataclass(frozen=True)
class ReplayCorpus:
    corpus_version: str
    policy_version: str
    outcome_schema_version: str
    evaluator_name: str
    evaluator_version: str
    evaluator_available: bool
    kind: CorpusKind
    representative: bool
    cases: tuple[ReplayCase, ...]
    schema_version: str = REPLAY_CORPUS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.evaluator_available) is not bool:
            raise ValueError("evaluator_available must be a boolean")
        if type(self.representative) is not bool:
            raise ValueError("representative must be a boolean")
        if not all(
            (
                self.schema_version,
                self.corpus_version,
                self.policy_version,
                self.outcome_schema_version,
                self.evaluator_name,
                self.evaluator_version,
            )
        ):
            raise ValueError(
                "replay corpus versions and evaluator identity are required"
            )
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("replay case IDs must be unique")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ReplayCorpus:
        value = _strict_object(value, "replay corpus")
        _require_fields(
            value,
            required=frozenset(
                {
                    "schema_version",
                    "corpus_version",
                    "policy_version",
                    "outcome_schema_version",
                    "evaluator_name",
                    "evaluator_version",
                    "evaluator_available",
                    "kind",
                    "representative",
                    "cases",
                }
            ),
            name="replay corpus",
        )
        raw_cases = value["cases"]
        if type(raw_cases) is not list:
            raise ValueError("cases must be an array")
        return cls(
            schema_version=_strict_string(value["schema_version"], "schema_version"),
            corpus_version=_strict_string(value["corpus_version"], "corpus_version"),
            policy_version=_strict_string(value["policy_version"], "policy_version"),
            outcome_schema_version=_strict_string(
                value["outcome_schema_version"], "outcome_schema_version"
            ),
            evaluator_name=_strict_string(value["evaluator_name"], "evaluator_name"),
            evaluator_version=_strict_string(
                value["evaluator_version"], "evaluator_version", maximum=64
            ),
            evaluator_available=_strict_bool(
                value["evaluator_available"], "evaluator_available"
            ),
            kind=CorpusKind(_strict_string(value["kind"], "kind")),
            representative=_strict_bool(value["representative"], "representative"),
            cases=tuple(
                ReplayCase.from_dict(_strict_object(item, "replay case"))
                for item in raw_cases
            ),
        )


@dataclass(frozen=True)
class PromotionPolicySnapshot:
    """Versioned diagnostic guardrails exercised by an offline replay.

    This is not yet a complete serialization of every deployed MASR behavior
    default, so a replay using it cannot by itself authorize promotion.
    """

    replay_protocol_version: str
    policy_version: str
    outcome_schema_version: str
    ordered_arms: tuple[int, ...]
    minimum_history: int
    minimum_samples_per_arm: int
    performance_threshold: float
    maximum_worker_adjust: int
    maximum_parallel_workers: int
    maximum_agents_per_query: int

    def __post_init__(self) -> None:
        if self.replay_protocol_version != REPLAY_PROTOCOL_VERSION:
            raise ValueError("unsupported replay protocol version")
        if self.policy_version != ADAPTIVE_POLICY_VERSION:
            raise ValueError("unsupported adaptive policy version")
        if self.outcome_schema_version != ADAPTIVE_OUTCOME_SCHEMA_VERSION:
            raise ValueError("unsupported adaptive outcome schema version")
        if self.ordered_arms != ADAPTIVE_ARMS:
            raise ValueError("ordered_arms must exactly match supported adaptive arms")
        integer_values = (
            self.minimum_history,
            self.minimum_samples_per_arm,
            self.maximum_worker_adjust,
            self.maximum_parallel_workers,
            self.maximum_agents_per_query,
        )
        if any(type(value) is not int for value in integer_values):
            raise ValueError("policy guardrails must use integer bounds")
        if self.minimum_history < 1 or self.minimum_samples_per_arm < 1:
            raise ValueError("policy readiness guardrails must be positive")
        if self.maximum_worker_adjust != max(abs(arm) for arm in ADAPTIVE_ARMS):
            raise ValueError("maximum_worker_adjust must cover the supported arms")
        if self.maximum_parallel_workers < 1 or self.maximum_agents_per_query < 1:
            raise ValueError("worker limits must be positive")
        if (
            type(self.performance_threshold) not in (int, float)
            or not math.isfinite(self.performance_threshold)
            or not 0.0 <= self.performance_threshold <= 1.0
        ):
            raise ValueError("performance_threshold must be finite and between 0 and 1")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PromotionPolicySnapshot:
        value = _strict_object(value, "promotion policy snapshot")
        _require_fields(
            value,
            required=frozenset(
                {
                    "replay_protocol_version",
                    "policy_version",
                    "outcome_schema_version",
                    "ordered_arms",
                    "minimum_history",
                    "minimum_samples_per_arm",
                    "performance_threshold",
                    "maximum_worker_adjust",
                    "maximum_parallel_workers",
                    "maximum_agents_per_query",
                }
            ),
            name="promotion policy snapshot",
        )
        raw_arms = value["ordered_arms"]
        if type(raw_arms) is not list:
            raise ValueError("ordered_arms must be an array")
        return cls(
            replay_protocol_version=_strict_string(
                value["replay_protocol_version"], "replay_protocol_version"
            ),
            policy_version=_strict_string(value["policy_version"], "policy_version"),
            outcome_schema_version=_strict_string(
                value["outcome_schema_version"], "outcome_schema_version"
            ),
            ordered_arms=tuple(_strict_int(arm, "ordered arm") for arm in raw_arms),
            minimum_history=_strict_int(value["minimum_history"], "minimum_history"),
            minimum_samples_per_arm=_strict_int(
                value["minimum_samples_per_arm"], "minimum_samples_per_arm"
            ),
            performance_threshold=_strict_float(
                value["performance_threshold"], "performance_threshold"
            ),
            maximum_worker_adjust=_strict_int(
                value["maximum_worker_adjust"], "maximum_worker_adjust"
            ),
            maximum_parallel_workers=_strict_int(
                value["maximum_parallel_workers"], "maximum_parallel_workers"
            ),
            maximum_agents_per_query=_strict_int(
                value["maximum_agents_per_query"], "maximum_agents_per_query"
            ),
        )

    def canonical_sha256(self) -> str:
        payload = json.dumps(
            asdict(self),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class PromotionCriteria:
    criteria_version: str
    policy_version: str
    outcome_schema_version: str
    corpus_schema_version: str
    evaluator_name: str
    evaluator_version: str
    policy_snapshot: PromotionPolicySnapshot
    required_modes: tuple[CollaborationMode, ...]
    approved_for_promotion: bool
    training_fraction: float
    minimum_total_cases: int
    minimum_training_cases: int
    minimum_heldout_cases: int
    minimum_training_cases_per_mode: int
    minimum_heldout_cases_per_mode: int
    minimum_training_outcomes_per_arm: int
    minimum_mean_quality_delta: float | None
    maximum_mean_cost_delta: float | None
    maximum_mean_latency_delta_ms: float | None
    schema_version: str = PROMOTION_CRITERIA_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.approved_for_promotion) is not bool:
            raise ValueError("approved_for_promotion must be a boolean")
        if type(self.training_fraction) not in (int, float) or not math.isfinite(
            self.training_fraction
        ):
            raise ValueError("training_fraction must be finite")
        if not 0.0 < self.training_fraction < 1.0:
            raise ValueError("training_fraction must be between 0 and 1")
        if any(
            type(item) is not int
            for item in (
                self.minimum_total_cases,
                self.minimum_training_cases,
                self.minimum_heldout_cases,
                self.minimum_training_cases_per_mode,
                self.minimum_heldout_cases_per_mode,
                self.minimum_training_outcomes_per_arm,
            )
        ):
            raise ValueError("sample-size criteria must be integers")
        if (
            min(
                self.minimum_total_cases,
                self.minimum_training_cases,
                self.minimum_heldout_cases,
                self.minimum_training_cases_per_mode,
                self.minimum_heldout_cases_per_mode,
                self.minimum_training_outcomes_per_arm,
            )
            < 1
        ):
            raise ValueError("sample-size criteria must be positive")
        if not all(
            (
                self.schema_version,
                self.criteria_version,
                self.policy_version,
                self.outcome_schema_version,
                self.corpus_schema_version,
                self.evaluator_name,
                self.evaluator_version,
            )
        ):
            raise ValueError("criteria versions and evaluator identity are required")
        if self.policy_snapshot.policy_version != self.policy_version:
            raise ValueError("policy snapshot and criteria policy versions must match")
        if self.policy_snapshot.outcome_schema_version != self.outcome_schema_version:
            raise ValueError("policy snapshot and criteria outcome versions must match")
        if not self.required_modes or len(set(self.required_modes)) != len(
            self.required_modes
        ):
            raise ValueError("required_modes must be non-empty and unique")
        supported_modes = {
            CollaborationMode.PARALLEL,
            CollaborationMode.HIERARCHICAL,
            CollaborationMode.ENSEMBLE,
        }
        if not set(self.required_modes).issubset(supported_modes):
            raise ValueError("required_modes contains a fixed-allocation mode")
        if (
            self.minimum_training_outcomes_per_arm
            < self.policy_snapshot.minimum_samples_per_arm
        ):
            raise ValueError("per-arm criteria cannot relax target readiness")
        if (
            self.minimum_training_cases_per_mode * len(ADAPTIVE_ARMS)
            < self.policy_snapshot.minimum_history
        ):
            raise ValueError("per-mode criteria cannot relax target history readiness")
        for name, threshold in (
            ("minimum_mean_quality_delta", self.minimum_mean_quality_delta),
            ("maximum_mean_cost_delta", self.maximum_mean_cost_delta),
            ("maximum_mean_latency_delta_ms", self.maximum_mean_latency_delta_ms),
        ):
            if threshold is not None and (
                type(threshold) not in (int, float) or not math.isfinite(threshold)
            ):
                raise ValueError(f"{name} must be finite")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PromotionCriteria:
        value = _strict_object(value, "promotion criteria")
        _require_fields(
            value,
            required=frozenset(
                {
                    "schema_version",
                    "criteria_version",
                    "policy_version",
                    "outcome_schema_version",
                    "corpus_schema_version",
                    "evaluator_name",
                    "evaluator_version",
                    "policy_snapshot",
                    "required_modes",
                    "approved_for_promotion",
                    "training_fraction",
                    "minimum_total_cases",
                    "minimum_training_cases",
                    "minimum_heldout_cases",
                    "minimum_training_cases_per_mode",
                    "minimum_heldout_cases_per_mode",
                    "minimum_training_outcomes_per_arm",
                    "minimum_mean_quality_delta",
                    "maximum_mean_cost_delta",
                    "maximum_mean_latency_delta_ms",
                }
            ),
            name="promotion criteria",
        )
        raw_modes = value["required_modes"]
        if type(raw_modes) is not list:
            raise ValueError("required_modes must be an array")
        return cls(
            schema_version=_strict_string(value["schema_version"], "schema_version"),
            criteria_version=_strict_string(
                value["criteria_version"], "criteria_version"
            ),
            policy_version=_strict_string(value["policy_version"], "policy_version"),
            outcome_schema_version=_strict_string(
                value["outcome_schema_version"], "outcome_schema_version"
            ),
            corpus_schema_version=_strict_string(
                value["corpus_schema_version"], "corpus_schema_version"
            ),
            evaluator_name=_strict_string(value["evaluator_name"], "evaluator_name"),
            evaluator_version=_strict_string(
                value["evaluator_version"], "evaluator_version", maximum=64
            ),
            policy_snapshot=PromotionPolicySnapshot.from_dict(
                _strict_object(value["policy_snapshot"], "promotion policy snapshot")
            ),
            required_modes=tuple(
                CollaborationMode(_strict_string(mode, "required mode"))
                for mode in raw_modes
            ),
            approved_for_promotion=_strict_bool(
                value["approved_for_promotion"], "approved_for_promotion"
            ),
            training_fraction=_strict_float(
                value["training_fraction"], "training_fraction"
            ),
            minimum_total_cases=_strict_int(
                value["minimum_total_cases"], "minimum_total_cases"
            ),
            minimum_training_cases=_strict_int(
                value["minimum_training_cases"], "minimum_training_cases"
            ),
            minimum_heldout_cases=_strict_int(
                value["minimum_heldout_cases"], "minimum_heldout_cases"
            ),
            minimum_training_cases_per_mode=_strict_int(
                value["minimum_training_cases_per_mode"],
                "minimum_training_cases_per_mode",
            ),
            minimum_heldout_cases_per_mode=_strict_int(
                value["minimum_heldout_cases_per_mode"],
                "minimum_heldout_cases_per_mode",
            ),
            minimum_training_outcomes_per_arm=_strict_int(
                value["minimum_training_outcomes_per_arm"],
                "minimum_training_outcomes_per_arm",
            ),
            minimum_mean_quality_delta=_strict_optional_float(
                value["minimum_mean_quality_delta"], "minimum_mean_quality_delta"
            ),
            maximum_mean_cost_delta=_strict_optional_float(
                value["maximum_mean_cost_delta"], "maximum_mean_cost_delta"
            ),
            maximum_mean_latency_delta_ms=_strict_optional_float(
                value["maximum_mean_latency_delta_ms"],
                "maximum_mean_latency_delta_ms",
            ),
        )


@dataclass(frozen=True)
class CriterionResult:
    name: str
    status: GateStatus
    observed: int | float | str | bool | None
    threshold: int | float | str | bool | None
    reason: str


@dataclass(frozen=True)
class ModeCaseCounts:
    collaboration_mode: str
    training_cases: int
    heldout_cases: int


@dataclass(frozen=True)
class ModeArmTrainingCount:
    collaboration_mode: str
    arm: int
    applied_outcomes: int


@dataclass(frozen=True)
class ReplayMetrics:
    total_cases: int
    training_cases: int
    heldout_cases: int
    mean_quality_delta: float | None
    mean_cost_delta: float | None
    mean_latency_delta_ms: float | None
    training_last_observed_at: str | None
    heldout_first_observed_at: str | None
    mode_case_counts: tuple[ModeCaseCounts, ...]
    mode_arm_training_counts: tuple[ModeArmTrainingCount, ...]


@dataclass(frozen=True)
class PromotionReport:
    status: GateStatus
    report_schema_version: str
    policy_version: str | None
    outcome_schema_version: str | None
    evaluator_name: str | None
    evaluator_version: str | None
    corpus_schema_version: str | None
    corpus_version: str | None
    criteria_schema_version: str | None
    criteria_version: str | None
    policy_snapshot: PromotionPolicySnapshot | None
    policy_snapshot_sha256: str | None
    seed: int
    corpus_kind: str | None
    synthetic: bool
    operator_review_eligible: bool
    activation_approved: bool
    generated_at: str
    criteria: tuple[CriterionResult, ...]
    metrics: ReplayMetrics | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def chronological_split(
    corpus: ReplayCorpus, training_fraction: float
) -> tuple[tuple[ReplayCase, ...], tuple[ReplayCase, ...]]:
    """Sort by time and split once; held-out cases are never used for updates."""

    ordered = tuple(
        sorted(corpus.cases, key=lambda item: (item.observed_at, item.case_id))
    )
    split_at = int(len(ordered) * training_fraction)
    return ordered[:split_at], ordered[split_at:]


def _result(
    name: str,
    status: GateStatus,
    observed: int | float | str | bool | None,
    threshold: int | float | str | bool | None,
    reason: str,
) -> CriterionResult:
    return CriterionResult(name, status, observed, threshold, reason)


def _empty_report(
    *,
    criteria: PromotionCriteria | None,
    corpus: ReplayCorpus | None,
    seed: int,
    result: CriterionResult,
    generated_at: datetime,
) -> PromotionReport:
    return PromotionReport(
        status=result.status,
        report_schema_version=PROMOTION_REPORT_SCHEMA_VERSION,
        policy_version=(
            criteria.policy_version
            if criteria is not None
            else corpus.policy_version
            if corpus is not None
            else None
        ),
        outcome_schema_version=(
            criteria.outcome_schema_version
            if criteria is not None
            else corpus.outcome_schema_version
            if corpus is not None
            else None
        ),
        evaluator_name=(
            criteria.evaluator_name
            if criteria is not None
            else corpus.evaluator_name
            if corpus is not None
            else None
        ),
        evaluator_version=(
            criteria.evaluator_version
            if criteria is not None
            else corpus.evaluator_version
            if corpus is not None
            else None
        ),
        corpus_schema_version=corpus.schema_version if corpus else None,
        corpus_version=corpus.corpus_version if corpus else None,
        criteria_schema_version=criteria.schema_version if criteria else None,
        criteria_version=criteria.criteria_version if criteria else None,
        policy_snapshot=criteria.policy_snapshot if criteria else None,
        policy_snapshot_sha256=(
            criteria.policy_snapshot.canonical_sha256() if criteria else None
        ),
        seed=seed,
        corpus_kind=corpus.kind.value if corpus is not None else None,
        synthetic=corpus is not None and corpus.kind == CorpusKind.SYNTHETIC,
        operator_review_eligible=False,
        activation_approved=False,
        generated_at=generated_at.isoformat(),
        criteria=(result,),
        metrics=None,
    )


def _version_mismatches(criteria: PromotionCriteria, corpus: ReplayCorpus) -> list[str]:
    mismatches: list[str] = []
    expected = {
        "criteria_schema": (
            criteria.schema_version,
            PROMOTION_CRITERIA_SCHEMA_VERSION,
        ),
        "criteria_corpus_schema": (
            criteria.corpus_schema_version,
            REPLAY_CORPUS_SCHEMA_VERSION,
        ),
        "corpus_schema": (
            corpus.schema_version,
            REPLAY_CORPUS_SCHEMA_VERSION,
        ),
        "criteria_outcome_schema": (
            criteria.outcome_schema_version,
            ADAPTIVE_OUTCOME_SCHEMA_VERSION,
        ),
        "corpus_outcome_schema": (
            corpus.outcome_schema_version,
            ADAPTIVE_OUTCOME_SCHEMA_VERSION,
        ),
        "criteria_policy": (
            criteria.policy_version,
            ADAPTIVE_POLICY_VERSION,
        ),
        "snapshot_policy": (
            criteria.policy_snapshot.policy_version,
            ADAPTIVE_POLICY_VERSION,
        ),
        "snapshot_outcome_schema": (
            criteria.policy_snapshot.outcome_schema_version,
            ADAPTIVE_OUTCOME_SCHEMA_VERSION,
        ),
        "replay_protocol": (
            criteria.policy_snapshot.replay_protocol_version,
            REPLAY_PROTOCOL_VERSION,
        ),
        "corpus_policy": (
            corpus.policy_version,
            ADAPTIVE_POLICY_VERSION,
        ),
        "evaluator_name": (corpus.evaluator_name, criteria.evaluator_name),
        "evaluator_version": (
            corpus.evaluator_version,
            criteria.evaluator_version,
        ),
    }
    for name, (actual, wanted) in expected.items():
        if actual != wanted:
            mismatches.append(f"{name}:{actual}!={wanted}")
    return mismatches


async def _replay(
    criteria: PromotionCriteria,
    corpus: ReplayCorpus,
    *,
    seed: int,
) -> ReplayMetrics:
    training, heldout = chronological_split(corpus, criteria.training_fraction)
    modes = tuple(
        sorted(
            {case.collaboration_mode for case in corpus.cases}
            | set(criteria.required_modes)
        )
    )

    def mode_case_counts() -> tuple[ModeCaseCounts, ...]:
        return tuple(
            ModeCaseCounts(
                collaboration_mode=mode.value,
                training_cases=sum(
                    case.collaboration_mode == mode for case in training
                ),
                heldout_cases=sum(case.collaboration_mode == mode for case in heldout),
            )
            for mode in modes
        )

    if not training or not heldout:
        return ReplayMetrics(
            len(corpus.cases),
            len(training),
            len(heldout),
            None,
            None,
            None,
            training[-1].observed_at.isoformat() if training else None,
            heldout[0].observed_at.isoformat() if heldout else None,
            mode_case_counts(),
            (),
        )

    # Historical replay advances an isolated logical clock with the training
    # sequence. Production's outcome-age rule still applies to live delivery,
    # but must not reject a valid archived corpus merely because its complete
    # time range exceeds the live retention window.
    replay_clock = [training[0].observed_at]
    policy = EvaluatorEligibilityPolicy(
        policy_version=criteria.policy_version,
        schema_version=criteria.outcome_schema_version,
        allowed_evaluators={
            criteria.evaluator_name: frozenset({criteria.evaluator_version})
        },
        clock=lambda: replay_clock[0],
    )
    router = MASRouter(
        config={
            "adaptive_routing_enabled": True,
            "adaptive_routing_min_history": (criteria.policy_snapshot.minimum_history),
            "adaptive_routing_min_samples_per_arm": (
                criteria.policy_snapshot.minimum_samples_per_arm
            ),
            "adaptive_routing_performance_threshold": (
                criteria.policy_snapshot.performance_threshold
            ),
            "adaptive_routing_max_worker_adjust": (
                criteria.policy_snapshot.maximum_worker_adjust
            ),
            "adaptive_routing_rng": np.random.default_rng(seed),
            "adaptive_routing_schema_version": criteria.outcome_schema_version,
            "adaptive_routing_policy_version": criteria.policy_version,
            "max_parallel": criteria.policy_snapshot.maximum_parallel_workers,
            "max_agents": criteria.policy_snapshot.maximum_agents_per_query,
        },
        outcome_eligibility_policy=policy,
    )

    def analysis(case: ReplayCase) -> SimpleNamespace:
        count = case.analytic_worker_count
        return SimpleNamespace(
            domains=[f"domain-{index}" for index in range(max(1, count - 1))],
            subtask_count=count,
            reasoning_types=[],
        )

    async def select(case: ReplayCase) -> int:
        proposal = await router._get_adaptive_allocation_adjustment(
            complexity_analysis=analysis(case),  # type: ignore[arg-type]
            collaboration_mode=case.collaboration_mode,
            episodic_prior=None,
        )
        if proposal is None:
            return 0
        _, attribution = router._allocate_agents_with_attribution(
            complexity_analysis=analysis(case),  # type: ignore[arg-type]
            collaboration_mode=case.collaboration_mode,
            adaptive_recommendation=proposal,
        )
        return attribution.applied_arm

    try:
        applied_counts: dict[tuple[CollaborationMode, int], int] = {}
        for index, case in enumerate(training):
            replay_clock[0] = case.observed_at
            for arm in criteria.policy_snapshot.ordered_arms:
                measurement = case.arm_measurements[arm]
                arm_id = f"n{abs(arm)}" if arm < 0 else f"p{arm}"
                application = await router.record_routing_outcome(
                    RoutingOutcome(
                        outcome_id=(f"promotion-train-{index}-{arm_id}-{case.case_id}"),
                        routing_id=f"promotion-route-{index}-{arm_id}-{case.case_id}",
                        policy_version=criteria.policy_version,
                        schema_version=criteria.outcome_schema_version,
                        source=OutcomeSource.EVALUATOR,
                        collaboration_mode=case.collaboration_mode,
                        proposed_arm=arm,
                        applied_arm=arm,
                        final_worker_count=max(1, case.analytic_worker_count + arm),
                        execution_status="completed",
                        latency_ms=measurement.latency_ms,
                        measured_cost=measurement.measured_cost,
                        cost_availability=(
                            MetricAvailability.MEASURED
                            if measurement.measured_cost is not None
                            else MetricAvailability.UNAVAILABLE
                        ),
                        quality_score=measurement.quality_score,
                        quality_availability=MetricAvailability.MEASURED,
                        evaluator_name=criteria.evaluator_name,
                        evaluator_version=criteria.evaluator_version,
                        recorded_at=case.observed_at,
                    )
                )
                if not application.learning_updated:
                    raise RuntimeError("training outcome was rejected")
                key = (case.collaboration_mode, arm)
                applied_counts[key] = applied_counts.get(key, 0) + 1

        quality_deltas: list[float] = []
        cost_deltas: list[float] = []
        latency_deltas: list[float] = []
        for case in heldout:
            arm = await select(case)
            candidate = case.arm_measurements[arm]
            control = case.arm_measurements[0]
            quality_deltas.append(
                _finite_difference(
                    candidate.quality_score,
                    control.quality_score,
                    "quality delta",
                )
            )
            if (
                candidate.measured_cost is not None
                and control.measured_cost is not None
            ):
                cost_deltas.append(
                    _finite_difference(
                        candidate.measured_cost,
                        control.measured_cost,
                        "cost delta",
                    )
                )
            if candidate.latency_ms is not None and control.latency_ms is not None:
                latency_deltas.append(
                    _finite_difference(
                        candidate.latency_ms,
                        control.latency_ms,
                        "latency delta",
                    )
                )

        return ReplayMetrics(
            total_cases=len(corpus.cases),
            training_cases=len(training),
            heldout_cases=len(heldout),
            mean_quality_delta=_finite_mean(
                quality_deltas, len(heldout), "mean quality delta"
            ),
            mean_cost_delta=_finite_mean(cost_deltas, len(heldout), "mean cost delta"),
            mean_latency_delta_ms=_finite_mean(
                latency_deltas, len(heldout), "mean latency delta"
            ),
            training_last_observed_at=training[-1].observed_at.isoformat(),
            heldout_first_observed_at=heldout[0].observed_at.isoformat(),
            mode_case_counts=mode_case_counts(),
            mode_arm_training_counts=tuple(
                ModeArmTrainingCount(
                    collaboration_mode=mode.value,
                    arm=arm,
                    applied_outcomes=applied_counts.get((mode, arm), 0),
                )
                for mode in modes
                for arm in criteria.policy_snapshot.ordered_arms
            ),
        )
    finally:
        await router.close()


def _threshold_result(
    name: str,
    observed: float | None,
    threshold: float | None,
    *,
    minimum: bool,
) -> CriterionResult:
    if threshold is None:
        return _result(
            name,
            GateStatus.INSUFFICIENT_EVIDENCE,
            observed,
            None,
            "no approved threshold is configured",
        )
    if observed is None:
        return _result(
            name,
            GateStatus.INSUFFICIENT_EVIDENCE,
            None,
            threshold,
            "the held-out corpus lacks complete measured values",
        )
    passed = observed >= threshold if minimum else observed <= threshold
    return _result(
        name,
        GateStatus.PASS if passed else GateStatus.FAIL,
        observed,
        threshold,
        "criterion satisfied" if passed else "criterion not satisfied",
    )


async def evaluate_promotion(
    *,
    criteria: PromotionCriteria | None,
    corpus: ReplayCorpus | None,
    seed: int = 42,
    generated_at: datetime | None = None,
) -> PromotionReport:
    """Evaluate explicit evidence without mutating runtime configuration."""

    generated = generated_at or datetime.now(UTC)
    if generated.tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")
    if criteria is None or corpus is None:
        missing = ", ".join(
            name
            for name, value in (("criteria", criteria), ("corpus", corpus))
            if value is None
        )
        return _empty_report(
            criteria=criteria,
            corpus=corpus,
            seed=seed,
            generated_at=generated,
            result=_result(
                "inputs_present",
                GateStatus.INSUFFICIENT_EVIDENCE,
                missing,
                "criteria and corpus",
                f"missing required input: {missing}",
            ),
        )

    mismatches = _version_mismatches(criteria, corpus)
    if mismatches:
        return _empty_report(
            criteria=criteria,
            corpus=corpus,
            seed=seed,
            generated_at=generated,
            result=_result(
                "version_compatibility",
                GateStatus.INSUFFICIENT_EVIDENCE,
                ";".join(mismatches),
                "exact version match",
                "incompatible input versions were rejected before replay",
            ),
        )

    if not criteria.approved_for_promotion:
        return _empty_report(
            criteria=criteria,
            corpus=corpus,
            seed=seed,
            generated_at=generated,
            result=_result(
                "criteria_approved",
                GateStatus.INSUFFICIENT_EVIDENCE,
                False,
                True,
                "criteria thresholds have not been approved",
            ),
        )

    if not corpus.evaluator_available:
        return _empty_report(
            criteria=criteria,
            corpus=corpus,
            seed=seed,
            generated_at=generated,
            result=_result(
                "product_evaluator_available",
                GateStatus.INSUFFICIENT_EVIDENCE,
                False,
                True,
                "the versioned product evaluator is not available",
            ),
        )

    out_of_scope_count = sum(
        case.collaboration_mode not in criteria.required_modes for case in corpus.cases
    )
    if out_of_scope_count:
        return _empty_report(
            criteria=criteria,
            corpus=corpus,
            seed=seed,
            generated_at=generated,
            result=_result(
                "promotion_scope_compatibility",
                GateStatus.INSUFFICIENT_EVIDENCE,
                out_of_scope_count,
                0,
                "the corpus contains collaboration modes outside the approved scope",
            ),
        )

    training, heldout = chronological_split(corpus, criteria.training_fraction)
    insufficient_counts = (
        len(corpus.cases) < criteria.minimum_total_cases
        or len(training) < criteria.minimum_training_cases
        or len(heldout) < criteria.minimum_heldout_cases
    )
    if insufficient_counts:
        readiness_observed = (
            f"total={len(corpus.cases)};training={len(training)};heldout={len(heldout)}"
        )
        readiness_threshold = (
            f"total>={criteria.minimum_total_cases};"
            f"training>={criteria.minimum_training_cases};"
            f"heldout>={criteria.minimum_heldout_cases}"
        )
        return _empty_report(
            criteria=criteria,
            corpus=corpus,
            seed=seed,
            generated_at=generated,
            result=_result(
                "eligible_sample_readiness",
                GateStatus.INSUFFICIENT_EVIDENCE,
                readiness_observed,
                readiness_threshold,
                "eligible chronological evidence does not meet sample requirements",
            ),
        )

    if training[-1].observed_at >= heldout[0].observed_at:
        return _empty_report(
            criteria=criteria,
            corpus=corpus,
            seed=seed,
            generated_at=generated,
            result=_result(
                "chronological_isolation",
                GateStatus.INSUFFICIENT_EVIDENCE,
                training[-1].observed_at.isoformat(),
                f"strictly before {heldout[0].observed_at.isoformat()}",
                "the train/held-out boundary is not strictly chronological",
            ),
        )

    try:
        metrics = await _replay(criteria, corpus, seed=seed)
    except Exception:
        return _empty_report(
            criteria=criteria,
            corpus=corpus,
            seed=seed,
            generated_at=generated,
            result=_result(
                "replay_execution",
                GateStatus.INSUFFICIENT_EVIDENCE,
                None,
                "successful isolated replay",
                "replay could not produce validated promotion evidence",
            ),
        )
    results: list[CriterionResult] = [
        _result(
            "exact_policy_replay_supported",
            GateStatus.INSUFFICIENT_EVIDENCE,
            False,
            True,
            "the diagnostic replay does not yet serialize every runtime policy behavior",
        ),
        _result(
            "criteria_approved",
            GateStatus.PASS,
            criteria.approved_for_promotion,
            True,
            "criteria are explicitly approved",
        ),
        _result(
            "corpus_promotional_eligibility",
            (
                GateStatus.PASS
                if corpus.kind == CorpusKind.EVALUATOR and corpus.representative
                else GateStatus.INSUFFICIENT_EVIDENCE
            ),
            f"{corpus.kind.value};representative={corpus.representative}",
            "evaluator;representative=true",
            (
                "evaluator-qualified representative corpus"
                if corpus.kind == CorpusKind.EVALUATOR and corpus.representative
                else "synthetic or non-representative evidence is non-promotional"
            ),
        ),
    ]
    for name, observed, threshold in (
        ("minimum_total_cases", metrics.total_cases, criteria.minimum_total_cases),
        (
            "minimum_training_cases",
            metrics.training_cases,
            criteria.minimum_training_cases,
        ),
        (
            "minimum_heldout_cases",
            metrics.heldout_cases,
            criteria.minimum_heldout_cases,
        ),
    ):
        results.append(
            _result(
                name,
                (
                    GateStatus.PASS
                    if observed >= threshold
                    else GateStatus.INSUFFICIENT_EVIDENCE
                ),
                observed,
                threshold,
                "criterion satisfied"
                if observed >= threshold
                else "eligible sample evidence is insufficient",
            )
        )
    mode_counts = {item.collaboration_mode: item for item in metrics.mode_case_counts}
    arm_counts = {
        (item.collaboration_mode, item.arm): item.applied_outcomes
        for item in metrics.mode_arm_training_counts
    }
    for mode in criteria.required_modes:
        counts = mode_counts.get(
            mode.value,
            ModeCaseCounts(mode.value, training_cases=0, heldout_cases=0),
        )
        for name, observed, threshold in (
            (
                f"{mode.value}.minimum_training_cases",
                counts.training_cases,
                criteria.minimum_training_cases_per_mode,
            ),
            (
                f"{mode.value}.minimum_heldout_cases",
                counts.heldout_cases,
                criteria.minimum_heldout_cases_per_mode,
            ),
            (
                f"{mode.value}.target_minimum_history",
                sum(
                    arm_counts.get((mode.value, arm), 0)
                    for arm in criteria.policy_snapshot.ordered_arms
                ),
                criteria.policy_snapshot.minimum_history,
            ),
        ):
            results.append(
                _result(
                    name,
                    (
                        GateStatus.PASS
                        if observed >= threshold
                        else GateStatus.INSUFFICIENT_EVIDENCE
                    ),
                    observed,
                    threshold,
                    "criterion satisfied"
                    if observed >= threshold
                    else "required collaboration-mode evidence is insufficient",
                )
            )
        per_arm_threshold = max(
            criteria.minimum_training_outcomes_per_arm,
            criteria.policy_snapshot.minimum_samples_per_arm,
        )
        for arm in criteria.policy_snapshot.ordered_arms:
            observed = arm_counts.get((mode.value, arm), 0)
            results.append(
                _result(
                    f"{mode.value}.arm_{arm}.minimum_training_outcomes",
                    (
                        GateStatus.PASS
                        if observed >= per_arm_threshold
                        else GateStatus.INSUFFICIENT_EVIDENCE
                    ),
                    observed,
                    per_arm_threshold,
                    "criterion satisfied"
                    if observed >= per_arm_threshold
                    else "required per-arm evidence is insufficient",
                )
            )
    results.extend(
        (
            _threshold_result(
                "mean_quality_delta",
                metrics.mean_quality_delta,
                criteria.minimum_mean_quality_delta,
                minimum=True,
            ),
            _threshold_result(
                "mean_cost_delta",
                metrics.mean_cost_delta,
                criteria.maximum_mean_cost_delta,
                minimum=False,
            ),
            _threshold_result(
                "mean_latency_delta_ms",
                metrics.mean_latency_delta_ms,
                criteria.maximum_mean_latency_delta_ms,
                minimum=False,
            ),
        )
    )
    if any(item.status == GateStatus.FAIL for item in results):
        status = GateStatus.FAIL
    elif any(item.status == GateStatus.INSUFFICIENT_EVIDENCE for item in results):
        status = GateStatus.INSUFFICIENT_EVIDENCE
    else:
        status = GateStatus.PASS

    return PromotionReport(
        status=status,
        report_schema_version=PROMOTION_REPORT_SCHEMA_VERSION,
        policy_version=criteria.policy_version,
        outcome_schema_version=criteria.outcome_schema_version,
        evaluator_name=criteria.evaluator_name,
        evaluator_version=criteria.evaluator_version,
        corpus_schema_version=corpus.schema_version,
        corpus_version=corpus.corpus_version,
        criteria_schema_version=criteria.schema_version,
        criteria_version=criteria.criteria_version,
        policy_snapshot=criteria.policy_snapshot,
        policy_snapshot_sha256=criteria.policy_snapshot.canonical_sha256(),
        seed=seed,
        corpus_kind=corpus.kind.value,
        synthetic=corpus.kind == CorpusKind.SYNTHETIC,
        operator_review_eligible=status == GateStatus.PASS,
        activation_approved=False,
        generated_at=generated.isoformat(),
        criteria=tuple(results),
        metrics=metrics,
    )


def load_criteria(path: Path) -> PromotionCriteria:
    payload = json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=_reject_json_constant,
    )
    return PromotionCriteria.from_dict(_strict_object(payload, "promotion criteria"))


def load_corpus(path: Path) -> ReplayCorpus:
    payload = json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=_reject_json_constant,
    )
    return ReplayCorpus.from_dict(_strict_object(payload, "replay corpus"))


def write_private_report(report: PromotionReport, output_path: Path) -> Path:
    """Write only to an explicit path outside the repository."""

    expanded = output_path.expanduser()
    if not expanded.name:
        raise ValueError("output must name a report file")
    unresolved = expanded if expanded.is_absolute() else Path.cwd() / expanded
    unresolved.parent.mkdir(parents=True, exist_ok=True)
    target = unresolved.parent.resolve() / unresolved.name
    repository = Path(__file__).resolve().parents[4]
    if target == repository or target.is_relative_to(repository):
        raise ValueError(
            "promotion reports are private and cannot be written in the repo"
        )
    if target.exists() or target.is_symlink():
        raise FileExistsError("refusing to overwrite an existing output path")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(target, flags, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(
            report.to_dict(),
            handle,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        handle.write("\n")
    return target


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the manual, non-activating MASR promotion gate."
    )
    parser.add_argument("--criteria", type=Path)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        criteria = load_criteria(args.criteria) if args.criteria else None
        corpus = load_corpus(args.corpus) if args.corpus else None
        report = asyncio.run(
            evaluate_promotion(
                criteria=criteria,
                corpus=corpus,
                seed=args.seed,
            )
        )
    except Exception:
        report = _empty_report(
            criteria=None,
            corpus=None,
            seed=args.seed,
            generated_at=datetime.now(UTC),
            result=_result(
                "input_validation",
                GateStatus.INSUFFICIENT_EVIDENCE,
                None,
                "strictly validated criteria and corpus",
                "one or more promotion inputs could not be validated",
            ),
        )
    written = write_private_report(report, args.output)
    print(f"Promotion report ({report.status.value}) written to {written}")
    if report.status == GateStatus.PASS:
        return 0
    if report.status == GateStatus.FAIL:
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
