"""Admission cancellation and publication failures close the idempotency slot."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import pytest

from src.core.contracts import CapabilityDecision, ToolInvocation, ToolInvocationStatus
from src.core.tools import (
    EVENT_COMPLETED,
    EVENT_REQUESTED,
    ToolAuditEvent,
    ToolBoundary,
    ToolCallContext,
    ToolOutcomeStatus,
)

from .conftest import (
    EchoInput,
    RecordingAuditStore,
    RecordingPublisher,
    invoke_kwargs,
)


@dataclass(slots=True)
class CoordinatedStore:
    """A store with a barrier before the requested write commits."""

    inner: RecordingAuditStore
    block_requested_persist: bool = False
    requested_persist_started: asyncio.Event = field(default_factory=asyncio.Event)
    release_requested_persist: asyncio.Event = field(default_factory=asyncio.Event)

    async def find_invocation(
        self,
        *,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
    ) -> ToolInvocation | None:
        return await self.inner.find_invocation(
            run_id=run_id,
            attempt_id=attempt_id,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
        )

    async def persist(
        self,
        *,
        invocation: ToolInvocation,
        events: Sequence[ToolAuditEvent],
        organization_id: str | None,
        capability_decision: CapabilityDecision | None,
    ) -> None:
        if (
            self.block_requested_persist
            and invocation.status is ToolInvocationStatus.REQUESTED
        ):
            self.requested_persist_started.set()
            await self.release_requested_persist.wait()
        await self.inner.persist(
            invocation=invocation,
            events=events,
            organization_id=organization_id,
            capability_decision=capability_decision,
        )


@dataclass(slots=True)
class CoordinatedPublisher:
    """A publisher with a requested-event barrier and one-shot failures."""

    calls: list[str]
    block_requested_publish: bool = False
    failures: dict[str, BaseException] = field(default_factory=dict)
    requested_publish_started: asyncio.Event = field(default_factory=asyncio.Event)
    release_requested_publish: asyncio.Event = field(default_factory=asyncio.Event)
    published: list[ToolAuditEvent] = field(default_factory=list)

    async def publish(self, events: Sequence[ToolAuditEvent]) -> None:
        for event in events:
            self.calls.append(f"publish:{event.event_type}")
            if event.event_type == EVENT_REQUESTED and self.block_requested_publish:
                self.requested_publish_started.set()
                await self.release_requested_publish.wait()
            failure = self.failures.pop(event.event_type, None)
            if failure is not None:
                raise failure
            self.published.append(event)


def _boundary_with_handler(
    boundary_dependencies: dict[str, Any],
    echo_spec: Any,
    *,
    store: Any,
    publisher: Any,
    handler: Any,
) -> ToolBoundary:
    boundary = ToolBoundary(
        **{
            **boundary_dependencies,
            "audit_store": store,
            "event_publisher": publisher,
        }
    )
    boundary.register(replace(echo_spec, handler=handler))
    return boundary


async def _recording_handler(
    calls: list[str], args: EchoInput, context: ToolCallContext
) -> Mapping[str, Any]:
    calls.append("handler")
    return {"echoed": args.query}


@pytest.mark.asyncio
async def test_external_cancellation_during_requested_persist_is_settled_after_admission(
    boundary_dependencies: dict[str, Any], echo_spec: Any
) -> None:
    calls: list[str] = []
    durable = RecordingAuditStore(calls=calls)
    store = CoordinatedStore(durable, block_requested_persist=True)
    publisher = RecordingPublisher(calls=calls)
    handler_calls: list[str] = []
    boundary = _boundary_with_handler(
        boundary_dependencies,
        echo_spec,
        store=store,
        publisher=publisher,
        handler=lambda args, context: _recording_handler(handler_calls, args, context),
    )

    call = asyncio.create_task(boundary.invoke(**invoke_kwargs()))
    await store.requested_persist_started.wait()
    assert handler_calls == []

    call.cancel()
    await asyncio.sleep(0)
    try:
        assert not call.done(), "cancellation must wait for the protected admission"
    finally:
        store.release_requested_persist.set()

    with pytest.raises(asyncio.CancelledError):
        await call

    assert handler_calls == []
    assert [invocation.status for invocation in durable.invocations] == [
        ToolInvocationStatus.REQUESTED,
        ToolInvocationStatus.CANCELLED,
    ]
    assert calls == [
        "persist:requested",
        f"publish:{EVENT_REQUESTED}",
        "persist:cancelled",
        f"publish:{EVENT_COMPLETED}",
    ]


@pytest.mark.asyncio
async def test_external_cancellation_during_requested_publish_is_settled_after_admission(
    boundary_dependencies: dict[str, Any], echo_spec: Any
) -> None:
    calls: list[str] = []
    durable = RecordingAuditStore(calls=calls)
    store = CoordinatedStore(durable)
    publisher = CoordinatedPublisher(calls=calls, block_requested_publish=True)
    handler_calls: list[str] = []
    boundary = _boundary_with_handler(
        boundary_dependencies,
        echo_spec,
        store=store,
        publisher=publisher,
        handler=lambda args, context: _recording_handler(handler_calls, args, context),
    )

    call = asyncio.create_task(boundary.invoke(**invoke_kwargs()))
    await publisher.requested_publish_started.wait()
    assert handler_calls == []

    call.cancel()
    await asyncio.sleep(0)
    try:
        assert not call.done(), "cancellation must wait for the protected admission"
    finally:
        publisher.release_requested_publish.set()

    with pytest.raises(asyncio.CancelledError):
        await call

    assert handler_calls == []
    assert [invocation.status for invocation in durable.invocations] == [
        ToolInvocationStatus.REQUESTED,
        ToolInvocationStatus.CANCELLED,
    ]
    assert calls == [
        "persist:requested",
        f"publish:{EVENT_REQUESTED}",
        "persist:cancelled",
        f"publish:{EVENT_COMPLETED}",
    ]


@pytest.mark.asyncio
async def test_requested_publication_cancellation_is_settled_as_terminal_cancellation(
    boundary_dependencies: dict[str, Any], echo_spec: Any
) -> None:
    calls: list[str] = []
    durable = RecordingAuditStore(calls=calls)
    publisher = CoordinatedPublisher(
        calls=calls,
        failures={EVENT_REQUESTED: asyncio.CancelledError()},
    )
    handler_calls: list[str] = []
    boundary = _boundary_with_handler(
        boundary_dependencies,
        echo_spec,
        store=durable,
        publisher=publisher,
        handler=lambda args, context: _recording_handler(handler_calls, args, context),
    )

    with pytest.raises(asyncio.CancelledError):
        await boundary.invoke(**invoke_kwargs(idempotency_key="publisher-cancel-key"))

    assert handler_calls == []
    assert [invocation.status for invocation in durable.invocations] == [
        ToolInvocationStatus.REQUESTED,
        ToolInvocationStatus.CANCELLED,
    ]
    assert calls == [
        "persist:requested",
        f"publish:{EVENT_REQUESTED}",
        "persist:cancelled",
        f"publish:{EVENT_COMPLETED}",
    ]


@pytest.mark.asyncio
async def test_requested_publication_failure_is_recorded_as_a_terminal_failure(
    boundary_dependencies: dict[str, Any], echo_spec: Any
) -> None:
    calls: list[str] = []
    durable = RecordingAuditStore(calls=calls)
    publisher = CoordinatedPublisher(
        calls=calls,
        failures={EVENT_REQUESTED: RuntimeError("requested publication failed")},
    )
    handler_calls: list[str] = []
    boundary = _boundary_with_handler(
        boundary_dependencies,
        echo_spec,
        store=durable,
        publisher=publisher,
        handler=lambda args, context: _recording_handler(handler_calls, args, context),
    )

    first = await boundary.invoke(**invoke_kwargs(idempotency_key="publish-key"))

    assert first.status is ToolOutcomeStatus.FAILED
    assert first.error_code == "tool_error"
    assert first.invocation.output is None
    assert first.invocation.status_reason is not None
    assert "requested publication failed" in first.invocation.status_reason
    assert handler_calls == []
    assert [invocation.status for invocation in durable.invocations] == [
        ToolInvocationStatus.REQUESTED,
        ToolInvocationStatus.FAILED,
    ]

    replay = await boundary.invoke(**invoke_kwargs(idempotency_key="publish-key"))

    assert replay.status is ToolOutcomeStatus.FAILED
    assert replay.invocation.tool_invocation_id == first.invocation.tool_invocation_id
    assert calls == [
        "persist:requested",
        f"publish:{EVENT_REQUESTED}",
        "persist:failed",
        f"publish:{EVENT_COMPLETED}",
    ]


@pytest.mark.asyncio
async def test_terminal_publication_failure_leaves_the_durable_failure_authoritative(
    boundary_dependencies: dict[str, Any], echo_spec: Any
) -> None:
    calls: list[str] = []
    durable = RecordingAuditStore(calls=calls)
    publisher = CoordinatedPublisher(
        calls=calls,
        failures={
            EVENT_REQUESTED: RuntimeError("requested publication failed"),
            EVENT_COMPLETED: RuntimeError("terminal publication failed"),
        },
    )
    handler_calls: list[str] = []
    boundary = _boundary_with_handler(
        boundary_dependencies,
        echo_spec,
        store=durable,
        publisher=publisher,
        handler=lambda args, context: _recording_handler(handler_calls, args, context),
    )

    with pytest.raises(RuntimeError, match="terminal publication failed"):
        await boundary.invoke(**invoke_kwargs(idempotency_key="terminal-key"))

    recorded = await durable.find_invocation(
        run_id="run-1",
        attempt_id="attempt-1",
        organization_id="org-1",
        idempotency_key="terminal-key",
    )
    assert recorded is not None
    assert recorded.status is ToolInvocationStatus.FAILED
    assert recorded.output is None
    assert handler_calls == []

    replay = await boundary.invoke(**invoke_kwargs(idempotency_key="terminal-key"))

    assert replay.status is ToolOutcomeStatus.FAILED
    assert replay.invocation.tool_invocation_id == recorded.tool_invocation_id
    assert calls == [
        "persist:requested",
        f"publish:{EVENT_REQUESTED}",
        "persist:failed",
        f"publish:{EVENT_COMPLETED}",
    ]
