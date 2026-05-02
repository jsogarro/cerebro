"""Characterization tests for the datetime.utcnow → datetime.now(timezone.utc) codemod.

These tests pin the BEHAVIOR that must be preserved across the migration:

1. Default factories produce a wall-clock UTC timestamp within tight tolerance of `now`.
2. Datetime arithmetic (delta computation) works on values produced by the codebase.
3. Datetime ordering / comparison works on values produced by the codebase.
4. JSON serialization round-trips through ISO-8601 without losing wall-clock value.
5. Naive-vs-aware mixing is detected before it silently corrupts arithmetic — the
   regression-catcher for partial codemods.

These tests deliberately do NOT pin `tzinfo is None` or `tzinfo == timezone.utc`.
The migration's INTENT is to flip tzinfo from None to timezone.utc, so a tzinfo-pinning
test would fail the migration on its own success. Instead, we pin the behaviors a
consumer relies on regardless of representation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from src.models.masr_api_models import MASRErrorResponse
from src.models.report import ReportMetadata
from src.models.research_project import ResearchProject, ResearchQuery
from src.models.supervisor_api_models import SupervisorType, SupervisorWebSocketEvent
from src.models.websocket_messages import (
    ConnectionInfo,
    HeartbeatMessage,
    ProgressUpdate,
    WSMessage,
    WSMessageType,
)
from src.reliability.health_checks import (
    HealthCheckResult,
    HealthStatus,
)
from src.reliability.service_registry import (
    ServiceInstance,
    ServiceMetadata,
)

# Tolerance: defaults are evaluated at object construction; any drift greater
# than this implies a substantive change in semantics, not normal jitter.
WALL_CLOCK_TOLERANCE = timedelta(seconds=10)


def _to_utc_naive_view(dt: datetime) -> datetime:
    """Project a datetime to a naive view in UTC for tolerance comparison.

    Treats either a naive (assumed UTC) or aware (timezone.utc) datetime as the
    same wall-clock instant, so the assertion is timezone-representation-agnostic.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def _now_utc_naive_view() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _assert_close_to_now(dt: datetime, *, tolerance: timedelta = WALL_CLOCK_TOLERANCE) -> None:
    """Assert wall-clock value of dt is within `tolerance` of current UTC, regardless of tzinfo."""
    delta = abs(_now_utc_naive_view() - _to_utc_naive_view(dt))
    assert delta <= tolerance, (
        f"datetime {dt!r} differs from current UTC by {delta} (> {tolerance})"
    )


def _build_health_check_result() -> HealthCheckResult:
    return HealthCheckResult(
        component="char-test",
        status=HealthStatus.HEALTHY,
        latency_ms=1.0,
    )


def _build_service_instance() -> ServiceInstance:
    return ServiceInstance(
        metadata=ServiceMetadata(
            name="svc",
            version="0.0.1",
            instance_id="i-1",
            host="localhost",
            port=8080,
        ),
    )


# ---------------------------------------------------------------------------
# 1. Pydantic default_factory behaviors
# ---------------------------------------------------------------------------


class TestPydanticDefaultFactories:
    """Pin behavior of every Pydantic model that uses a default_factory for its timestamp field."""

    def test_wsmessage_timestamp_default_close_to_now(self) -> None:
        msg = WSMessage(type=WSMessageType.PROGRESS)
        _assert_close_to_now(msg.timestamp)

    def test_progressupdate_last_updated_default_close_to_now(self) -> None:
        upd = ProgressUpdate()
        _assert_close_to_now(upd.last_updated)

    def test_connectioninfo_timestamps_close_to_now(self) -> None:
        conn = ConnectionInfo(client_id="c1", client_type="web")
        _assert_close_to_now(conn.connected_at)
        _assert_close_to_now(conn.last_heartbeat)

    def test_heartbeat_message_timestamp_close_to_now(self) -> None:
        hb = HeartbeatMessage(client_id="c1")
        _assert_close_to_now(hb.timestamp)

    def test_masr_error_response_timestamp_close_to_now(self) -> None:
        err = MASRErrorResponse(error="boom", error_code="TEST")
        _assert_close_to_now(err.timestamp)

    def test_supervisor_websocket_event_timestamp_close_to_now(self) -> None:
        evt = SupervisorWebSocketEvent(
            event_type="status_update",
            supervisor_type=SupervisorType.RESEARCH,
            data={},
        )
        _assert_close_to_now(evt.timestamp)

    def test_research_project_timestamps_close_to_now(self) -> None:
        proj = ResearchProject(
            title="characterization",
            query=ResearchQuery(text="characterization", domains=["general"]),
            user_id="user-1",
        )
        _assert_close_to_now(proj.created_at)
        _assert_close_to_now(proj.updated_at)

    def test_report_metadata_generated_at_close_to_now(self) -> None:
        meta = ReportMetadata()
        _assert_close_to_now(meta.generated_at)


# ---------------------------------------------------------------------------
# 2. Dataclass field(default_factory=...) behaviors
# ---------------------------------------------------------------------------


class TestDataclassDefaultFactories:
    """Pin behavior of every @dataclass that uses a default_factory for its timestamp field."""

    def test_health_check_result_timestamp_close_to_now(self) -> None:
        result = _build_health_check_result()
        _assert_close_to_now(result.timestamp)

    def test_service_instance_registered_at_close_to_now(self) -> None:
        instance = _build_service_instance()
        _assert_close_to_now(instance.registered_at)
        _assert_close_to_now(instance.last_heartbeat)


# ---------------------------------------------------------------------------
# 3. Arithmetic / comparison behaviors
# ---------------------------------------------------------------------------


class TestDatetimeArithmeticInvariants:
    """Pin invariants that arithmetic and comparison code paths depend on.

    These tests catch the common refactor-breakage pattern: partial migration
    leaves some sites naive and others aware, and `(aware - naive)` raises
    TypeError. After the codemod, every code-produced datetime must be
    self-consistent with every other code-produced datetime.
    """

    def test_two_consecutive_defaults_produce_orderable_pair(self) -> None:
        first = WSMessage(type=WSMessageType.PROGRESS).timestamp
        second = WSMessage(type=WSMessageType.PROGRESS).timestamp
        # Both produced by same code path => must be comparable without TypeError.
        assert first <= second

    def test_arithmetic_between_two_dataclass_defaults_produces_timedelta(self) -> None:
        t1 = _build_health_check_result().timestamp
        t2 = _build_health_check_result().timestamp
        delta = t2 - t1
        assert isinstance(delta, timedelta)
        assert delta >= timedelta(0)

    def test_arithmetic_across_pydantic_and_dataclass_defaults(self) -> None:
        """Cross-cutting check: Pydantic and dataclass defaults must stay
        compatible with each other (both naive or both aware — never mixed)."""
        pyd_ts = WSMessage(type=WSMessageType.PROGRESS).timestamp
        dc_ts = _build_health_check_result().timestamp
        # Must not raise TypeError "can't subtract offset-naive and offset-aware".
        delta = abs(pyd_ts - dc_ts)
        assert delta <= WALL_CLOCK_TOLERANCE


# ---------------------------------------------------------------------------
# 4. Serialization behaviors
# ---------------------------------------------------------------------------


class TestSerializationRoundTrip:
    """Pin that JSON serialization preserves wall-clock value across the codemod.

    Pydantic V2's default datetime serializer emits ISO-8601 for both naive and
    aware datetimes; aware ones gain a `+00:00` (or `Z`) suffix. Either form
    must round-trip back to a datetime within tolerance of the original.
    """

    def test_pydantic_model_json_round_trip_preserves_wall_clock(self) -> None:
        original = WSMessage(type=WSMessageType.PROGRESS)
        as_json = original.model_dump_json()
        as_dict = json.loads(as_json)
        rebuilt = WSMessage.model_validate(as_dict)
        delta = abs(_to_utc_naive_view(rebuilt.timestamp) - _to_utc_naive_view(original.timestamp))
        assert delta < timedelta(seconds=1), (
            f"round-trip drifted by {delta}: {original.timestamp} vs {rebuilt.timestamp}"
        )

    def test_iso8601_string_parseable(self) -> None:
        """Whatever Pydantic emits for the timestamp must be ISO-8601 parseable."""
        msg = WSMessage(type=WSMessageType.PROGRESS)
        as_dict = json.loads(msg.model_dump_json())
        ts_str = as_dict["timestamp"]
        # `datetime.fromisoformat` accepts both naive and `+00:00`-suffixed strings on 3.11+.
        parsed = datetime.fromisoformat(ts_str)
        _assert_close_to_now(parsed)


# ---------------------------------------------------------------------------
# 5. Mixed naive/aware detection (regression-catcher)
# ---------------------------------------------------------------------------


class TestNaiveAwareConsistency:
    """All defaults produced by the codebase must share the same tzinfo state.

    If the refactor lands in some files but not others, these tests catch the
    inconsistency before it manifests as a TypeError in arithmetic at runtime.
    """

    def test_all_pydantic_defaults_have_consistent_tzinfo(self) -> None:
        defaults: list[datetime] = [
            WSMessage(type=WSMessageType.PROGRESS).timestamp,
            ProgressUpdate().last_updated,
            ConnectionInfo(client_id="c", client_type="web").connected_at,
            HeartbeatMessage(client_id="c").timestamp,
            MASRErrorResponse(error="e", error_code="c").timestamp,
            SupervisorWebSocketEvent(
                event_type="status_update",
                supervisor_type=SupervisorType.RESEARCH,
                data={},
            ).timestamp,
            ResearchProject(
                title="t", query=ResearchQuery(text="q", domains=["g"]), user_id="u"
            ).created_at,
            ReportMetadata().generated_at,
        ]
        all_naive = all(d.tzinfo is None for d in defaults)
        all_aware = all(d.tzinfo is not None for d in defaults)
        assert all_naive or all_aware, (
            "mixed naive/aware Pydantic defaults — partial codemod will break arithmetic: "
            f"{[(type(d).__name__, d.tzinfo) for d in defaults]}"
        )

    def test_all_dataclass_defaults_have_consistent_tzinfo(self) -> None:
        defaults = [
            _build_health_check_result().timestamp,
            _build_service_instance().registered_at,
            _build_service_instance().last_heartbeat,
        ]
        all_naive = all(d.tzinfo is None for d in defaults)
        all_aware = all(d.tzinfo is not None for d in defaults)
        assert all_naive or all_aware

    def test_pydantic_and_dataclass_defaults_have_consistent_tzinfo(self) -> None:
        """The whole codebase must agree — Pydantic and dataclasses both."""
        pyd = WSMessage(type=WSMessageType.PROGRESS).timestamp
        dc = _build_health_check_result().timestamp
        assert (pyd.tzinfo is None) == (dc.tzinfo is None), (
            f"Pydantic tzinfo={pyd.tzinfo!r} vs dataclass tzinfo={dc.tzinfo!r} — "
            "partial codemod detected; arithmetic across these will raise TypeError."
        )


# ---------------------------------------------------------------------------
# 6. Local helper sanity (these helpers are part of the test contract)
# ---------------------------------------------------------------------------


class TestCharacterizationHelpers:
    def test_to_utc_naive_view_naive_passthrough(self) -> None:
        naive = datetime(2026, 1, 1, 12, 0, 0)
        assert _to_utc_naive_view(naive) == naive

    def test_to_utc_naive_view_aware_strips_tz(self) -> None:
        aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = _to_utc_naive_view(aware)
        assert result.tzinfo is None
        assert result == datetime(2026, 1, 1, 12, 0, 0)

    def test_assert_close_to_now_accepts_naive(self) -> None:
        # Construct a naive datetime explicitly so this test exercises the
        # naive branch of _to_utc_naive_view regardless of which form the
        # codebase produces.
        naive_now = datetime.now(UTC).replace(tzinfo=None)
        _assert_close_to_now(naive_now)

    def test_assert_close_to_now_accepts_aware(self) -> None:
        _assert_close_to_now(datetime.now(UTC))

    def test_assert_close_to_now_rejects_far_past(self) -> None:
        with pytest.raises(AssertionError):
            _assert_close_to_now(datetime(2020, 1, 1))
