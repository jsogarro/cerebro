"""Lease-aware recovery and renewal at the mediated tool boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any

import pytest

from src.core.contracts import (
    CapabilityDecision,
    ProducerKind,
    ToolInvocation,
    ToolInvocationStatus,
    TrustClassification,
)
from src.core.tools import (
    EVENT_REQUESTED,
    ToolAuditEvent,
    ToolBoundary,
    ToolCallContext,
    ToolOutcomeStatus,
    ToolSpec,
)
from src.core.tools.errors import ToolInvocationConflictError

from .conftest import (
    ATTEMPT_ID,
    NOW,
    ORG_ID,
    RUN_ID,
    SCOPE,
    TASK_ID,
    TOOL_NAME,
    TOOL_VERSION,
    EchoInput,
    invoke_kwargs,
)


@dataclass(slots=True)
class LeaseAwareFakeStore:
    """Minimal lease store that exposes every boundary call for assertions."""

    reconciled: ToolInvocation | None = None
    renew_result: bool = True
    invocations: list[ToolInvocation] = field(default_factory=list)
    reconcile_calls: list[dict[str, Any]] = field(default_factory=list)
    renew_calls: list[dict[str, Any]] = field(default_factory=list)
    renew_called: asyncio.Event = field(default_factory=asyncio.Event)
    terminal_conflict: ToolInvocation | None = None

    async def find_invocation(
        self,
        *,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
    ) -> ToolInvocation | None:
        for invocation in reversed(self.invocations):
            if (
                invocation.run_id == run_id
                and invocation.attempt_id == attempt_id
                and organization_id == ORG_ID
                and invocation.idempotency_key == idempotency_key
                and invocation.status
                in {
                    ToolInvocationStatus.SUCCEEDED,
                    ToolInvocationStatus.FAILED,
                    ToolInvocationStatus.CANCELLED,
                    ToolInvocationStatus.TIMED_OUT,
                    ToolInvocationStatus.DENIED,
                }
            ):
                return invocation
        return None

    async def find_pending_invocation(
        self,
        *,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
    ) -> ToolInvocation | None:
        return self._pending_invocation(
            run_id=run_id,
            attempt_id=attempt_id,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
        )

    async def reconcile_pending_invocation(
        self,
        *,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
        now: Any,
    ) -> ToolInvocation | None:
        self.reconcile_calls.append(
            {
                "run_id": run_id,
                "attempt_id": attempt_id,
                "organization_id": organization_id,
                "idempotency_key": idempotency_key,
                "now": now,
            }
        )
        if self.reconciled is not None:
            return self.reconciled
        return self._pending_invocation(
            run_id=run_id,
            attempt_id=attempt_id,
            organization_id=organization_id,
            idempotency_key=idempotency_key,
        )

    async def renew_invocation_lease(
        self,
        *,
        tool_invocation_id: str,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
        lease_owner_id: str,
        now: Any,
        lease_expires_at: Any,
    ) -> bool:
        self.renew_calls.append(
            {
                "tool_invocation_id": tool_invocation_id,
                "run_id": run_id,
                "attempt_id": attempt_id,
                "organization_id": organization_id,
                "idempotency_key": idempotency_key,
                "lease_owner_id": lease_owner_id,
                "now": now,
                "lease_expires_at": lease_expires_at,
            }
        )
        self.renew_called.set()
        return self.renew_result

    async def persist(
        self,
        *,
        invocation: ToolInvocation,
        events: Sequence[ToolAuditEvent],
        organization_id: str | None,
        capability_decision: CapabilityDecision | None,
    ) -> None:
        if (
            invocation.status
            in {
                ToolInvocationStatus.SUCCEEDED,
                ToolInvocationStatus.FAILED,
                ToolInvocationStatus.CANCELLED,
                ToolInvocationStatus.TIMED_OUT,
            }
            and self.terminal_conflict is not None
        ):
            raise ToolInvocationConflictError(self.terminal_conflict)
        self.invocations.append(invocation)

    def _pending_invocation(
        self,
        *,
        run_id: str,
        attempt_id: str,
        organization_id: str | None,
        idempotency_key: str,
    ) -> ToolInvocation | None:
        for invocation in reversed(self.invocations):
            if (
                invocation.run_id == run_id
                and invocation.attempt_id == attempt_id
                and organization_id == ORG_ID
                and invocation.idempotency_key == idempotency_key
                and invocation.status
                in {
                    ToolInvocationStatus.REQUESTED,
                    ToolInvocationStatus.RUNNING,
                }
            ):
                return invocation
        return None


@dataclass(slots=True)
class BlockingRequestedPublisher:
    """Publisher that holds the REQUESTED event until the test releases it."""

    requested_publish_started: asyncio.Event = field(default_factory=asyncio.Event)
    release_requested_publish: asyncio.Event = field(default_factory=asyncio.Event)
    published: list[ToolAuditEvent] = field(default_factory=list)

    async def publish(self, events: Sequence[ToolAuditEvent]) -> None:
        for event in events:
            if event.event_type == EVENT_REQUESTED:
                self.requested_publish_started.set()
                await self.release_requested_publish.wait()
            self.published.append(event)


def _invocation(
    *,
    status: ToolInvocationStatus = ToolInvocationStatus.REQUESTED,
    invocation_id: str = "existing-invocation",
    idempotency_key: str = "same-request",
    lease_owner_id: str | None = "existing-owner",
    lease_expires_at: Any = None,
) -> ToolInvocation:
    terminal = status in {
        ToolInvocationStatus.SUCCEEDED,
        ToolInvocationStatus.FAILED,
        ToolInvocationStatus.CANCELLED,
        ToolInvocationStatus.TIMED_OUT,
        ToolInvocationStatus.DENIED,
    }
    return ToolInvocation(
        tool_invocation_id=invocation_id,
        run_id=RUN_ID,
        task_id=TASK_ID,
        attempt_id=ATTEMPT_ID,
        tool_name=TOOL_NAME,
        tool_version=TOOL_VERSION,
        status=status,
        capability_scope=SCOPE,
        idempotency_key=idempotency_key,
        input={"query": "hello"},
        input_trust=TrustClassification.USER_SUPPLIED,
        producer_kind=ProducerKind.SYSTEM,
        error_code="unknown_after_lease_expired" if terminal else None,
        status_reason=("the previous owner lease expired" if terminal else None),
        requested_at=NOW,
        completed_at=NOW if terminal else None,
        lease_owner_id=lease_owner_id if not terminal else None,
        lease_expires_at=lease_expires_at if not terminal else None,
    )


def _boundary(
    boundary_dependencies: dict[str, Any],
    echo_spec: ToolSpec,
    store: LeaseAwareFakeStore,
    handler: Any,
) -> ToolBoundary:
    boundary = ToolBoundary(
        **{**boundary_dependencies, "audit_store": store},
    )
    boundary.register(replace(echo_spec, handler=handler))
    return boundary


@pytest.mark.asyncio
async def test_new_request_has_owner_lease_and_terminal_clears_it(
    boundary_dependencies: dict[str, Any], echo_spec: ToolSpec
) -> None:
    store = LeaseAwareFakeStore()

    async def handler(args: EchoInput, context: ToolCallContext) -> Mapping[str, Any]:
        return {"echoed": args.query}

    outcome = await _boundary(boundary_dependencies, echo_spec, store, handler).invoke(
        **invoke_kwargs(idempotency_key="new-lease-request")
    )

    assert outcome.status is ToolOutcomeStatus.SUCCEEDED
    requested, terminal = store.invocations
    assert requested.status is ToolInvocationStatus.REQUESTED
    assert requested.lease_owner_id == requested.tool_invocation_id
    assert requested.lease_expires_at is not None
    assert requested.lease_expires_at > NOW + timedelta(
        seconds=echo_spec.timeout_seconds
    )
    assert terminal.status is ToolInvocationStatus.SUCCEEDED
    assert terminal.lease_owner_id is None
    assert terminal.lease_expires_at is None


@pytest.mark.asyncio
async def test_live_pending_lease_is_reconciled_before_handler_dispatch(
    boundary_dependencies: dict[str, Any], echo_spec: ToolSpec
) -> None:
    store = LeaseAwareFakeStore(
        reconciled=_invocation(
            lease_expires_at=NOW + timedelta(minutes=5),
        )
    )
    handler_calls: list[str] = []

    async def handler(args: EchoInput, context: ToolCallContext) -> Mapping[str, Any]:
        handler_calls.append("handler")
        return {"echoed": args.query}

    outcome = await _boundary(boundary_dependencies, echo_spec, store, handler).invoke(
        **invoke_kwargs(idempotency_key="same-request")
    )

    assert outcome.status is ToolOutcomeStatus.IN_PROGRESS
    assert outcome.invocation.tool_invocation_id == "existing-invocation"
    assert handler_calls == []
    assert store.invocations == []
    assert len(store.reconcile_calls) == 1
    assert store.reconcile_calls[0]["now"] == NOW


@pytest.mark.asyncio
async def test_reconciled_stale_lease_is_replayed_without_handler_dispatch(
    boundary_dependencies: dict[str, Any], echo_spec: ToolSpec
) -> None:
    store = LeaseAwareFakeStore(
        reconciled=_invocation(status=ToolInvocationStatus.FAILED)
    )
    handler_calls: list[str] = []

    async def handler(args: EchoInput, context: ToolCallContext) -> Mapping[str, Any]:
        handler_calls.append("handler")
        return {"echoed": args.query}

    outcome = await _boundary(boundary_dependencies, echo_spec, store, handler).invoke(
        **invoke_kwargs(idempotency_key="same-request")
    )

    assert outcome.status is ToolOutcomeStatus.FAILED
    assert outcome.invocation.tool_invocation_id == "existing-invocation"
    assert outcome.invocation.output is None
    assert handler_calls == []
    assert store.invocations == []


@pytest.mark.asyncio
async def test_live_handler_renews_owner_lease(
    boundary_dependencies: dict[str, Any], echo_spec: ToolSpec
) -> None:
    store = LeaseAwareFakeStore()
    handler_started = asyncio.Event()
    release_handler = asyncio.Event()

    async def handler(args: EchoInput, context: ToolCallContext) -> Mapping[str, Any]:
        handler_started.set()
        await release_handler.wait()
        return {"echoed": args.query}

    task = asyncio.create_task(
        _boundary(
            boundary_dependencies,
            replace(echo_spec, timeout_seconds=0.2),
            store,
            handler,
        ).invoke(**invoke_kwargs(idempotency_key="renew-while-active"))
    )
    await asyncio.wait_for(handler_started.wait(), timeout=1)
    await asyncio.wait_for(store.renew_called.wait(), timeout=1)
    release_handler.set()
    outcome = await task

    assert outcome.status is ToolOutcomeStatus.SUCCEEDED
    renewal = store.renew_calls[0]
    requested = store.invocations[0]
    assert renewal["tool_invocation_id"] == requested.tool_invocation_id
    assert renewal["lease_owner_id"] == requested.lease_owner_id
    assert renewal["lease_expires_at"] > renewal["now"]
    assert store.invocations[-1].lease_owner_id is None
    assert store.invocations[-1].lease_expires_at is None


@pytest.mark.asyncio
async def test_request_renews_lease_while_requested_publication_is_blocked(
    boundary_dependencies: dict[str, Any], echo_spec: ToolSpec
) -> None:
    store = LeaseAwareFakeStore()
    publisher = BlockingRequestedPublisher()
    handler_calls: list[str] = []

    async def handler(args: EchoInput, context: ToolCallContext) -> Mapping[str, Any]:
        handler_calls.append("handler")
        return {"echoed": args.query}

    boundary = ToolBoundary(
        **{
            **boundary_dependencies,
            "audit_store": store,
            "event_publisher": publisher,
        }
    )
    boundary.register(replace(echo_spec, timeout_seconds=0.03, handler=handler))

    first_call = asyncio.create_task(
        boundary.invoke(**invoke_kwargs(idempotency_key="renew-during-publication"))
    )
    await asyncio.wait_for(publisher.requested_publish_started.wait(), timeout=1)
    assert handler_calls == []

    try:
        await asyncio.wait_for(store.renew_called.wait(), timeout=0.2)
        assert handler_calls == []
        duplicate = await asyncio.wait_for(
            boundary.invoke(
                **invoke_kwargs(idempotency_key="renew-during-publication")
            ),
            timeout=1,
        )
    finally:
        publisher.release_requested_publish.set()

    outcome = await asyncio.wait_for(first_call, timeout=1)

    assert duplicate.status is ToolOutcomeStatus.IN_PROGRESS
    assert outcome.status is ToolOutcomeStatus.SUCCEEDED
    assert handler_calls == ["handler"]
    assert len(store.renew_calls) >= 1
    assert [invocation.status for invocation in store.invocations] == [
        ToolInvocationStatus.REQUESTED,
        ToolInvocationStatus.SUCCEEDED,
    ]


@pytest.mark.asyncio
async def test_lease_loss_during_requested_publication_never_dispatches_handler(
    boundary_dependencies: dict[str, Any], echo_spec: ToolSpec
) -> None:
    store = LeaseAwareFakeStore(renew_result=False)
    publisher = BlockingRequestedPublisher()
    handler_calls: list[str] = []

    async def handler(args: EchoInput, context: ToolCallContext) -> Mapping[str, Any]:
        handler_calls.append("handler")
        return {"echoed": args.query}

    boundary = ToolBoundary(
        **{
            **boundary_dependencies,
            "audit_store": store,
            "event_publisher": publisher,
        }
    )
    boundary.register(replace(echo_spec, timeout_seconds=0.03, handler=handler))

    first_call = asyncio.create_task(
        boundary.invoke(**invoke_kwargs(idempotency_key="no-dispatch-after-loss"))
    )
    await asyncio.wait_for(publisher.requested_publish_started.wait(), timeout=1)
    await asyncio.wait_for(store.renew_called.wait(), timeout=1)

    publisher.release_requested_publish.set()
    outcome = await asyncio.wait_for(first_call, timeout=1)

    assert outcome.status is ToolOutcomeStatus.FAILED
    assert outcome.error_code == "tool_error"
    assert handler_calls == []
    assert [invocation.status for invocation in store.invocations] == [
        ToolInvocationStatus.REQUESTED,
        ToolInvocationStatus.FAILED,
    ]


@pytest.mark.asyncio
async def test_late_terminal_conflict_returns_the_durable_failure(
    boundary_dependencies: dict[str, Any], echo_spec: ToolSpec
) -> None:
    store = LeaseAwareFakeStore(
        terminal_conflict=_invocation(
            status=ToolInvocationStatus.FAILED,
            invocation_id="late-owner",
            idempotency_key="late-owner-key",
            lease_owner_id=None,
            lease_expires_at=None,
        )
    )
    handler_calls: list[str] = []

    async def handler(args: EchoInput, context: ToolCallContext) -> Mapping[str, Any]:
        handler_calls.append("handler")
        return {"echoed": args.query}

    boundary = _boundary(
        boundary_dependencies,
        echo_spec,
        store,
        handler,
    )

    outcome = await boundary.invoke(**invoke_kwargs(idempotency_key="late-owner-key"))

    assert outcome.status is ToolOutcomeStatus.FAILED
    assert outcome.error_code == "unknown_after_lease_expired"
    assert outcome.invocation.output is None
    assert handler_calls == ["handler"]
    assert [invocation.status for invocation in store.invocations] == [
        ToolInvocationStatus.REQUESTED,
    ]


@pytest.mark.asyncio
async def test_renewal_failure_cancels_handler_and_records_failure_without_output(
    boundary_dependencies: dict[str, Any], echo_spec: ToolSpec
) -> None:
    store = LeaseAwareFakeStore(renew_result=False)
    handler_started = asyncio.Event()
    handler_cancelled = asyncio.Event()

    async def handler(args: EchoInput, context: ToolCallContext) -> Mapping[str, Any]:
        handler_started.set()
        try:
            await asyncio.Future[None]()
        except asyncio.CancelledError:
            handler_cancelled.set()
            raise
        return {"echoed": args.query}

    outcome = await asyncio.wait_for(
        _boundary(
            boundary_dependencies,
            replace(echo_spec, timeout_seconds=0.2),
            store,
            handler,
        ).invoke(**invoke_kwargs(idempotency_key="renewal-failure")),
        timeout=1,
    )

    assert handler_started.is_set()
    assert handler_cancelled.is_set()
    assert outcome.status is ToolOutcomeStatus.FAILED
    assert outcome.invocation.output is None
    assert outcome.invocation.error_code == "tool_error"
    assert store.invocations[-1].status is ToolInvocationStatus.FAILED
    assert store.invocations[-1].lease_owner_id is None
    assert store.invocations[-1].lease_expires_at is None
