"""Versioned, non-PII state primitives for adaptive routing.

The store boundary is deliberately small: load a validated snapshot and
atomically compare-and-set the next snapshot together with an opaque outcome
identifier.  Store failures are returned as data so base routing never depends
on Redis availability.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from .routing_outcome import (
    ADAPTIVE_ARMS,
    ADAPTIVE_OUTCOME_RETENTION_SECONDS,
    ADAPTIVE_OUTCOME_SCHEMA_VERSION,
    ADAPTIVE_POLICY_VERSION,
)

_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_EXPERIMENT = re.compile(
    r"^adaptive_allocation_"
    r"(fast_path|direct|parallel|hierarchical|debate|ensemble)$"
)


@dataclass(frozen=True)
class AdaptiveExperimentSnapshot:
    """Sufficient statistics for one collaboration-mode bandit."""

    experiment_id: str
    ordered_arms: tuple[int, ...]
    arm_counts: tuple[int, ...]
    arm_reward_sums: tuple[float, ...]
    arm_values: tuple[float, ...]
    alpha_params: tuple[float, ...]
    beta_params: tuple[float, ...]

    def __post_init__(self) -> None:
        if not _SAFE_EXPERIMENT.fullmatch(self.experiment_id):
            raise ValueError("experiment_id is not a recognized adaptive experiment")
        if self.ordered_arms != ADAPTIVE_ARMS:
            raise ValueError(f"ordered_arms must be exactly {ADAPTIVE_ARMS}")

        width = len(ADAPTIVE_ARMS)
        vectors = (
            self.arm_counts,
            self.arm_reward_sums,
            self.arm_values,
            self.alpha_params,
            self.beta_params,
        )
        if any(len(vector) != width for vector in vectors):
            raise ValueError("all adaptive experiment vectors must match arm order")
        if any(count < 0 for count in self.arm_counts):
            raise ValueError("arm counts cannot be negative")
        if any(not math.isfinite(value) for vector in vectors[1:] for value in vector):
            raise ValueError("adaptive experiment statistics must be finite")
        if any(value < 0.0 for value in self.arm_reward_sums):
            raise ValueError("arm reward sums cannot be negative")
        if any(not 0.0 <= value <= 1.0 for value in self.arm_values):
            raise ValueError("arm values must be in [0, 1]")
        if any(value <= 0.0 for value in (*self.alpha_params, *self.beta_params)):
            raise ValueError("posterior parameters must be positive")
        for count, reward_sum, value, alpha, beta in zip(
            self.arm_counts,
            self.arm_reward_sums,
            self.arm_values,
            self.alpha_params,
            self.beta_params,
            strict=True,
        ):
            if reward_sum > count + 1e-9:
                raise ValueError("arm reward sum cannot exceed its sample count")
            expected_value = reward_sum / count if count else 0.0
            if not math.isclose(value, expected_value, abs_tol=1e-9):
                raise ValueError("arm value is inconsistent with sufficient statistics")
            if not math.isclose(alpha, 1.0 + reward_sum, abs_tol=1e-9):
                raise ValueError("alpha posterior is inconsistent with rewards")
            if not math.isclose(beta, 1.0 + count - reward_sum, abs_tol=1e-9):
                raise ValueError("beta posterior is inconsistent with rewards")

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "ordered_arms": list(self.ordered_arms),
            "arm_counts": list(self.arm_counts),
            "arm_reward_sums": list(self.arm_reward_sums),
            "arm_values": list(self.arm_values),
            "alpha_params": list(self.alpha_params),
            "beta_params": list(self.beta_params),
        }

    @classmethod
    def from_dict(cls, value: Any) -> AdaptiveExperimentSnapshot:
        data = _strict_mapping(
            value,
            {
                "experiment_id",
                "ordered_arms",
                "arm_counts",
                "arm_reward_sums",
                "arm_values",
                "alpha_params",
                "beta_params",
            },
            "experiment",
        )
        return cls(
            experiment_id=_require_str(data["experiment_id"], "experiment_id"),
            ordered_arms=tuple(_int_list(data["ordered_arms"], "ordered_arms")),
            arm_counts=tuple(_int_list(data["arm_counts"], "arm_counts")),
            arm_reward_sums=tuple(
                _float_list(data["arm_reward_sums"], "arm_reward_sums")
            ),
            arm_values=tuple(_float_list(data["arm_values"], "arm_values")),
            alpha_params=tuple(_float_list(data["alpha_params"], "alpha_params")),
            beta_params=tuple(_float_list(data["beta_params"], "beta_params")),
        )


@dataclass(frozen=True)
class AdaptiveStateSnapshot:
    """Strict durable state; intentionally excludes request and provider data."""

    schema_version: str = ADAPTIVE_OUTCOME_SCHEMA_VERSION
    policy_version: str = ADAPTIVE_POLICY_VERSION
    revision: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ordered_arms: tuple[int, ...] = ADAPTIVE_ARMS
    experiments: tuple[AdaptiveExperimentSnapshot, ...] = ()
    mode_quality_baselines: tuple[tuple[str, float], ...] = ()
    eligible_outcome_count: int = 0
    ineligible_outcome_count: int = 0
    duplicate_outcome_count: int = 0
    fallback_count: int = 0
    processed_outcome_count: int = 0

    def __post_init__(self) -> None:
        if not _SAFE_VERSION.fullmatch(self.schema_version):
            raise ValueError("invalid adaptive schema version")
        if not _SAFE_VERSION.fullmatch(self.policy_version):
            raise ValueError("invalid adaptive policy version")
        if self.revision < 0:
            raise ValueError("snapshot revision cannot be negative")
        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")
        if self.ordered_arms != ADAPTIVE_ARMS:
            raise ValueError(f"ordered_arms must be exactly {ADAPTIVE_ARMS}")

        experiment_ids = [item.experiment_id for item in self.experiments]
        if len(experiment_ids) != len(set(experiment_ids)):
            raise ValueError("experiment snapshots must have unique identifiers")

        allowed_modes = {
            "fast_path",
            "direct",
            "parallel",
            "hierarchical",
            "debate",
            "ensemble",
        }
        mode_names = [mode for mode, _ in self.mode_quality_baselines]
        if len(mode_names) != len(set(mode_names)):
            raise ValueError("mode baselines must have unique keys")
        for mode, value in self.mode_quality_baselines:
            if mode not in allowed_modes:
                raise ValueError("unknown collaboration mode in state")
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError("mode quality baselines must be finite in [0, 1]")

        counters = (
            self.eligible_outcome_count,
            self.ineligible_outcome_count,
            self.duplicate_outcome_count,
            self.fallback_count,
            self.processed_outcome_count,
        )
        if any(value < 0 for value in counters):
            raise ValueError("adaptive counters cannot be negative")
        if (
            self.eligible_outcome_count + self.ineligible_outcome_count
            > self.processed_outcome_count
        ):
            raise ValueError("processed outcome count is inconsistent")

    def next_revision(self, **changes: Any) -> AdaptiveStateSnapshot:
        """Create a new revision with an updated timestamp."""

        return replace(
            self,
            revision=self.revision + 1,
            updated_at=datetime.now(UTC),
            **changes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "revision": self.revision,
            "updated_at": self.updated_at.isoformat(),
            "ordered_arms": list(self.ordered_arms),
            "experiments": [item.to_dict() for item in self.experiments],
            "mode_quality_baselines": dict(self.mode_quality_baselines),
            "eligible_outcome_count": self.eligible_outcome_count,
            "ineligible_outcome_count": self.ineligible_outcome_count,
            "duplicate_outcome_count": self.duplicate_outcome_count,
            "fallback_count": self.fallback_count,
            "processed_outcome_count": self.processed_outcome_count,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, value: Any) -> AdaptiveStateSnapshot:
        fields = {
            "schema_version",
            "policy_version",
            "revision",
            "updated_at",
            "ordered_arms",
            "experiments",
            "mode_quality_baselines",
            "eligible_outcome_count",
            "ineligible_outcome_count",
            "duplicate_outcome_count",
            "fallback_count",
            "processed_outcome_count",
        }
        data = _strict_mapping(value, fields, "state")
        experiments = data["experiments"]
        if not isinstance(experiments, list):
            raise ValueError("experiments must be a list")
        baselines = data["mode_quality_baselines"]
        if not isinstance(baselines, dict) or not all(
            isinstance(key, str) for key in baselines
        ):
            raise ValueError("mode_quality_baselines must be an object")

        updated_at = datetime.fromisoformat(
            _require_str(data["updated_at"], "updated_at")
        )
        return cls(
            schema_version=_require_str(data["schema_version"], "schema_version"),
            policy_version=_require_str(data["policy_version"], "policy_version"),
            revision=_require_int(data["revision"], "revision"),
            updated_at=updated_at,
            ordered_arms=tuple(_int_list(data["ordered_arms"], "ordered_arms")),
            experiments=tuple(
                AdaptiveExperimentSnapshot.from_dict(item) for item in experiments
            ),
            mode_quality_baselines=tuple(
                sorted(
                    (
                        key,
                        _require_float(value, f"mode_quality_baselines.{key}"),
                    )
                    for key, value in baselines.items()
                )
            ),
            eligible_outcome_count=_require_int(
                data["eligible_outcome_count"], "eligible_outcome_count"
            ),
            ineligible_outcome_count=_require_int(
                data["ineligible_outcome_count"], "ineligible_outcome_count"
            ),
            duplicate_outcome_count=_require_int(
                data["duplicate_outcome_count"], "duplicate_outcome_count"
            ),
            fallback_count=_require_int(data["fallback_count"], "fallback_count"),
            processed_outcome_count=_require_int(
                data["processed_outcome_count"], "processed_outcome_count"
            ),
        )

    @classmethod
    def from_json(cls, payload: str | bytes) -> AdaptiveStateSnapshot:
        try:
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("adaptive state is not valid JSON") from exc
        return cls.from_dict(value)


class StateLoadStatus(StrEnum):
    LOADED = "loaded"
    MISSING = "missing"
    CORRUPT = "corrupt"
    INCOMPATIBLE = "incompatible"
    ERROR = "error"


class StateWriteStatus(StrEnum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    CORRUPT = "corrupt"
    INCOMPATIBLE = "incompatible"
    ERROR = "error"


@dataclass(frozen=True)
class StateLoadResult:
    status: StateLoadStatus
    snapshot: AdaptiveStateSnapshot | None = None
    reason: str | None = None


@dataclass(frozen=True)
class StateWriteResult:
    status: StateWriteStatus
    snapshot: AdaptiveStateSnapshot | None = None
    reason: str | None = None


class AdaptiveStateStore(Protocol):
    """Async state authority used by the router."""

    async def load(self) -> StateLoadResult:
        """Load and validate the current snapshot."""

    async def compare_and_set(
        self,
        *,
        expected_revision: int,
        snapshot: AdaptiveStateSnapshot,
        outcome_id: str,
    ) -> StateWriteResult:
        """Atomically save state and mark an opaque outcome as processed."""


class InMemoryAdaptiveStateStore:
    """Deterministic store for tests and offline evaluation."""

    def __init__(self, snapshot: AdaptiveStateSnapshot | None = None) -> None:
        self._snapshot = snapshot
        self._processed_outcomes: set[str] = set()
        self._lock = asyncio.Lock()

    async def load(self) -> StateLoadResult:
        async with self._lock:
            if self._snapshot is None:
                return StateLoadResult(StateLoadStatus.MISSING)
            return StateLoadResult(StateLoadStatus.LOADED, self._snapshot)

    async def compare_and_set(
        self,
        *,
        expected_revision: int,
        snapshot: AdaptiveStateSnapshot,
        outcome_id: str,
    ) -> StateWriteResult:
        async with self._lock:
            if outcome_id in self._processed_outcomes:
                return StateWriteResult(StateWriteStatus.DUPLICATE, self._snapshot)
            current_revision = self._snapshot.revision if self._snapshot else 0
            if current_revision != expected_revision:
                return StateWriteResult(StateWriteStatus.CONFLICT, self._snapshot)
            if snapshot.revision != expected_revision + 1:
                return StateWriteResult(
                    StateWriteStatus.INCOMPATIBLE,
                    self._snapshot,
                    "next snapshot revision must increment exactly once",
                )
            self._snapshot = snapshot
            self._processed_outcomes.add(outcome_id)
            return StateWriteResult(StateWriteStatus.APPLIED, snapshot)


class AsyncRedisClient(Protocol):
    """Subset of the redis asyncio client used by the state store."""

    async def get(self, key: str) -> bytes | str | None:
        """Get one value."""

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        """Evaluate a Lua script."""


class RedisAdaptiveStateStore:
    """Injected-client Redis store with atomic, bounded idempotent CAS.

    Each opaque outcome gets a hashed marker with a finite retention window.
    State and marker keys share one Redis Cluster hash tag so the Lua operation
    is valid on both standalone and clustered Redis.
    """

    _COMPARE_AND_SET_SCRIPT = """
local seen = redis.call("EXISTS", KEYS[2])
if seen == 1 then
  return 2
end

local current = redis.call("GET", KEYS[1])
local revision = 0
if current then
  local ok, decoded = pcall(cjson.decode, current)
  if not ok or type(decoded) ~= "table" or type(decoded["revision"]) ~= "number" then
    return -1
  end
  revision = decoded["revision"]
end

if revision ~= tonumber(ARGV[1]) then
  return 0
end

redis.call("SET", KEYS[1], ARGV[2])
redis.call("SET", KEYS[2], "1", "EX", ARGV[3])
return 1
"""

    def __init__(
        self,
        client: AsyncRedisClient,
        *,
        schema_version: str = ADAPTIVE_OUTCOME_SCHEMA_VERSION,
        policy_version: str = ADAPTIVE_POLICY_VERSION,
        key_prefix: str = "cerebro:masr:adaptive",
        idempotency_retention_seconds: int = ADAPTIVE_OUTCOME_RETENTION_SECONDS,
    ) -> None:
        if not _SAFE_VERSION.fullmatch(schema_version):
            raise ValueError("invalid schema_version for Redis namespace")
        if not _SAFE_VERSION.fullmatch(policy_version):
            raise ValueError("invalid policy_version for Redis namespace")
        if idempotency_retention_seconds <= 0:
            raise ValueError("idempotency_retention_seconds must be positive")
        self._client = client
        namespace = f"{key_prefix}:{{{schema_version}:{policy_version}}}"
        self.state_key = f"{namespace}:state"
        self.outcome_key_prefix = f"{namespace}:outcome"
        self.schema_version = schema_version
        self.policy_version = policy_version
        self.idempotency_retention_seconds = idempotency_retention_seconds

    def outcome_key(self, outcome_id: str) -> str:
        """Return a non-reversible marker key for one opaque outcome."""

        digest = hashlib.sha256(outcome_id.encode("utf-8")).hexdigest()
        return f"{self.outcome_key_prefix}:{digest}"

    async def load(self) -> StateLoadResult:
        try:
            payload = await self._client.get(self.state_key)
        except Exception as exc:
            return StateLoadResult(StateLoadStatus.ERROR, reason=type(exc).__name__)
        if payload is None:
            return StateLoadResult(StateLoadStatus.MISSING)
        try:
            snapshot = AdaptiveStateSnapshot.from_json(payload)
        except ValueError as exc:
            return StateLoadResult(StateLoadStatus.CORRUPT, reason=str(exc))
        if (
            snapshot.schema_version != self.schema_version
            or snapshot.policy_version != self.policy_version
        ):
            return StateLoadResult(
                StateLoadStatus.INCOMPATIBLE,
                reason="snapshot namespace versions do not match",
            )
        return StateLoadResult(StateLoadStatus.LOADED, snapshot)

    async def compare_and_set(
        self,
        *,
        expected_revision: int,
        snapshot: AdaptiveStateSnapshot,
        outcome_id: str,
    ) -> StateWriteResult:
        if (
            snapshot.schema_version != self.schema_version
            or snapshot.policy_version != self.policy_version
            or snapshot.revision != expected_revision + 1
        ):
            return StateWriteResult(
                StateWriteStatus.INCOMPATIBLE,
                reason="snapshot versions or revision are incompatible",
            )
        try:
            result = await self._client.eval(
                self._COMPARE_AND_SET_SCRIPT,
                2,
                self.state_key,
                self.outcome_key(outcome_id),
                str(expected_revision),
                snapshot.to_json(),
                str(self.idempotency_retention_seconds),
            )
        except Exception as exc:
            return StateWriteResult(StateWriteStatus.ERROR, reason=type(exc).__name__)

        if result == 1:
            return StateWriteResult(StateWriteStatus.APPLIED, snapshot)
        if result == 2:
            return StateWriteResult(StateWriteStatus.DUPLICATE)
        if result == 0:
            return StateWriteResult(StateWriteStatus.CONFLICT)
        if result == -1:
            return StateWriteResult(
                StateWriteStatus.CORRUPT,
                reason="existing Redis state is corrupt",
            )
        return StateWriteResult(
            StateWriteStatus.ERROR,
            reason="unexpected Redis compare-and-set result",
        )


def empty_adaptive_snapshot(
    *,
    schema_version: str = ADAPTIVE_OUTCOME_SCHEMA_VERSION,
    policy_version: str = ADAPTIVE_POLICY_VERSION,
) -> AdaptiveStateSnapshot:
    """Create a timestamped empty snapshot."""

    return AdaptiveStateSnapshot(
        schema_version=schema_version,
        policy_version=policy_version,
        updated_at=datetime.now(UTC),
    )


def _strict_mapping(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    if set(value) != fields:
        missing = sorted(fields - set(value))
        extra = sorted(set(value) - fields)
        raise ValueError(f"{label} fields mismatch: missing={missing}, extra={extra}")
    return value


def _require_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _require_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _require_float(value: Any, field_name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


def _int_list(value: Any, field_name: str) -> list[int]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return [_require_int(item, field_name) for item in value]


def _float_list(value: Any, field_name: str) -> list[float]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return [_require_float(item, field_name) for item in value]


__all__ = [
    "AdaptiveExperimentSnapshot",
    "AdaptiveStateSnapshot",
    "AdaptiveStateStore",
    "AsyncRedisClient",
    "InMemoryAdaptiveStateStore",
    "RedisAdaptiveStateStore",
    "StateLoadResult",
    "StateLoadStatus",
    "StateWriteResult",
    "StateWriteStatus",
    "empty_adaptive_snapshot",
]
