"""Stream cursor and delivery-key semantics for the durable run event stream.

Run events are persisted with a per-run monotonically increasing ``sequence``.
That sequence is the only cursor a stream client needs: a client resumes by
telling the server the last sequence it processed, and the server replays
everything after it.

Delivery is at-least-once. A consumer may legitimately observe the same event
more than once — after a reconnect, a relay retry, or a redelivery following a
crash between delivery and acknowledgement. Consumers must therefore dedupe on
the delivery idempotency key derived here, which is stable across redeliveries
of the same event to the same destination.
"""

import base64
import binascii
import json
from typing import Self

from pydantic import Field, ValidationError

from .base import ContractId, ContractModel, IdempotencyKey


class RunEventCursor(ContractModel):
    """A resumable position in one run's event stream.

    ``last_sequence`` is the sequence of the last event the holder has
    processed. Zero means "nothing processed yet"; a replay from this cursor
    starts at sequence 1.
    """

    run_id: ContractId
    last_sequence: int = Field(default=0, ge=0)

    def advanced_to(self, sequence: int) -> Self:
        """Return the cursor moved forward to ``sequence``.

        Moving backwards or standing still is rejected: a cursor that can
        regress would silently replay or skip events on resume.
        """

        if sequence <= self.last_sequence:
            raise ValueError(
                f"Cursor for run {self.run_id!r} cannot move from "
                f"{self.last_sequence} to {sequence}"
            )
        return type(self).model_validate(
            self.model_dump() | {"last_sequence": sequence}
        )

    def encode(self) -> str:
        """Return an opaque, URL-safe resume token."""

        return base64.urlsafe_b64encode(self.canonical_json().encode("utf-8")).decode(
            "ascii"
        )

    @classmethod
    def decode(cls, token: str) -> Self:
        """Rebuild a cursor from a resume token, rejecting anything malformed."""

        if not token:
            raise ValueError("Resume token must not be empty")
        try:
            raw = base64.urlsafe_b64decode(token.encode("ascii"))
            payload = json.loads(raw)
        except (binascii.Error, UnicodeEncodeError, ValueError) as error:
            raise ValueError("Resume token is not a valid cursor") from error
        try:
            return cls.model_validate(payload)
        except ValidationError as error:
            raise ValueError("Resume token is not a valid cursor") from error


def delivery_idempotency_key(
    *, deduplication_key: str, destination: str
) -> IdempotencyKey:
    """Return the key a consumer dedupes redeliveries on.

    The key is derived rather than generated so that a redelivery produced by a
    different process, after a restart, or from a replayed outbox row resolves
    to the same value. Scoping by destination keeps independent consumers from
    suppressing each other's first delivery.
    """

    cleaned_key = deduplication_key.strip()
    cleaned_destination = destination.strip()
    if not cleaned_key:
        raise ValueError("deduplication_key must not be empty")
    if not cleaned_destination:
        raise ValueError("destination must not be empty")
    return f"{cleaned_destination}:{cleaned_key}"
