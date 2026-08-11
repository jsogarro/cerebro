"""Persist before publish, and correlate everything back to a durable record.

Wave 3 established persist-before-publish and proved with a crash matrix why it
is not a style preference: an event delivered before its row is committed is a
fact a subscriber holds and the system can never reproduce. Wave 3 also found
the opposite failure — a stream closing without delivering its terminal event.
Both are ordering defects, and both are invisible on the happy path, so they
are tested by watching the order rather than by watching the result.

Packet 4-Char's fourth finding closes here too: no public method on the
existing integration threads a run, task, or attempt identifier, so nothing
could correlate a tool call to a durable record. Every event carries all three.
"""

from typing import Any

import pytest

from src.core.contracts.provenance import ToolInvocation, ToolInvocationStatus
from src.core.tools import (
    EVENT_COMPLETED,
    EVENT_REQUESTED,
    ToolBoundary,
    ToolOutcomeStatus,
)

from .conftest import (
    ATTEMPT_ID,
    RUN_ID,
    TASK_ID,
    RecordingAuditStore,
    RecordingPublisher,
    invoke_kwargs,
)


class TestNothingIsPublishedBeforeItIsDurable:
    async def test_every_publish_follows_its_own_persist(
        self, boundary: ToolBoundary, audit_store: RecordingAuditStore
    ) -> None:
        await boundary.invoke(**invoke_kwargs())

        assert audit_store.calls == [
            "persist:requested",
            f"publish:{EVENT_REQUESTED}",
            "persist:succeeded",
            f"publish:{EVENT_COMPLETED}",
        ]

    async def test_a_failed_write_publishes_nothing(
        self,
        boundary: ToolBoundary,
        audit_store: RecordingAuditStore,
        publisher: RecordingPublisher,
    ) -> None:
        """The ordering guarantee is only real when persistence fails."""

        audit_store.fail_on_persist = True

        with pytest.raises(RuntimeError, match="database is down"):
            await boundary.invoke(**invoke_kwargs())

        assert publisher.published == []
        assert audit_store.calls == ["persist-failed"]

    async def test_a_denied_call_is_still_recorded_and_published(
        self, boundary: ToolBoundary, audit_store: RecordingAuditStore
    ) -> None:
        """An audit trail with a hole where every denial goes is not a trail."""

        await boundary.invoke(**invoke_kwargs(grants=[]))

        assert audit_store.calls == [
            "persist:denied",
            f"publish:{EVENT_COMPLETED}",
        ]

    async def test_the_terminal_event_is_always_delivered(
        self, boundary: ToolBoundary, publisher: RecordingPublisher
    ) -> None:
        await boundary.invoke(**invoke_kwargs())

        assert publisher.published[-1].event_type == EVENT_COMPLETED
        assert publisher.published[-1].payload["outcome"] == "succeeded"


class TestEveryEventCorrelatesToItsRun:
    async def test_run_task_and_attempt_travel_with_each_event(
        self, boundary: ToolBoundary, publisher: RecordingPublisher
    ) -> None:
        await boundary.invoke(**invoke_kwargs())

        assert publisher.published
        for event in publisher.published:
            assert event.run_id == RUN_ID
            assert event.task_id == TASK_ID
            assert event.attempt_id == ATTEMPT_ID
            assert event.aggregate_type == "tool_invocation"
            assert event.correlation_id == RUN_ID

    async def test_each_event_points_at_the_invocation_it_describes(
        self, boundary: ToolBoundary, publisher: RecordingPublisher
    ) -> None:
        outcome = await boundary.invoke(**invoke_kwargs())

        assert {event.aggregate_id for event in publisher.published} == {
            outcome.invocation.tool_invocation_id
        }

    async def test_deduplication_keys_distinguish_the_two_events(
        self, boundary: ToolBoundary, publisher: RecordingPublisher
    ) -> None:
        """A shared key would let the terminal event be dropped as a duplicate."""

        await boundary.invoke(**invoke_kwargs())

        keys = [event.deduplication_key for event in publisher.published]
        assert len(set(keys)) == len(keys)


class TestARepeatedCallIsNotASecondCall:
    async def test_a_recorded_terminal_result_is_replayed_not_re_executed(
        self, boundary_dependencies: dict[str, Any], audit_store: RecordingAuditStore
    ) -> None:
        from src.core.contracts.capabilities import SensitivityClass
        from src.core.tools import ToolCallContext, ToolSpec

        from .conftest import EchoInput, EchoOutput

        runs: list[str] = []

        async def counting(args: Any, context: ToolCallContext) -> dict[str, str]:
            runs.append("ran")
            return {"echoed": args.query}

        boundary = ToolBoundary(**boundary_dependencies)
        boundary.register(
            ToolSpec(
                name="echo",
                version="1.0.0",
                sensitivity=SensitivityClass.READ_ONLY,
                input_model=EchoInput,
                output_model=EchoOutput,
                timeout_seconds=1.0,
                handler=counting,
            )
        )

        first = await boundary.invoke(**invoke_kwargs())
        audit_store.replay = first.invocation

        second = await boundary.invoke(**invoke_kwargs())

        assert runs == ["ran"]
        assert second.succeeded
        assert second.unwrap() == first.unwrap()
        assert (
            second.invocation.tool_invocation_id == first.invocation.tool_invocation_id
        )

    async def test_an_identical_call_derives_an_identical_key(
        self, boundary: ToolBoundary
    ) -> None:
        first = await boundary.invoke(**invoke_kwargs())
        second = await boundary.invoke(**invoke_kwargs())

        assert first.invocation.idempotency_key == second.invocation.idempotency_key

    async def test_a_different_argument_derives_a_different_key(
        self, boundary: ToolBoundary
    ) -> None:
        first = await boundary.invoke(**invoke_kwargs())
        second = await boundary.invoke(**invoke_kwargs(arguments={"query": "other"}))

        assert first.invocation.idempotency_key != second.invocation.idempotency_key

    async def test_a_new_attempt_is_a_new_invocation(
        self, boundary: ToolBoundary
    ) -> None:
        """A deliberate retry must not replay the previous attempt's result."""

        first = await boundary.invoke(**invoke_kwargs())
        second = await boundary.invoke(**invoke_kwargs(attempt_id="attempt-2"))

        assert first.invocation.idempotency_key != second.invocation.idempotency_key

    async def test_an_in_flight_record_is_not_replayed(
        self, boundary: ToolBoundary, audit_store: RecordingAuditStore
    ) -> None:
        """Only a terminal record answers for a call; a running one does not."""

        first = await boundary.invoke(**invoke_kwargs())
        audit_store.replay = ToolInvocation(
            **{
                **first.invocation.model_dump(),
                "status": ToolInvocationStatus.RUNNING,
                "output": None,
                "output_trust": None,
                "completed_at": None,
            }
        )

        second = await boundary.invoke(**invoke_kwargs())

        assert second.status is ToolOutcomeStatus.SUCCEEDED
        assert (
            second.invocation.tool_invocation_id != first.invocation.tool_invocation_id
        )
