"""Executable invariants for run event stream cursors and delivery keys."""

import pytest
from pydantic import ValidationError

from src.core.contracts import (
    RunEventCursor,
    delivery_idempotency_key,
)


def test_a_fresh_cursor_starts_before_the_first_event() -> None:
    cursor = RunEventCursor(run_id="run-1")

    assert cursor.last_sequence == 0


def test_cursor_rejects_a_negative_position() -> None:
    with pytest.raises(ValidationError):
        RunEventCursor(run_id="run-1", last_sequence=-1)


def test_cursor_advances_forward_only() -> None:
    cursor = RunEventCursor(run_id="run-1", last_sequence=4)

    assert cursor.advanced_to(5).last_sequence == 5


@pytest.mark.parametrize("sequence", [4, 3, 0])
def test_cursor_refuses_to_move_backwards_or_stall(sequence: int) -> None:
    cursor = RunEventCursor(run_id="run-1", last_sequence=4)

    with pytest.raises(ValueError):
        cursor.advanced_to(sequence)


def test_cursor_token_round_trips() -> None:
    cursor = RunEventCursor(run_id="run:with:colons", last_sequence=17)

    assert RunEventCursor.decode(cursor.encode()) == cursor


@pytest.mark.parametrize("token", ["", "not-base64!!", "eyJib2d1cyI6IHRydWV9"])
def test_cursor_decode_rejects_malformed_tokens(token: str) -> None:
    with pytest.raises(ValueError):
        RunEventCursor.decode(token)


def test_delivery_key_is_deterministic_for_the_same_destination() -> None:
    first = delivery_idempotency_key(
        deduplication_key="run-1:7:task.succeeded", destination="websocket"
    )
    second = delivery_idempotency_key(
        deduplication_key="run-1:7:task.succeeded", destination="websocket"
    )

    assert first == second


def test_delivery_key_is_scoped_per_destination() -> None:
    websocket = delivery_idempotency_key(
        deduplication_key="run-1:7:task.succeeded", destination="websocket"
    )
    redis = delivery_idempotency_key(
        deduplication_key="run-1:7:task.succeeded", destination="redis_pubsub"
    )

    assert websocket != redis


@pytest.mark.parametrize(
    ("deduplication_key", "destination"),
    [("", "websocket"), ("run-1:7:task.succeeded", ""), ("  ", "websocket")],
)
def test_delivery_key_requires_both_parts(
    deduplication_key: str, destination: str
) -> None:
    with pytest.raises(ValueError):
        delivery_idempotency_key(
            deduplication_key=deduplication_key, destination=destination
        )
